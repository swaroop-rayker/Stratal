"""HPO-1.1 and HPO-1.2 for networks at the API and in the runtime (ADR-0018 rules 2 and 5).

A study of a network is an ``hpo_study`` job whose ``study`` stage runs in short steps
and waits between them while an ``hpo_trial`` job trains. Nothing here trains: the
trial jobs are put in the states a real one passes through, so the coordination can
be checked in dev (``test_M10_deep_studies_run.py`` runs whole studies, with PyTorch).
"""

import itertools
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.build import layers as L
from stratal.build.io import vram_problem
from stratal.build.spec import GraphSpec, Node
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import DatasetVersion, Event, Job, JobStage, Study, StudyTrial
from stratal.hpo import control
from stratal.hpo import store as hpo_store
from stratal.mon.events import utc_now
from stratal.mon.watchdog import Watchdog
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue
from stratal.runtime.states import transition
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.train.config import TrainConfig

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    path = ["input", *ids, "output"]
    return GraphSpec(label=label, nodes=nodes, edges=list(itertools.pairwise(path)))


MLP = _chain("tab_mlp", L.Linear(out_features=16), L.ReLU())


def _split(api: PipelineApi, root: Path, slug: str) -> str:
    root.mkdir(parents=True)
    rows = [f"{i % 9},{('red', 'blue')[i % 2]},{'yes' if i % 9 > 4 else 'no'}" for i in range(60)]
    (root / "t.csv").write_text("a,colour,label\n" + "\n".join(rows) + "\n", encoding="utf-8")
    job = api.run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "label"}}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _network(graph: GraphSpec = MLP, **config: Any) -> dict[str, Any]:
    return {
        "model": {"name": "graph", "graph": graph.model_dump(mode="json")},
        "epochs": 3,
        "batch_size": 16,
        "device": "cpu",
        **config,
    }


def _study(version: str, **fields: Any) -> dict[str, Any]:
    return {
        "kind": "hpo_study",
        "dataset_version": version,
        "config": _network(),
        "space": [
            {"path": "/config/learning_rate", "type": "log_float", "low": 1e-4, "high": 1e-1},
            {
                "path": "/config/model/graph/nodes/n0/layer/out_features",
                "type": "int",
                "low": 8,
                "high": 64,
            },
        ],
        **fields,
    }


