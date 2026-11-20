"""ML-1.1 and ML-1.5: a classical run, end to end, against the real PostgreSQL (marker ``ml``).

Runs in the worker-cpu image, which has scikit-learn and skops but not the API's
test client (httpx), so jobs are created through the job service, not over HTTP,
and the owner's split units run for real. check.ps1, step "integration (ml)".
"""
# pyright: reportMissingImports=false

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.registry import store
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.testing.harness import Runner
from stratal.train.config import TrainRequest

pytestmark = [pytest.mark.integration, pytest.mark.ml]
pytest.importorskip("sklearn")
pytest.importorskip("skops")


def _run(runner: Runner, request: PipelineRequest | TrainRequest) -> dict[str, Any]:
    with Session(get_engine()) as session, session.begin():
        if isinstance(request, TrainRequest):
            job, first = job_service.create_train_job(session, request)
        else:
            job, first = job_service.create_pipeline_job(session, request)
        job_id = job.id
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    return runner.job(job_id)


def _tabular_split(runner: Runner, root: Path, slug: str) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,score,label"]
    for i in range(80):
        size = 1.0 + (i * 13) % 17
        colour = ("red", "green", "blue")[i % 3]
        lines.append(f"{size},{colour},{(i * 7) % 11},{'big' if size > 9 else 'small'}")
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


def test_ML_1_5_a_classical_run_registers_a_skops_model_with_its_test_metrics(
    runner: Runner, tmp_path: Path
) -> None:
    from stratal.ml import persist
    from stratal.ml.data import records_table
    from stratal.testing.no_pickle import forbid_unpickling

    version = _tabular_split(runner, tmp_path / "src", "m10run")
    request = TrainRequest.model_validate(
        {
            "kind": "train",
            "dataset_version": version,
            "classical": {
                "model": {
                    "kind": "sklearn",
                    "estimator": "random_forest",
                    "params": {"n_estimators": 20},
                }
            },
        }
    )

    job = _run(runner, request)

    assert job["state"] == "completed", job
    metrics: dict[str, Any] = job["stages"][0]["metrics"]
    assert metrics["task"] == "classification"
    assert 0.5 <= metrics["val_accuracy"] <= 1.0 and "test_macro_f1" in metrics
    version_id = str(metrics["model_version_id"])
    with Session(get_engine()) as session:
        model = store.get_version(session, version_id)
        lineage = store.get_lineage(session, model.lineage_id)
        base = store.base_of(session, lineage.id)
        assert (model.state, model.weights_status, lineage.task) == (
            "trained",
            "present",
            "classification",
        )
        assert lineage.model_spec == {"kind": "sklearn", "estimator": "random_forest"}
        assert base is not None and base.id != version_id, "the unfitted pipeline is the base"
        assert model.metrics is not None
        assert model.metrics["detail"]["test"]["labels"] == ["big", "small"]
        folder = get_settings().data_dir / str(model.weights_path)
    with forbid_unpickling() as attempts:
        loaded = persist.load(folder / persist.MODEL_FILE)
    assert attempts == []
    sample = records_table(
        [{"size": 15.0, "colour": "red", "score": 3.0}], ["size", "score"], ["colour"]
    )
    assert list(loaded.predict(sample)) == ["big"]

    again = _run(runner, request)
    assert again["state"] == "completed"
    with Session(get_engine()) as session:
        second = store.run_version(session, str(again["id"]))
        assert second is not None and second.id != version_id
        assert second.lineage_id == lineage.id, "a second run joins the same lineage"
        assert second.parent_version_id == base.id
