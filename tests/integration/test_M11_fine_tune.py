# pyright: basic, reportMissingImports=false
"""RUN-1.10 and REG-2.8: fine-tuning a trained version, end to end.

ADR-0014 Addendum A (the run) and ADR-0015 Addendum A (its parent and retention).
Real pipelines make the splits, real training runs on the CPU make the parents,
and the API refuses or accepts each fine-tune. Needs PyTorch, so it runs in the
GPU worker's image.
"""

import itertools
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
from stratal.testing.synthetic import make_image_folder  # noqa: E402
from stratal.train import weights  # noqa: E402
from stratal.train.table_prep import PREPROCESS_FILE  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    return GraphSpec(
        label=label, nodes=nodes, edges=list(itertools.pairwise(["input", *ids, "output"]))
    )


CNN = _chain(
    "ft_cnn",
    L.Conv2d(out_channels=8),
    L.BatchNorm(),
    L.ReLU(),
    L.Conv2d(out_channels=8),
    L.GlobalAvgPool(),
)
"""n0 conv, n1 batch norm, n2 relu (no weights), n3 conv, n4 pool (no weights), then the head."""
MLP = _chain("ft_mlp", L.Linear(out_features=16), L.ReLU())
LSTM = _chain("ft_lstm", L.Embedding(embedding_dim=16, padding_idx=0), L.LSTM(hidden_size=16))


