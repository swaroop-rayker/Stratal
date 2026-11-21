# pyright: basic, reportMissingImports=false
"""EVAL-1.3 (M11, SCR-11 v2): versions side by side on one test split, and Score on test.

Real tiny trainings on the CPU make versions scored on one split. A version
without test metrics, as every run before M10, is made by removing them. Needs
PyTorch, so it runs in the GPU worker's image.
"""

import itertools
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

pytest.importorskip("torch")

from stratal.build import layers as L
from stratal.build.spec import GraphSpec, Node
from stratal.db.engine import get_engine
from stratal.eval.report_stats import wilson_interval
from stratal.testing.harness import Runner
from stratal.testing.labelled import labelled_split
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    return GraphSpec(
        label=label, nodes=nodes, edges=list(itertools.pairwise(["input", *ids, "output"]))
    )


CNNS = [
    _chain("cmp_a", L.Conv2d(out_channels=4), L.GlobalAvgPool()),
    _chain("cmp_b", L.Conv2d(out_channels=8), L.ReLU(), L.GlobalAvgPool()),
    _chain("cmp_c", L.Conv2d(out_channels=6), L.BatchNorm(), L.GlobalAvgPool()),
]


class Api:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.api = PipelineApi(runner)

    def flush(self) -> None:
        self.runner.queue.extend(self.api.queued)
        self.api.queued.clear()
        self.runner.run_queued()
        self.runner.collect()

    def call(self, method: str, path: str, status: int, **kwargs: Any) -> Any:
        response = self.api.client.request(method, f"/api/v1{path}", **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def train(self, version: str, graph: GraphSpec) -> str:
        body = {
            "kind": "train",
            "dataset_version": version,
            "config": {
                "model": {"name": "graph", "graph": graph.model_dump(mode="json")},
                "epochs": 1,
                "batch_size": 8,
                "device": "cpu",
                "loader_workers": 0,
            },
        }
        job = self.call("POST", "/jobs", 202, json=body)
        self.flush()
        done = self.call("GET", f"/jobs/{job['id']}", 200)
        assert done["state"] == "completed", done
        return str(done["model_version_id"])

    def compare(self, split: str, ids: list[str], status: int = 200) -> Any:
        return self.call("GET", "/models/compare", status, params={"split": split, "v": ids})


def _untest(version_id: str) -> None:
    """As a version trained before M10: no test metrics."""
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "UPDATE model_versions SET metrics = (metrics - 'test_accuracy' - "
                "'test_macro_f1') #- '{detail,test}' WHERE id = :v"
            ),
            {"v": version_id},
        )


def test_EVAL_1_3_three_models_compared_in_one_view(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "cmp-three")
    ids = [api.train(split, g) for g in CNNS]

    view = api.compare(split, ids)

    assert view["split"]["version_id"] == split and view["split"]["classes"] == ["cat", "dog"]
    assert [c["id"] for c in view["versions"]] == ids, "in the order asked"
    rows = view["split"]["test_rows"]
    for column in view["versions"]:
        assert set(column["metrics"]) == {"accuracy", "macro_f1"}
        assert column["correct"] == round(column["metrics"]["accuracy"] * rows)
        interval = wilson_interval(column["correct"], rows)
        assert (column["accuracy_low"], column["accuracy_high"]) == (
            round(interval.low, 6),
            round(interval.high, 6),
        )
        assert set(column["per_class_f1"]) == {"cat", "dog"}
        assert column["kind"] == "network" and column["parent_is_base"] is True
    accuracies = {c["id"]: c["metrics"]["accuracy"] for c in view["versions"]}
    if len(set(accuracies.values())) > 1:
        top = max(accuracies.values())
        assert view["best"]["accuracy"] == [i for i, a in accuracies.items() if a == top]
    else:
        assert "accuracy" not in view["best"], "a tie everywhere marks no best"


def test_EVAL_1_3_only_versions_scored_on_the_split_are_compared(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "a", "cmp-only")
    other = labelled_split(runner, tmp_path / "b", "cmp-other", per_class=16)
    a, b = api.train(split, CNNS[0]), api.train(split, CNNS[1])
    elsewhere = api.train(other, CNNS[2])

    problem = api.compare(split, [a, elsewhere], status=422)
    assert problem["errors"][0]["loc"] == ["v", 1]
    assert f"tested on {other}, not {split}" in problem["errors"][0]["msg"]
    assert api.compare(split, [a], status=422)["errors"][0]["loc"] == ["v"]
    assert api.compare(split, [a, a], status=422)["errors"][0]["loc"] == ["v"]

    _untest(b)
    problem = api.compare(split, [a, b], status=422)
    assert "has no test metrics: score it on test first" in problem["errors"][0]["msg"]


def test_EVAL_1_3_score_on_test_runs_once_for_a_version_without_test_metrics(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    split = labelled_split(runner, tmp_path / "src", "cmp-score")
    a, b = api.train(split, CNNS[0]), api.train(split, CNNS[1])
    before = api.call("GET", f"/model-versions/{b}", 200)["metrics"]
    _untest(b)

    listed = {
        c["id"]: c
        for c in api.call("GET", "/models/compare/candidates", 200, params={"split": split})[
            "items"
        ]
    }
    assert listed[b]["can_score"] is True and listed[b]["test_value"] is None
    assert listed[a]["can_score"] is False and listed[a]["test_metric"] == "accuracy"

    job = api.call("POST", f"/model-versions/{b}/score-test", 202)
    assert job["kind"] == "score_test"
    queued = {
        c["id"]: c
        for c in api.call("GET", "/models/compare/candidates", 200, params={"split": split})[
            "items"
        ]
    }
    assert queued[b]["scoring_job_id"] == job["id"]
    assert api.call("POST", f"/model-versions/{b}/score-test", 409)["code"] == "conflict", "once"
    api.flush()

    done = api.call("GET", f"/jobs/{job['id']}", 200)
    assert done["state"] == "completed", done
    after = api.call("GET", f"/model-versions/{b}", 200)["metrics"]
    assert after["test_accuracy"] == pytest.approx(before["test_accuracy"], abs=1e-9)
    assert after["detail"]["test"]["rows"] == before["detail"]["test"]["rows"]
    assert len(api.compare(split, [a, b])["versions"]) == 2
    assert api.call("POST", f"/model-versions/{b}/score-test", 409)["code"] == "conflict"

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE model_versions SET weights_status = 'weights_deleted' WHERE id = :v"),
            {"v": a},
        )
    _untest(a)
    gone = api.call("POST", f"/model-versions/{a}/score-test", 409)
    assert gone["code"] == "weights_deleted"
    listed = {
        c["id"]: c
        for c in api.call("GET", "/models/compare/candidates", 200, params={"split": split})[
            "items"
        ]
    }
    assert (listed[a]["can_score"], listed[a]["why_not"]) == (
        False,
        "weights deleted: cannot be scored",
    )
