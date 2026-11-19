"""RUN-1.1: training requests at ``POST /jobs`` (``kind: train``), without PyTorch.

What a request becomes (a one-stage ``train`` job on the right queue, with its
config recorded) and what is refused. Running the stage needs PyTorch and is
tested in ``test_M8_training.py``, in the GPU worker's image.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stratal.api.app import create_app
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.testing.harness import Runner
from stratal.testing.labelled import labelled_split
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _train(version: str, **config: Any) -> dict[str, Any]:
    return {
        "kind": "train",
        "dataset_version": version,
        "config": {"model": {"name": "mlp"}, **config},
    }


def test_RUN_1_1_a_train_request_becomes_a_one_stage_job_on_the_right_queue(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8req")
    api = PipelineApi(runner)

    gpu = api.client.post("/api/v1/jobs", json=_train(version, epochs=3))
    cpu = api.client.post("/api/v1/jobs", json=_train(version, device="cpu"))

    assert gpu.status_code == 202, gpu.text
    job = gpu.json()
    assert (job["kind"], job["state"], job["dataset"]) == ("train", "queued", "m8req")
    [stage] = job["stages"]
    assert (stage["name"], stage["queue"], stage["input_version_id"]) == ("train", "gpu", version)
    assert stage["config"]["epochs"] == 3
    assert stage["config"]["model"] == {"name": "mlp"}
    assert stage["config"]["checkpoint_every_s"] == 300, "OC-3: at least every 5 minutes"
    assert cpu.status_code == 202, cpu.text
    assert cpu.json()["stages"][0]["queue"] == "cpu"
    assert [(q.job_id, q.queue) for q in api.queued] == [
        (job["id"], "gpu"),
        (cpu.json()["id"], "cpu"),
    ]


def test_RUN_1_1_training_needs_a_split_of_a_labelled_dataset(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8bad")
    api = PipelineApi(runner)
    with Session(get_engine()) as session:
        preprocess = registry.lineage(session, version)[1].version_id  # labelled, not a split

    cases = [
        (_train("ds_000000000000"), ["dataset_version"]),
        (_train(preprocess), ["dataset_version"]),
        (_train(version, epochs=0), None),
        (_train(version, model_name="x"), None),
        ({"kind": "train", "dataset_version": version, "config": {"model": {"name": "vgg"}}}, None),
    ]
    for body, loc in cases:
        response = api.client.post("/api/v1/jobs", json=body)
        assert response.status_code == 422, (body, response.text)
        problem = response.json()
        assert problem["code"] == "validation_error", problem
        if loc is not None:
            assert problem["errors"][0]["loc"] == loc, problem
    assert api.queued == []


def test_RUN_1_1_the_train_stage_has_a_schema_but_is_not_a_pipeline_stage() -> None:
    client = TestClient(create_app())

    listed = [s["name"] for s in client.get("/api/v1/config/schemas").json()["items"]]
    schema = client.get("/api/v1/config/schemas/train")

    assert "train" not in listed
    assert "ingest" in listed
    assert schema.status_code == 200
    assert {"model", "epochs", "batch_size", "device", "seed"} <= set(schema.json()["properties"])
