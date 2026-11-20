"""ML-1.1, ML-1.4 and ML-1.5 through the API and the job runtime, against the real PostgreSQL.

A classical request (``kind: train`` with ``classical``) becomes a one-stage
``train_classical`` job on the CPU queue, refused before queueing if the
estimator does not suit the data (ML-1.4). Running one needs scikit-learn:
``test_M10_classical_run.py``, in the worker-cpu image.
"""

from pathlib import Path
from typing import Any

import pytest

from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _tabular_split(runner: Runner, root: Path, slug: str, *, regression: bool = False) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,score,label"]
    for i in range(80):
        colour = ("red", "green", "blue")[i % 3]
        size = 1.0 + (i * 13) % 17
        target = (
            f"{size * 1.5 + (2 if colour == 'red' else 0):.2f}"
            if regression
            else ("big" if size > 9 else "small")
        )
        lines.append(f"{size},{colour},{(i * 7) % 11},{target}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    job = PipelineApi(runner).run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "label"}}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _classical(version: str, estimator: str, **params: Any) -> dict[str, Any]:
    return {
        "kind": "train",
        "dataset_version": version,
        "classical": {"model": {"kind": "sklearn", "estimator": estimator, "params": params}},
    }


def test_ML_1_1_a_classical_request_becomes_a_cpu_job_with_its_resolved_params(
    runner: Runner, tmp_path: Path
) -> None:
    version = _tabular_split(runner, tmp_path / "src", "m10cls")
    api = PipelineApi(runner)

    response = api.client.post(
        "/api/v1/jobs", json=_classical(version, "random_forest", max_depth=4)
    )

    assert response.status_code == 202, response.text
    [stage] = response.json()["stages"]
    assert (stage["name"], stage["queue"]) == ("train_classical", "cpu")
    params = stage["config"]["model"]["params"]
    assert params["max_depth"] == 4 and params["n_estimators"] == 100, "defaults resolved (NFR-4)"


@pytest.mark.parametrize(
    ("estimator", "params", "loc", "match"),
    [
        ("logistic_regression", {"penalty": "l1"}, ["classical", "model"], "liblinear or saga"),
        ("linear", {}, ["classical", "model", "estimator"], "for regression"),
        ("knn", {"n_neighbors": 500}, ["classical", "model", "params", "n_neighbors"], "train has"),
    ],
)
def test_ML_1_4_invalid_combinations_are_refused_before_the_run(
    runner: Runner,
    tmp_path: Path,
    estimator: str,
    params: dict[str, Any],
    loc: list[str],
    match: str,
) -> None:
    version = _tabular_split(runner, tmp_path / "src", "m10bad")

    response = PipelineApi(runner).client.post(
        "/api/v1/jobs", json=_classical(version, estimator, **params)
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    # Request parsing reports ["body", "train", ...]; the checks against the data report the
    # field's own path. Either way the error names the field.
    where = error["loc"][2:] if error["loc"][:2] == ["body", "train"] else error["loc"]
    assert where[: len(loc)] == loc
    assert match in error["msg"]


def test_ML_1_1_a_classical_model_needs_a_tabular_split(runner: Runner, tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "t.csv").write_text("x,y\n1,a\n2,b\n", encoding="utf-8")
    job = PipelineApi(runner).run(
        {
            "dataset": "m10notsplit",
            "source": {"path": str(root)},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "y"}}},
            "until_stage": "ingest",
        }
    )

    response = PipelineApi(runner).client.post(
        "/api/v1/jobs", json=_classical(job["output_version_id"], "random_forest")
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["dataset_version"]