class Api:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.api = PipelineApi(runner)
        self.data = get_settings().data_dir

    def flush(self) -> None:
        """Run what the API queued, and what that queues (retention after a run)."""
        self.runner.queue.extend(self.api.queued)
        self.api.queued.clear()
        self.runner.run_queued()
        self.runner.collect()

    def post(self, path: str, body: Any, status: int) -> Any:
        response = self.api.client.post(f"/api/v1{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def get(self, path: str) -> Any:
        response = self.api.client.get(f"/api/v1{path}")
        assert response.status_code == 200, response.text
        return response.json()

    def body(self, version: str, graph: GraphSpec | None, **config: Any) -> dict[str, Any]:
        model = (
            {"name": "graph", "graph": graph.model_dump(mode="json")}
            if graph is not None
            else {"name": "mlp"}
        )
        return {
            "kind": "train",
            "dataset_version": version,
            "config": {
                "model": model,
                "epochs": 2,
                "batch_size": 8,
                "learning_rate": 0.01,
                "device": "cpu",
                "loader_workers": 0,
                **config,
            },
        }

    def start(self, version: str, graph: GraphSpec | None, **config: Any) -> str:
        return str(self.post("/jobs", self.body(version, graph, **config), 202)["id"])

    def train(self, version: str, graph: GraphSpec | None, **config: Any) -> dict[str, Any]:
        job_id = self.start(version, graph, **config)
        self.flush()
        job = self.get(f"/jobs/{job_id}")
        assert job["state"] == "completed", job
        return self.get(f"/model-versions/{job['model_version_id']}")

    def refused(self, version: str, graph: GraphSpec | None, **config: Any) -> dict[str, Any]:
        return self.post("/jobs", self.body(version, graph, **config), 422)

    def tensors(self, version_id: str) -> dict[str, Any]:
        with get_engine().connect() as connection:
            folder = connection.execute(
                text("SELECT weights_path FROM model_versions WHERE id = :v"), {"v": version_id}
            ).scalar_one()
        return weights.read_tensors(self.data / folder / "model.safetensors", suggested_action="")

    def history(self, version_id: str) -> list[dict[str, Any]]:
        with get_engine().connect() as connection:
            folder = connection.execute(
                text("SELECT weights_path FROM model_versions WHERE id = :v"), {"v": version_id}
            ).scalar_one()
        return json.loads((self.data / folder / "model.json").read_text(encoding="utf-8"))[
            "history"
        ]


def _three_classes(runner: Runner, root: Path, slug: str) -> str:
    for seed, label in enumerate(("cat", "dog", "fox"), start=1):
        make_image_folder(root / label, valid=14, size=32, seed=seed)
    job = PipelineApi(runner).run(
        {
            "dataset": slug,
            "source": {"path": str(root)},
            "dataset_type": "labelled_images",
            "stages": {"ingest": {"min_size": 32}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _codes(problem: dict[str, Any]) -> set[str]:
    return {e["code"] for e in problem.get("errors", [])}


# ---------------------------------------------------------------------------- AC1


def test_RUN_1_10_AC1_a_run_from_a_version_starts_at_its_weights_and_names_it_parent(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-ac1")
    parent = api.train(split, CNN)

    child = api.train(split, CNN, init_from=parent["id"], epochs=1)

    assert child["parent_version_id"] == parent["id"]
    assert child["lineage_id"] == parent["lineage_id"], "same spec, data and classes"
    first = api.history(child["id"])[0]
    assert first["epoch"] == 0
    assert abs(first["val_loss"] - parent["metrics"]["val_loss"]) <= 1e-4, (first, parent)
    epoch0 = [e for e in runner.events() if e["message"].startswith("Epoch 0, before training")]
    assert epoch0 and "the parent's on its own split" in epoch0[-1]["message"]


# ---------------------------------------------------------------------------- AC2


def test_RUN_1_10_AC2_frozen_layers_and_their_statistics_stay_the_rest_change(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-ac2")
    parent = api.train(split, CNN)

    child = api.train(split, CNN, init_from=parent["id"], freeze=["n0", "n1"])

    before, after = api.tensors(parent["id"]), api.tensors(child["id"])
    frozen = [k for k in before if k.startswith(("nodes.n0.", "nodes.n1."))]
    assert any(k.endswith("running_mean") for k in frozen), "BatchNorm's buffers are checked too"
    for key in frozen:
        assert torch.equal(before[key], after[key]), key
    for key in ("nodes.n3.weight", "head.weight"):
        assert not torch.equal(before[key], after[key]), key


# ---------------------------------------------------------------------------- AC3


def test_RUN_1_10_AC3_other_classes_are_refused_unless_the_head_is_reset(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    parent = api.train(labelled_split(runner, tmp_path / "a", "ft-ac3"), CNN)
    other = _three_classes(runner, tmp_path / "b", "ft-ac3-three")

    problem = api.refused(other, CNN, init_from=parent["id"])
    assert _codes(problem) == {"init_from_output"}
    assert "2 classes (cat, dog) in the parent, 3 classes (cat, dog, fox) here" in problem["detail"]

    child = api.train(other, CNN, init_from=parent["id"], reset_head=True, epochs=1)

    assert child["parent_version_id"] == parent["id"], "a parent in another lineage"
    assert child["lineage_id"] != parent["lineage_id"]
    lineage = api.get(f"/models/{child['lineage_id']}")
    base = next(v for v in lineage["versions"] if v["state"] == "base")
    start, theirs = api.tensors(base["id"]), api.tensors(parent["id"])
    assert all(torch.equal(start[k], theirs[k]) for k in theirs if not k.startswith("head."))
    assert start["head.weight"].shape == (3, 8), "only the head is new"
    assert api.history(child["id"])[0]["epoch"] == 0


def test_RUN_1_10_AC3_another_input_shape_is_refused_even_with_a_reset_head(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    parent = api.train(labelled_split(runner, tmp_path / "a", "ft-shape"), CNN)
    larger = labelled_split(runner, tmp_path / "b", "ft-shape-48", size=48)

    problem = api.refused(larger, CNN, init_from=parent["id"], reset_head=True)

    assert _codes(problem) == {"init_from_input"}
    assert "3×32×32 in the parent, 3×48×48 here" in problem["detail"]


# ---------------------------------------------------------------------------- refusals


def test_RUN_1_10_only_a_trained_version_with_weights_and_its_own_spec_can_start_a_run(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-parent")
    parent = api.train(split, CNN)
    base = next(
        v for v in api.get(f"/models/{parent['lineage_id']}")["versions"] if v["state"] == "base"
    )

    assert _codes(api.refused(split, CNN, init_from=base["id"])) == {"invalid"}
    assert _codes(api.refused(split, CNN, init_from="mv_000000000000")) == {"not_found"}
    other_spec = _chain("other", L.Conv2d(out_channels=4), L.GlobalAvgPool())
    assert "init_from_spec" in _codes(api.refused(split, other_spec, init_from=parent["id"]))
    assert "need init_from" in json.dumps(api.refused(split, CNN, reset_head=True))

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE model_versions SET weights_status = 'weights_deleted' WHERE id = :v"),
            {"v": parent["id"]},
        )
    gone = api.post("/jobs", api.body(split, CNN, init_from=parent["id"]), 409)
    assert gone["code"] == "weights_deleted"


def test_RUN_1_10_freezing_is_checked_and_reference_models_are_not_frozen(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-freeze")
    parent = api.train(split, CNN, epochs=1)

    problem = api.refused(split, CNN, init_from=parent["id"], freeze=["n2", "nope"])
    assert _codes(problem) == {"freeze_invalid"}
    problem = api.refused(split, CNN, init_from=parent["id"], reset_head=True, freeze=["head"])
    assert "a reset head must train" in problem["detail"]
    everything = ["n0", "n1", "n3", "head"]
    problem = api.refused(split, CNN, init_from=parent["id"], freeze=everything)
    assert "nothing would train" in problem["detail"]

    reference = api.train(split, None, epochs=1)
    problem = api.refused(split, None, init_from=reference["id"], freeze=["0"])
    assert _codes(problem) == {"freeze_unsupported"}
    three = _three_classes(runner, tmp_path / "three", "ft-freeze-three")
    child = api.train(three, None, init_from=reference["id"], reset_head=True, epochs=1)
    assert child["parent_version_id"] == reference["id"], "a reference model's head resets too"


# ---------------------------------------------------------------------------- validate


def test_RUN_1_10_jobs_validate_lists_every_check_and_creates_nothing(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "a", "ft-validate")
    parent = api.train(split, CNN, epochs=1)
    other = _three_classes(runner, tmp_path / "b", "ft-validate-three")
    jobs_before = len(api.get("/jobs?limit=200")["items"])

    same = api.post(
        "/jobs/validate", api.body(split, CNN, init_from=parent["id"], freeze=["n0"]), 200
    )
    assert [c["rule"] for c in same["checks"]] == [
        "Dataset type",
        "Task",
        "Input",
        "Output",
        "Spec",
    ]
    assert all(c["ok"] for c in same["checks"])
    assert same["lineage"] == {
        "id": parent["lineage_id"],
        "name": "ft_cnn · ft-validate",
        "joins": True,
    }
    assert same["frozen_parameters"] > 0 and same["freeze_problems"] == []
    total = same["trainable_parameters"] + same["frozen_parameters"]
    fresh = api.post("/jobs/validate", api.body(split, CNN), 200)
    assert fresh["checks"] == [] and fresh["trainable_parameters"] == total
    assert same["memory"]["total_bytes"] < fresh["memory"]["total_bytes"], "no gradients if frozen"

    mismatch = api.post("/jobs/validate", api.body(other, CNN, init_from=parent["id"]), 200)
    output = next(c for c in mismatch["checks"] if c["rule"] == "Output")
    assert (output["ok"], output["code"]) == (False, "init_from_output")
    assert (output["parent"], output["here"]) == (
        "2 classes (cat, dog)",
        "3 classes (cat, dog, fox)",
    )
    reset = api.post(
        "/jobs/validate", api.body(other, CNN, init_from=parent["id"], reset_head=True), 200
    )
    assert all(c["ok"] for c in reset["checks"]) and reset["lineage"]["joins"] is False
    assert len(api.get("/jobs?limit=200")["items"]) == jobs_before


# ---------------------------------------------------------------------------- tables and texts


def _table(runner: Runner, root: Path, slug: str, rows: int = 120) -> str:
    root.mkdir(parents=True)
    lines = ["size,colour,y"]
    for i in range(rows):
        size = float(i % 20)
        lines.append(f"{size},{('red', 'green', 'blue')[i % 3]},{'big' if size > 9 else 'small'}")
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


def _texts(runner: Runner, root: Path, slug: str, words: tuple[str, ...]) -> str:
    root.mkdir(parents=True)
    rows = [
        json.dumps({"text": f"a {w} number {i} sits {words[i % 2]}", "label": w})
        for i, w in enumerate(itertools.islice(itertools.cycle(("cat", "dog", "bird")), 90))
    ]
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


def test_RUN_1_10_a_table_reuses_its_parents_preprocessing(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    split = _table(runner, tmp_path / "t", "ft-table")
    parent = api.train(split, MLP)
    other = _table(runner, tmp_path / "u", "ft-table-more", rows=150)

    child = api.train(other, MLP, init_from=parent["id"], epochs=1)

    def prep(version_id: str) -> str:
        with get_engine().connect() as connection:
            folder = connection.execute(
                text("SELECT weights_path FROM model_versions WHERE id = :v"), {"v": version_id}
            ).scalar_one()
        return (api.data / folder / PREPROCESS_FILE).read_text(encoding="utf-8")

    assert prep(child["id"]) == prep(parent["id"]), "fitted on the parent's train split, kept"


def test_RUN_1_10_a_text_needs_the_parents_vocabulary(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    parent = api.train(_texts(runner, tmp_path / "a", "ft-text", ("here", "there")), LSTM)
    other = _texts(runner, tmp_path / "b", "ft-text-other", ("indoors", "outside"))

    problem = api.refused(other, LSTM, init_from=parent["id"])

    assert _codes(problem) == {"init_from_input"}
    assert "token IDs would mean other words" in problem["detail"]


# ---------------------------------------------------------------------------- studies


def test_RUN_1_10_a_study_cannot_tune_the_start(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-study")
    parent = api.train(split, CNN, epochs=1)
    base = api.body(split, CNN, init_from=parent["id"])
    problem = api.post(
        "/jobs",
        {
            "kind": "hpo_study",
            "dataset_version": split,
            "config": base["config"],
            "space": [
                {"path": "/config/reset_head", "type": "categorical", "choices": [True, False]}
            ],
            "n_trials": 2,
        },
        422,
    )
    assert "not a tunable training field" in json.dumps(problem)


# ---------------------------------------------------------------------------- retention (REG-2.8)


def test_REG_2_8_a_queued_fine_tune_holds_its_parent_and_a_parent_is_held_by_weights(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "ft-retention")
    parent = api.train(split, CNN, epochs=1)
    queued = api.start(split, CNN, init_from=parent["id"], epochs=1)
    held = list(api.api.queued)
    api.api.queued.clear()  # the fine-tune waits in the queue, with no version row yet

    api.train(split, CNN, epochs=1, seed=5)  # the parent is now an intermediate version

    plan = api.post("/retention/plan", {"lineage_id": parent["lineage_id"]}, 200)["lineages"][0]
    blocked = {d["version_id"]: d["reason"] for d in plan["blocked"]}
    assert f"{queued} starts from it" in blocked[parent["id"]]

    api.api.queued.extend(held)
    api.flush()
    child = api.get(f"/model-versions/{api.get(f'/jobs/{queued}')['model_version_id']}")
    plan = api.post("/retention/plan", {"lineage_id": parent["lineage_id"]}, 200)["lineages"][0]
    blocked = {d["version_id"]: d["reason"] for d in plan["blocked"]}
    assert parent["id"] not in blocked or child["id"] in blocked[parent["id"]]

    with get_engine().begin() as connection:  # as if retention had taken the child's weights
        connection.execute(
            text("UPDATE model_versions SET weights_status = 'weights_deleted' WHERE id = :v"),
            {"v": child["id"]},
        )
    plan = api.post("/retention/plan", {"lineage_id": parent["lineage_id"]}, 200)["lineages"][0]
    assert parent["id"] not in {d["version_id"] for d in plan["blocked"]}, "nothing holds it now"
