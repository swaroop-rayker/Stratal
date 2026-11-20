"""HPO-1.2 to HPO-1.5: classical studies, end to end, against the real PostgreSQL (marker ``ml``).

Optuna keeps its trials in schema ``optuna`` of the test database; ``studies`` and
``study_trials`` are what the API reads (ADR-0018 rule 3). Runs in the worker-cpu
image, which has scikit-learn and Optuna but not the API's test client, so studies
are created through the job service (check.ps1, step "integration (ml)").
"""
# pyright: basic, reportMissingImports=false

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine
from stratal.db.models import ModelVersion, Study
from stratal.hpo import store as hpo_store
from stratal.hpo.config import StudyRequest
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.testing.harness import Runner

pytestmark = [pytest.mark.integration, pytest.mark.ml]
pytest.importorskip("sklearn")
pytest.importorskip("optuna")


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
    lines = ["a,b,colour,label"]
    for i in range(150):
        a, b = (i * 37) % 100 / 10, (i * 53) % 70 / 10
        colour = ("red", "green", "blue")[i % 3]
        label = "yes" if a + (2 if colour == "red" else 0) > 5 else "no"
        lines.append(f"{a},{b},{colour},{label}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    job = _run(
        runner,
        PipelineRequest.model_validate(
            {
                "kind": "pipeline",
                "dataset": slug,
                "source": {"path": str(root)},
                "dataset_type": "tabular",
                "stages": {"ingest": {"table": {"target": "label"}}},
            }
        ),
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _study(
    version: str, estimator: str, space: list[dict[str, Any]], **fields: Any
) -> StudyRequest:
    return StudyRequest.model_validate(
        {
            "kind": "hpo_study",
            "dataset_version": version,
            "classical": {"model": {"kind": "sklearn", "estimator": estimator}},
            "space": space,
            "cv_folds": 3,
            **fields,
        }
    )


def _study_of(job: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with Session(get_engine()) as session:
        study = session.query(Study).filter_by(job_id=job["id"]).one()
        return hpo_store.study_view(session, study), hpo_store.list_trials(session, study.id)


FOREST_SPACE = [
    {"path": "/classical/model/params/n_estimators", "type": "int", "low": 5, "high": 40},
    {"path": "/classical/model/params/max_depth", "type": "int", "low": 1, "high": 8},
]


def test_HPO_1_2_a_study_stops_at_its_trial_limit_and_registers_its_best(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10study")

    job = _run(runner, _study(version, "random_forest", FOREST_SPACE, n_trials=6))

    assert job["state"] == "completed", job
    study, trials = _study_of(job)
    assert (study["state"], study["ended_by"]) == ("completed", "n_trials")
    assert study["trials"]["started"] == 6 and len(trials) == 6
    assert study["best"]["params"].keys() == {p["path"] for p in FOREST_SPACE}
    assert study["importance"]["method"] == "fanova"
    assert set(study["importance"]["values"]) == {p["path"] for p in FOREST_SPACE}
    with Session(get_engine()) as session:
        version_row = session.get(ModelVersion, study["best_version_id"])
        assert version_row is not None and version_row.study_id == study["id"]
        params = version_row.config["model"]["params"]
    assert params["n_estimators"] == study["best"]["params"]["/classical/model/params/n_estimators"]


def test_HPO_1_2_a_study_stops_at_its_time_budget(runner: Runner, tmp_path: Path) -> None:
    version = _split(runner, tmp_path / "s", "m10budget")
    request = _study(version, "random_forest", FOREST_SPACE, n_trials=200, timeout_s=60)
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_study_job(session, request)
        job_id, study_id = job.id, job.spec["study_id"]
        # As if 58 s had already run before a pause: the budget excludes paused time only.
        session.execute(update(Study).where(Study.id == study_id).values(elapsed_s=58.0))
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()

    study, _ = _study_of(runner.job(job_id))
    assert study["ended_by"] == "timeout"
    assert study["trials"]["started"] < 200
    assert study["elapsed_s"] >= 60.0


def test_HPO_1_3_pruned_trials_are_logged_with_the_step_they_stopped_at(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10prune")
    space = [{"path": "/classical/model/params/n_neighbors", "type": "int", "low": 1, "high": 90}]

    job = _run(runner, _study(version, "knn", space, n_trials=20, cv_folds=5, seed=3))

    _, trials = _study_of(job)
    pruned = [t for t in trials if t["state"] == "pruned"]
    assert pruned, [(t["state"], t["value"]) for t in trials]
    for trial in pruned:
        assert trial["prune_reason"] == "median" and 1 <= trial["step"] < 5
        assert len(trial["intermediate"]) == trial["step"], "each fold's running mean"
    assert all(t["number"] >= 5 for t in pruned), "no pruning before 5 trials have finished"


def test_HPO_1_4_test_metrics_are_computed_exactly_once(runner: Runner, tmp_path: Path) -> None:
    version = _split(runner, tmp_path / "s", "m10once")
    space = [
        {"path": "/classical/model/params/penalty", "type": "categorical", "choices": ["l1", "l2"]}
    ]

    job = _run(runner, _study(version, "logistic_regression", space, n_trials=6))

    study, trials = _study_of(job)
    assert study["test_evaluated_at"] is not None
    assert "test_accuracy" in study["test_metrics"] and study["test_metrics"]["detail"]["rows"] > 0
    assert {t["code"] for t in trials if t["state"] == "failed"} <= {"invalid_combination"}
    assert any(t["code"] == "invalid_combination" for t in trials), "l1 with lbfgs: a failed trial"
    assert not hpo_store.record_test_once(get_engine(), study["id"], {"test_accuracy": 0.0})
    again, _ = _study_of(job)
    assert again["test_metrics"] == study["test_metrics"], "a second evaluation is refused"
    assert again["importance"] == {"reason": "one field is tuned, so there is nothing to rank"}


def test_HPO_1_5_the_trial_table_best_config_and_importance_are_served(
    runner: Runner, tmp_path: Path
) -> None:
    version = _split(runner, tmp_path / "s", "m10views")
    job = _run(runner, _study(version, "random_forest", FOREST_SPACE, n_trials=5))
    study, trials = _study_of(job)

    with Session(get_engine()) as session:
        row = hpo_store.get_study(session, study["id"])
        best = hpo_store.best_config(row)

    assert [t["number"] for t in trials] == list(range(5))
    assert all(t["duration_s"] is not None for t in trials)
    depth = best["classical"]["model"]["params"]["max_depth"]
    assert depth == study["best"]["params"]["/classical/model/params/max_depth"]


def test_HPO_1_2_two_studies_starting_at_once_both_open_optunas_storage() -> None:
    """The first opening creates Optuna's tables and types; two at once used to race on
    ``CREATE TYPE studydirection`` and fail one study (found on the dev stack, 2026-11-20)."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    from stratal.hpo.runner import rdb_storage

    with get_engine().begin() as connection:  # the test database's schema, empty again
        connection.execute(text("DROP SCHEMA IF EXISTS optuna CASCADE"))
        connection.execute(text("CREATE SCHEMA optuna"))

    with ThreadPoolExecutor(max_workers=3) as pool:
        opened = list(pool.map(lambda _i: rdb_storage(), range(3)))

    assert len(opened) == 3
    assert opened[0].get_all_studies() == []
