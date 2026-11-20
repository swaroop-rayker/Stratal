# pyright: basic, reportMissingImports=false
"""RUN-1.1 and BUILD-1.3 for tables and texts (ADR-0019 rules 12 to 14).

A built network trains on a table's split (classification, and regression with
a one-wide head and MSE) and on a text's tokenize version, through the same
train stage as images; the builder's locked ends come from the data. The runs
need PyTorch, so they are marked ``torch`` and run in the GPU worker's image;
the API checks run anywhere.
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


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    path = ["input", *ids, "output"]
    return GraphSpec(label=label, nodes=nodes, edges=list(itertools.pairwise(path)))


MLP = _chain("tab_mlp", L.Linear(out_features=16), L.ReLU())
LSTM = _chain("text_lstm", L.Embedding(embedding_dim=16, padding_idx=0), L.LSTM(hidden_size=16))


def _table(runner: Runner, root: Path, slug: str, *, regression: bool) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,y"]
    for i in range(120):
        size = float(i % 20)
        colour = ("red", "green", "blue")[i % 3]
        y = (
            f"{2 * size + (5 if colour == 'red' else 0):.1f}"
            if regression
            else ("big" if size > 9 else "small")
        )
        lines.append(f"{size},{colour},{y}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    job = PipelineApi(runner).run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "y"}}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _texts(runner: Runner, root: Path, slug: str) -> str:
    root.mkdir(parents=True)
    rows = []
    for i in range(90):
        label = ("cat", "dog", "bird")[i % 3]
        rows.append(json.dumps({"text": f"a {label} number {i} sits here", "label": label}))
    (root / "t.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    job = PipelineApi(runner).run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "labelled_text",
            "stages": {"tokenize": {"min_freq": 1}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _validate(api: PipelineApi, version: str, graph: GraphSpec) -> dict[str, Any]:
    response = api.client.post(
        "/api/v1/model-specs/validate",
        json={"graph": graph.model_dump(mode="json"), "dataset_version": version},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_BUILD_1_3_a_table_locks_its_encoded_width_and_its_outputs(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    classes = _validate(api, _table(runner, tmp_path / "c", "m10ends", regression=False), MLP)
    numbers = _validate(api, _table(runner, tmp_path / "r", "m10endsr", regression=True), MLP)

    # One numeric column, and three colours plus one unknown slot: width 5.
    assert classes["dataset"]["input"] == {"kind": "vector", "dims": [5], "vocab": None}
    assert classes["dataset"]["output_size"] == 2 and classes["report"]["valid"]
    assert numbers["dataset"]["output_size"] == 1, "regression: one output"


def test_BUILD_1_3_a_text_locks_its_length_and_vocabulary(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    version = _texts(runner, tmp_path / "t", "m10endst")

    body = _validate(api, version, LSTM)

    dataset = body["dataset"]
    assert dataset["input"]["kind"] == "tokens" and dataset["input"]["vocab"] > 10
    assert dataset["output_size"] == 3 and body["report"]["valid"]


def test_RUN_1_1_reference_models_and_untrainable_versions_are_refused(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _table(runner, tmp_path / "c", "m10ref", regression=False)

    response = api.client.post(
        "/api/v1/jobs",
        json={"kind": "train", "dataset_version": version, "config": {"model": {"name": "mlp"}}},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["config", "model", "name"]


def _train(api: PipelineApi, version: str, graph: GraphSpec, **config: Any) -> dict[str, Any]:
    response = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {
                "model": {"name": "graph", "graph": graph.model_dump(mode="json")},
                "epochs": 30,
                "batch_size": 16,
                "learning_rate": 0.01,
                "device": "cpu",
                **config,
            },
        },
    )
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    api.runner.run_queued()
    api.runner.collect()
    return api.runner.job(response.json()["id"])


@pytest.mark.torch
def test_RUN_1_1_a_network_trains_on_a_table_and_keeps_its_preprocessing(
    runner: Runner, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    from sqlalchemy.orm import Session

    from stratal.db.engine import get_engine
    from stratal.registry import store
    from stratal.train.table_prep import PREPROCESS_FILE, TablePrep

    api = PipelineApi(runner)
    job = _train(api, _table(runner, tmp_path / "c", "m10net", regression=False), MLP)

    assert job["state"] == "completed", job
    metrics = job["stages"][0]["metrics"]
    assert metrics["task"] == "classification"
    assert metrics["val_accuracy"] >= 0.8 and "test_macro_f1" in metrics
    with Session(get_engine()) as session:
        version = store.get_version(session, metrics["model_version_id"])
        folder = get_settings().data_dir / str(version.weights_path)
        assert version.metrics is not None and version.metrics["detail"]["test"]["labels"]
    prep = TablePrep.from_json((folder / PREPROCESS_FILE).read_text(encoding="utf-8"))
    assert prep.width == 5 and prep.classes == ("big", "small")
    meta = json.loads((folder / "model.json").read_text(encoding="utf-8"))
    assert meta["input"] == {"kind": "vector", "dims": [5], "vocab": None}


@pytest.mark.torch
def test_RUN_1_1_regression_trains_with_mse_and_reports_on_the_target_scale(
    runner: Runner, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    api = PipelineApi(runner)
    job = _train(api, _table(runner, tmp_path / "r", "m10reg", regression=True), MLP, epochs=60)

    assert job["state"] == "completed", job
    metrics = job["stages"][0]["metrics"]
    assert metrics["task"] == "regression" and "val_accuracy" not in metrics
    assert metrics["val_r2"] > 0.8, metrics
    assert metrics["test_rmse"] < 5.0, "y spans 0 to 43: an RMSE on its own scale"


@pytest.mark.torch
def test_RUN_1_1_an_lstm_trains_on_a_tokenize_version(runner: Runner, tmp_path: Path) -> None:
    pytest.importorskip("torch")
    api = PipelineApi(runner)
    job = _train(api, _texts(runner, tmp_path / "t", "m10txt"), LSTM)

    assert job["state"] == "completed", job
    metrics = job["stages"][0]["metrics"]
    assert metrics["val_accuracy"] >= 0.9, "the class word is in every text"
