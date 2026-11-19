# pyright: basic, reportMissingImports=false
"""BUILD-1.1, BUILD-1.9, REG-1.1: a graph spec trains and registers like a reference model.

ADR-0017 rules 1, 2 and 8: the train stage compiles the graph through the same
factory, the lineage is named after the graph's label but keyed without it, and
the registered weights load into the spec rebuilt from ``model.json``. Needs
PyTorch, so it runs in the GPU worker's image.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

torch = pytest.importorskip("torch")

from stratal.build import layers as L  # noqa: E402
from stratal.build.spec import GraphSpec, Node  # noqa: E402
from stratal.core.settings import get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402
from stratal.testing.labelled import labelled_split  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402
from stratal.train import weights  # noqa: E402
from stratal.train.config import ModelSpec  # noqa: E402
from stratal.train.reference import build_model  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

RESIDUAL = GraphSpec(
    label="tiny_resnet",
    nodes=[
        Node(id="stem", layer=L.Conv2d(out_channels=8)),
        Node(id="a", layer=L.Conv2d(out_channels=8)),
        Node(id="relu", layer=L.ReLU()),
        Node(id="skip", layer=L.Add()),
        Node(id="pool", layer=L.GlobalAvgPool()),
    ],
    edges=[
        ("input", "stem"),
        ("stem", "a"),
        ("a", "relu"),
        ("relu", "skip"),
        ("stem", "skip"),
        ("skip", "pool"),
        ("pool", "output"),
    ],
)


def _train(api: PipelineApi, version: str, graph: GraphSpec) -> dict[str, Any]:
    response = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {
                "model": {"name": "graph", "graph": graph.model_dump(mode="json")},
                "epochs": 2,
                "batch_size": 8,
                "device": "cpu",
                "loader_workers": 0,
            },
        },
    )
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    api.runner.run_queued()
    api.runner.collect()
    return api.client.get(f"/api/v1/jobs/{response.json()['id']}").json()


def test_BUILD_1_9_a_residual_graph_trains_registers_and_reloads(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m9graph")
    api = PipelineApi(runner)

    job = _train(api, version, RESIDUAL)

    assert job["state"] == "completed", job
    lineage = api.client.get(f"/api/v1/models/{job['model_lineage_id']}").json()
    assert lineage["name"] == "tiny_resnet · m9graph"
    assert "label" not in lineage["model"]["graph"], "the label is not part of the key"
    with get_engine().connect() as connection:
        folder = connection.execute(
            text("SELECT weights_path FROM model_versions WHERE id = :v"),
            {"v": job["model_version_id"]},
        ).scalar_one()
    path = get_settings().data_dir / folder
    meta = json.loads((path / "model.json").read_text(encoding="utf-8"))
    model = build_model(ModelSpec.model_validate(meta["model"]), tuple(meta["input_shape"]), 2)
    weights.load_into(model, path)  # strict: every parameter name matches
    assert "nodes.skip" not in {n for n, _ in model.named_parameters()}, "add has no weights"
    assert sum(p.numel() for p in model.parameters()) == job["stages"][0]["metrics"]["parameters"]


def test_BUILD_1_1_renaming_a_graph_keeps_its_lineage(runner: Runner, tmp_path: Path) -> None:
    version = labelled_split(runner, tmp_path / "src", "m9rename")
    api = PipelineApi(runner)

    first = _train(api, version, RESIDUAL)
    second = _train(api, version, RESIDUAL.model_copy(update={"label": "renamed"}))

    assert first["state"] == second["state"] == "completed"
    assert first["model_lineage_id"] == second["model_lineage_id"]
