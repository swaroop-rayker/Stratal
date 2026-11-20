"""HPO-1.1, HPO-1.4 and HPO-1.5 at the API: a study request becomes an ``hpo_study`` job and
a ``studies`` row, or is refused naming the marker; the study endpoints answer (ADR-0018).
Runs anywhere: nothing here fits a model (``test_M10_studies_run.py`` does, in worker-cpu)."""

from pathlib import Path
from typing import Any

import pytest

from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


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


def _study(version: str, **fields: Any) -> dict[str, Any]:
    return {
        "kind": "hpo_study",
        "dataset_version": version,
        "classical": {"model": {"kind": "sklearn", "estimator": "random_forest"}},
        "space": [
            {"path": "/classical/model/params/max_depth", "type": "int", "low": 2, "high": 8}
        ],
        **fields,
    }


def test_HPO_1_1_a_study_request_becomes_a_study_job_and_row(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10sreq")

    response = api.client.post("/api/v1/jobs", json=_study(version, n_trials=7))

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["kind"] == "hpo_study"
    [stage] = job["stages"]
    assert (stage["name"], stage["queue"]) == ("study", "cpu")
    study_id = stage["config"]["study_id"]
    study = api.client.get(f"/api/v1/studies/{study_id}").json()
    assert (study["state"], study["objective"], study["direction"]) == (
        "running",
        "cv_accuracy",
        "maximize",
    )
    assert study["limits"] == {"n_trials": 7, "timeout_s": 3600}
    assert study["trials"]["started"] == 0 and study["cv_folds"] == 5
    assert study["base"]["classical"]["model"]["params"]["n_estimators"] == 100, "resolved"
    assert api.client.get(f"/api/v1/studies/{study_id}/trials").json() == {"items": []}
    assert api.client.get(f"/api/v1/studies/{study_id}/best-config").status_code == 404
    listed = api.client.get("/api/v1/studies").json()["items"]
    assert study_id in [s["id"] for s in listed]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        (
            {
                "space": [
                    {
                        "path": "/classical/model/params/n_estimators",
                        "type": "int",
                        "low": 0,
                        "high": 5,
                    }
                ]
            },
            ["space", 0, "path"],
        ),
        ({"objective": "cv_r2"}, ["objective"]),
        (
            {"classical": {"model": {"kind": "sklearn", "estimator": "kmeans"}}},
            ["classical", "model", "estimator"],
        ),
    ],
)
def test_HPO_1_1_a_bad_space_objective_or_model_is_refused_before_the_study(
    runner: Runner, tmp_path: Path, change: dict[str, Any], loc: list[Any]
) -> None:
    api = PipelineApi(runner)
    version = _split(api, tmp_path / "s", "m10sbad")

    response = api.client.post("/api/v1/jobs", json={**_study(version), **change})

    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == loc


def test_HPO_1_5_an_unknown_study_is_404(runner: Runner) -> None:
    response = PipelineApi(runner).client.get("/api/v1/studies/st_000000000000")

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
