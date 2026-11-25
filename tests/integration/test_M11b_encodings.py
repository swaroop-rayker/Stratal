# pyright: basic, reportMissingImports=false
"""DATA-7.3 end to end: an embedding column set by a transform reaches a trained network.

ADR-0019 Addendum B rule 29 and its *As built*: the transform sets ``colour`` to
``embedding``; the builder locks the input block's width with the embedding's
features; a network trains through the input layer and keeps its preprocessing.
The run needs PyTorch, so it is marked ``torch`` (worker-gpu image, on the CPU).
"""

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from stratal.build import layers as L
from stratal.build.spec import GraphSpec, Node
from stratal.core.settings import get_settings
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]

MLP = GraphSpec(
    label="tab_mlp",
    nodes=[Node(id="n0", layer=L.Linear(out_features=16)), Node(id="n1", layer=L.ReLU())],
    edges=list(itertools.pairwise(["input", "n0", "n1", "output"])),
)


def _split(runner: Runner, root: Path, slug: str) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,y"]
    for i in range(120):
        size = float(i % 20)
        lines.append(f"{size},{('red', 'green', 'blue')[i % 3]},{'big' if size > 9 else 'small'}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    api = PipelineApi(runner)
    raw = api.run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "y"}}},
            "until_stage": "ingest",
        }
    )
    ops = [{"op": "set_encoding", "column": "colour", "encoding": "embedding"}]
    job = api.run(
        {"from_version": raw["output_version_id"], "stages": {"transform": {"operations": ops}}}
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def test_DATA_7_3_the_builder_locks_the_width_with_the_embedding(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split(runner, tmp_path / "c", "m11bembw")

    response = api.client.post(
        "/api/v1/model-specs/validate",
        json={"graph": MLP.model_dump(mode="json"), "dataset_version": version},
    )

    assert response.status_code == 200, response.text
    # size, then colour's embedding: min(50, ceil(3 / 2)) = 2 features (one-hot would be 4).
    assert response.json()["dataset"]["input"] == {"kind": "vector", "dims": [3], "vocab": None}


@pytest.mark.torch
def test_DATA_7_3_a_network_trains_through_the_embedding(runner: Runner, tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from safetensors import safe_open
    from sqlalchemy.orm import Session

    from stratal.db.engine import get_engine
    from stratal.registry import store
    from stratal.train.table_prep import PREPROCESS_FILE, TablePrep

    api = PipelineApi(runner)
    version = _split(runner, tmp_path / "c", "m11bembt")
    response = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {
                "model": {"name": "graph", "graph": MLP.model_dump(mode="json")},
                "epochs": 30,
                "batch_size": 16,
                "learning_rate": 0.01,
                "device": "cpu",
            },
        },
    )
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    api.runner.run_queued()
    api.runner.collect()
    job: dict[str, Any] = api.runner.job(response.json()["id"])

    assert job["state"] == "completed", job
    metrics = job["stages"][0]["metrics"]
    assert metrics["val_accuracy"] >= 0.8, metrics
    with Session(get_engine()) as session:
        model = store.get_version(session, metrics["model_version_id"])
        folder = get_settings().data_dir / str(model.weights_path)
    prep = TablePrep.from_json((folder / PREPROCESS_FILE).read_text(encoding="utf-8"))
    assert [c.name for c in prep.embedded] == ["colour"] and prep.width == 3
    with safe_open(str(folder / store.WEIGHTS_FILE), framework="pt") as weights:
        keys = set(weights.keys())
    assert "embeddings.0.weight" in keys and any(k.startswith("body.") for k in keys)
    meta = json.loads((folder / "model.json").read_text(encoding="utf-8"))
    assert meta["table_schema"]["columns"][1]["encoding"] == "embedding"
