# pyright: basic, reportMissingImports=false
"""HPO-1.2 to HPO-1.4 for networks, end to end (ADR-0018 rules 5 to 8; markers ``torch``).

Each trial is an ``hpo_trial`` job that trains a small table network on the CPU; the
study's ``study`` stage runs once per trial in a short step, then ``test_eval`` tests
the registered best once. Runs in the GPU worker's image, which has PyTorch and Optuna.
"""

import itertools
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from stratal.build import layers as L
from stratal.build.spec import GraphSpec, Node
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Checkpoint, Job, ModelVersion, Study
from stratal.hpo import control
from stratal.hpo import store as hpo_store
from stratal.hpo.config import StudyRequest
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.runtime.supervisor import run_stage
from stratal.testing.harness import Runner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.torch,
    pytest.mark.usefixtures("fake_units"),
]
pytest.importorskip("torch")
pytest.importorskip("optuna")


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    path = ["input", *ids, "output"]
    return GraphSpec(label=label, nodes=nodes, edges=list(itertools.pairwise(path)))


MLP = _chain("tab_mlp", L.Linear(out_features=16), L.ReLU())


def _run(runner: Runner, request: Any) -> dict[str, Any]:
    with Session(get_engine()) as session, session.begin():
        if isinstance(request, StudyRequest):
            job, first = job_service.create_study_job(session, request)
        else:
            job, first = job_service.create_pipeline_job(session, request)
        job_id = job.id
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    return runner.job(job_id)