def test_HPO_1_1_a_network_study_becomes_a_study_job_with_a_test_stage(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dreq")

    response = api.client.post("/api/v1/jobs", json=_study(version, n_trials=4))

    assert response.status_code == 202, response.text
    job = response.json()
    assert [(s["name"], s["queue"]) for s in job["stages"]] == [
        ("study", "cpu"),
        ("test_eval", "cpu"),
    ]
    study_id = job["stages"][0]["config"]["study_id"]
    assert job["stages"][1]["config"] == {"study_id": study_id}
    study = api.client.get(f"/api/v1/studies/{study_id}").json()
    assert (study["kind"], study["objective"], study["direction"]) == (
        "deep",
        "val_accuracy",
        "maximize",
    )
    assert study["cv_folds"] is None
    assert study["base"]["config"]["checkpoint_every_s"] > 0, "resolved as a train job would be"


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"objective": "cv_accuracy"}, ["objective"]),
        (
            {
                "space": [
                    {
                        "path": "/config/model/graph/nodes/n0/layer/out_features",
                        "type": "int",
                        "low": 8,
                        "high": 100_000,
                    }
                ]
            },
            ["space", 0, "path"],
        ),
        (
            {
                "space": [
                    {
                        "path": "/config/model/graph/nodes/zz/layer/out_features",
                        "type": "int",
                        "low": 8,
                        "high": 16,
                    }
                ]
            },
            ["space", 0, "path"],
        ),
    ],
)
def test_HPO_1_1_a_network_study_refuses_a_wrong_objective_or_marker(
    runner: Runner, tmp_path: Path, change: dict[str, Any], loc: list[Any]
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dbad")

    response = api.client.post("/api/v1/jobs", json={**_study(version), **change})

    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == loc


# ------------------------------------------------------------------ a study between steps


def _waiting(api: PipelineApi, version: str, *, trial_state: str) -> tuple[str, str, str]:
    """A study as its first step leaves it: the job running, its stage waiting, trial 0's
    job created and put in ``trial_state``. Returns the study job, the study and the trial."""
    response = api.client.post("/api/v1/jobs", json=_study(version))
    assert response.status_code == 202, response.text
    api.queued.clear()
    study_job = response.json()["id"]
    with Session(get_engine()) as session, session.begin():
        job = job_service.get_job(session, study_job)
        transition(session, job, "running", reason="claimed", actor="supervisor")
        job.attempt = 1
        job.heartbeat_at = utc_now() - timedelta(minutes=10)
        stage = session.get(JobStage, (study_job, 0))
        assert stage is not None
        stage.status = "waiting"
        study = control.study_of_job(session, study_job)
        assert study is not None
        study_id = study.id
        dataset_version = session.get(DatasetVersion, version)
        assert dataset_version is not None
        trial, _ = job_service.create_trial_job(
            session,
            study_job_id=study_job,
            study_id=study_id,
            number=0,
            version=dataset_version,
            config=TrainConfig.model_validate(study.base["config"]),
        )
        session.add(
            StudyTrial(study_id=study_id, number=0, state="running", params={}, job_id=trial.id)
        )
        if trial_state in ("running", "paused", "completed", "failed"):
            transition(session, trial, "running", reason="claimed", actor="supervisor")
            trial.attempt = 1
            trial.heartbeat_at = utc_now()
            trial_stage = session.get(JobStage, (trial.id, 0))
            assert trial_stage is not None
            trial_stage.status = "running"
            trial_stage.last_progress_at = utc_now()  # not silent: the stall check leaves it
        if trial_state in ("paused", "completed", "failed"):
            transition(session, trial, trial_state, reason="as a trial would", actor="supervisor")
        trial_id = trial.id
    return study_job, study_id, trial_id


def _job(job_id: str) -> Job:
    with Session(get_engine()) as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


def _stage(job_id: str) -> str:
    with Session(get_engine()) as session:
        stage = session.get(JobStage, (job_id, 0))
        assert stage is not None
        return stage.status


def _row(study_id: str) -> StudyTrial:
    with Session(get_engine()) as session:
        row = session.get(StudyTrial, (study_id, 0))
        assert row is not None
        session.expunge(row)
        return row


def test_HPO_1_2_a_trial_job_carries_its_study_and_number(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dtrial")

    study_job, study_id, trial_id = _waiting(api, version, trial_state="queued")

    trial = _job(trial_id)
    assert trial.kind == "hpo_trial"
    assert (trial.spec["study_id"], trial.spec["study_job"], trial.spec["trial"]) == (
        study_id,
        study_job,
        0,
    )
    with Session(get_engine()) as session:
        [stage] = job_service.stages_of(session, trial_id)
        assert (stage.name, stage.queue, stage.status) == ("train", "cpu", "queued")
        assert control.trial_to_enqueue(session, study_job) == Enqueue(trial_id, 0, "cpu")


def test_HPO_1_2_a_trial_end_queues_its_study_step_once(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dend")
    study_job, _, trial_id = _waiting(api, version, trial_state="completed")

    with Session(get_engine()) as session, session.begin():
        trial = job_service.get_job(session, trial_id)
        first = control.trial_ended(session, trial)
        again = control.trial_ended(session, trial)

    assert first == Enqueue(study_job, 0, "cpu")
    assert again is None, "a duplicate end changes nothing"
    assert _stage(study_job) == "queued" and _job(study_job).state == "running"


def test_HPO_1_2_pausing_a_study_pauses_its_running_trial_then_itself(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dpause")
    study_job, _, trial_id = _waiting(api, version, trial_state="running")

    response = api.client.post(f"/api/v1/jobs/{study_job}/pause")

    assert response.status_code == 202, response.text
    assert _job(trial_id).desired_state == "paused", "the pause reaches the trial"
    assert runner.bus.get_control(trial_id) == "pause"
    with Session(get_engine()) as session, session.begin():  # as the trial's supervisor would
        trial = job_service.get_job(session, trial_id)
        trial.desired_state = None
        transition(session, trial, "paused", reason="safe point", actor="supervisor")
        control.trial_paused(session, trial)
    assert (_job(study_job).state, _stage(study_job)) == ("paused", "waiting")

    resumed = api.client.post(f"/api/v1/jobs/{study_job}/resume")

    assert resumed.status_code == 202, resumed.text
    assert _stage(study_job) == "queued", "its step resumes the trial"
    assert api.queued == [Enqueue(study_job, 0, "cpu")]


def test_HPO_1_2_pausing_a_study_withdraws_a_trial_that_has_not_started(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dwith")
    study_job, study_id, trial_id = _waiting(api, version, trial_state="queued")

    response = api.client.post(f"/api/v1/jobs/{study_job}/pause")

    assert response.status_code == 202, response.text
    assert _job(study_job).state == "paused"
    assert _job(trial_id).state == "cancelled"
    row = _row(study_id)
    assert (row.state, row.code) == ("failed", "interrupted")
    with Session(get_engine()) as session:
        counts = hpo_store.counts(session, study_id)
    assert (counts["started"], counts["interrupted"]) == (0, 1), "it does not count"


@pytest.mark.parametrize("trial_state", ["running", "queued"])
def test_HPO_1_2_cancelling_a_waiting_study_cancels_it_and_its_trial(
    runner: Runner, tmp_path: Path, trial_state: str
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", f"m10dc{trial_state[0]}")
    study_job, study_id, trial_id = _waiting(api, version, trial_state=trial_state)
    folder = control.trial_folder(get_settings().data_dir, trial_id)
    (folder / "initial").mkdir(parents=True)
    (folder / "initial" / "model.safetensors").write_bytes(b"0" * 64)

    response = api.client.post(f"/api/v1/jobs/{study_job}/cancel")

    assert response.status_code == 202, response.text
    assert _job(study_job).state == "cancelled", "no step runs: it is cancelled at once"
    assert _stage(study_job) == "cancelled"
    with Session(get_engine()) as session:
        study = session.get(Study, study_id)
        assert study is not None and study.state == "cancelled"
        assert study.best_version_id is None and study.test_metrics is None
    trial = _job(trial_id)
    if trial_state == "running":
        assert trial.desired_state == "cancelled" and runner.bus.get_control(trial_id) == "cancel"
    else:
        assert trial.state == "cancelled"
    assert (_row(study_id).state, _row(study_id).code) == ("failed", "cancelled")
    assert not folder.exists(), "a cancelled study keeps no trial weights"


def test_HPO_1_2_the_watchdog_checks_a_waiting_study_through_its_trial(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dwatch")
    study_job, _, trial_id = _waiting(api, version, trial_state="running")
    sent: list[Enqueue] = []
    watchdog = Watchdog(get_engine(), enqueue=sent.append, temperature=lambda: None)

    watchdog.run_once()

    assert _job(study_job).state == "running", "an old heartbeat is not a lost worker here"
    assert sent == []
    with Session(get_engine()) as session, session.begin():  # the trial ends, its message lost
        trial = job_service.get_job(session, trial_id)
        transition(session, trial, "completed", reason="done", actor="supervisor")

    watchdog.run_once()

    assert sent == [Enqueue(study_job, 0, "cpu")]
    assert _stage(study_job) == "queued"
    with Session(get_engine()) as session:
        codes = session.scalars(select(Event.metrics).where(Event.job_id == study_job)).all()
    assert any((m or {}).get("code") == "study_step_requeued" for m in codes)
    watchdog.run_once()
    assert len(sent) == 1, "a step just queued is not sent again"


def test_HPO_1_2_a_network_too_big_for_the_gpu_is_refused_by_its_estimate(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10dvram")
    huge = _chain("huge", L.Linear(out_features=65_536), L.Linear(out_features=65_536))
    data_dir = get_settings().data_dir

    on_gpu = TrainConfig.model_validate(_network(huge, device="gpu"))
    small = TrainConfig.model_validate(_network(device="gpu"))
    on_cpu = TrainConfig.model_validate(_network(huge, device="cpu"))

    problem = vram_problem(get_engine(), data_dir, on_gpu, version)
    assert problem is not None and "the GPU allows" in problem
    assert vram_problem(get_engine(), data_dir, small, version) is None
    assert vram_problem(get_engine(), data_dir, on_cpu, version) is None, "the CPU has no cap"