def _split(runner: Runner, root: Path, slug: str) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,y"]
    for i in range(120):
        size = float(i % 20) + i / 1000
        colour = ("red", "green", "blue")[i % 3]
        lines.append(f"{size},{colour},{'big' if size > 9 else 'small'}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    job = _run(
        runner,
        PipelineRequest.model_validate(
            {
                "kind": "pipeline",
                "dataset": slug,
                "source": {"path": str(root)},
                "dataset_type": "tabular",
                "stages": {"ingest": {"table": {"target": "y"}}},
            }
        ),
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


SPACE = [
    {"path": "/config/learning_rate", "type": "log_float", "low": 1e-3, "high": 1e-1},
    {
        "path": "/config/model/graph/nodes/n0/layer/out_features",
        "type": "int",
        "low": 4,
        "high": 32,
    },
]


def _study(version: str, space: list[dict[str, Any]], **fields: Any) -> StudyRequest:
    config = {
        "model": {"name": "graph", "graph": MLP.model_dump(mode="json")},
        "epochs": 3,
        "batch_size": 16,
        "device": "cpu",
        **fields.pop("config", {}),
    }
    return StudyRequest.model_validate(
        {
            "kind": "hpo_study",
            "dataset_version": version,
            "config": config,
            "space": space,
            **fields,
        }
    )


def _trial_jobs(study_id: str) -> list[Job]:
    with Session(get_engine()) as session:
        jobs = session.scalars(
            select(Job).where(Job.kind == "hpo_trial").order_by(Job.created_at)
        ).all()
        found = [j for j in jobs if j.spec.get("study_id") == study_id]
        for job in found:
            session.expunge(job)
        return found


def _study_of(job: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with Session(get_engine()) as session:
        study = session.query(Study).filter_by(job_id=job["id"]).one()
        return hpo_store.study_view(session, study), hpo_store.list_trials(session, study.id)


def test_HPO_1_2_a_network_study_runs_each_trial_as_a_job_and_registers_only_its_best(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10deep")

    job = _run(runner, _study(version, SPACE, n_trials=4))

    assert job["state"] == "completed", job
    assert [s["status"] for s in job["stages"]] == ["completed", "completed"]
    study, trials = _study_of(job)
    assert (study["state"], study["ended_by"]) == ("completed", "n_trials")
    assert study["trials"]["started"] == 4 and study["elapsed_s"] > 0
    trial_jobs = _trial_jobs(study["id"])
    assert [j.state for j in trial_jobs] == ["completed"] * 4
    assert [t["job_id"] for t in trials] == [j.id for j in trial_jobs]
    assert all(len(t["intermediate"]) == 3 for t in trials), "one value per epoch"
    with Session(get_engine()) as session:
        registered = session.scalars(
            select(ModelVersion).where(ModelVersion.job_id.in_([j.id for j in trial_jobs]))
        ).all()
        states = sorted(v.state for v in registered)
        best = session.get(ModelVersion, study["best_version_id"])
        assert best is not None and best.study_id == study["id"]
        assert best.job_id == trials[study["best"]["trial"]]["job_id"]
        assert best.metrics is not None and "test_accuracy" in best.metrics
        assert best.metrics["detail"]["test"]["rows"] > 0
        left = session.scalars(
            select(Checkpoint).where(Checkpoint.job_id.in_([j.id for j in trial_jobs]))
        ).all()
    assert states == ["base", "trained"], "only the best trial reaches the registry"
    assert left == [], "trial checkpoints are removed"
    data_dir = get_settings().data_dir
    assert not any(control.trial_folder(data_dir, j.id).exists() for j in trial_jobs)
    assert (data_dir / str(best.weights_path) / "model.json").is_file()
    lr = best.config["learning_rate"]
    assert lr == study["best"]["params"]["/config/learning_rate"]


def test_HPO_1_4_a_network_study_tests_its_best_once_and_its_trials_never(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10deeptest")

    job = _run(runner, _study(version, SPACE[:1], n_trials=2))

    study, _ = _study_of(job)
    assert study["test_evaluated_at"] is not None
    assert "test_accuracy" in study["test_metrics"]
    for trial in _trial_jobs(study["id"]):
        with Session(get_engine()) as session:
            [stage] = job_service.stages_of(session, trial.id)
            metrics = dict(stage.metrics or {})
        assert "val_accuracy" in metrics and not any(k.startswith("test_") for k in metrics)
    assert not hpo_store.record_test_once(get_engine(), study["id"], {"test_accuracy": 0.0})
    again, _ = _study_of(job)
    assert again["test_metrics"] == study["test_metrics"], "a second evaluation is refused"


def test_HPO_1_3_network_trials_are_pruned_at_an_epoch_behind_the_median(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10deepprune")
    space = [{"path": "/config/learning_rate", "type": "log_float", "low": 1e-5, "high": 0.3}]

    job = _run(
        runner,
        _study(version, space, n_trials=11, objective="val_loss", seed=4, config={"epochs": 4}),
    )

    assert job["state"] == "completed", job
    _, trials = _study_of(job)
    pruned = [t for t in trials if t["state"] == "pruned"]
    assert pruned, [(t["number"], t["state"], t["value"]) for t in trials]
    for trial in pruned:
        assert trial["prune_reason"] == "median" and 2 <= trial["step"] < 4
        assert trial["number"] >= 5, "no pruning before 5 trials have finished"
        assert len(trial["intermediate"]) == trial["step"]


def test_HPO_1_2_a_network_study_stops_at_its_time_budget(runner: Runner, tmp_path: Path) -> None:
    version = _split(runner, tmp_path / "s", "m10deepbudget")
    request = _study(version, SPACE, n_trials=50, timeout_s=60)
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_study_job(session, request)
        job_id, study_id = job.id, job.spec["study_id"]
    assert run_stage(first.job_id, first.idx, enqueue=runner.queue.append) == "waiting"
    assert [item.job_id for item in runner.queue] != [job_id], "trial 0 is queued"
    with Session(get_engine()) as session, session.begin():
        # As if the budget were all but spent: trial 0's first epoch spends the rest.
        session.execute(update(Study).where(Study.id == study_id).values(elapsed_s=59.999))
    runner.run_queued()
    runner.collect()

    final = runner.job(job_id)
    assert final["state"] == "completed", final
    study, trials = _study_of(final)
    assert study["ended_by"] == "timeout" and study["elapsed_s"] >= 60
    assert [(t["state"], t["prune_reason"], t["step"]) for t in trials] == [("pruned", "budget", 1)]
    assert study["best_version_id"] is None and study["test_metrics"] is None
    assert study["state"] == "completed"
