"""RUN-1.10: the fine-tuning fields of a training config, freezing and its memory estimate.

ADR-0014 Addendum A rules 1, 7 and 11, without PyTorch: what the API checks before
a job exists.
"""

import itertools
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from stratal.build import layers as L
from stratal.build.shapes import Shape
from stratal.build.spec import GraphSpec, Node, validate_graph
from stratal.runtime.jobs import config_hash
from stratal.train import finetune
from stratal.train.config import TrainConfig

GRAPH = GraphSpec(
    label="cnn",
    nodes=[
        Node(id="c", layer=L.Conv2d(out_channels=8)),
        Node(id="bn", layer=L.BatchNorm()),
        Node(id="relu", layer=L.ReLU()),
        Node(id="pool", layer=L.GlobalAvgPool()),
    ],
    edges=list(itertools.pairwise(["input", "c", "bn", "relu", "pool", "output"])),
)
MODEL: dict[str, Any] = {"name": "graph", "graph": GRAPH.model_dump(mode="json")}


def _report() -> Any:
    return validate_graph(GRAPH, input_shape=Shape("image", (3, 32, 32)), output_size=10)


def test_RUN_1_10_a_fresh_config_serializes_and_hashes_as_before() -> None:
    fresh = TrainConfig.model_validate({"model": {"name": "small_cnn"}})
    dumped = fresh.model_dump(mode="json")
    assert not {"init_from", "reset_head", "freeze"} & set(dumped), "M8-M10 hashes unchanged"
    assert config_hash(dumped) == config_hash(
        TrainConfig.model_validate(dumped).model_dump(mode="json")
    )


def test_RUN_1_10_the_three_fields_are_kept_and_hashed_when_fine_tuning() -> None:
    a = TrainConfig.model_validate(
        {"model": MODEL, "init_from": "mv_a014046ec3b5", "freeze": ["c", "bn", "c"]}
    )
    b = a.model_copy(update={"reset_head": True})
    assert a.freeze == ["bn", "c"], "sorted, once each"
    assert a.model_dump(mode="json")["init_from"] == "mv_a014046ec3b5"
    assert config_hash(a.model_dump(mode="json")) != config_hash(b.model_dump(mode="json"))


@pytest.mark.parametrize(
    "fields",
    [{"reset_head": True}, {"freeze": ["c"]}, {"init_from": "not-a-version"}],
)
def test_RUN_1_10_reset_and_freeze_need_a_parent_and_the_parent_is_a_version_id(
    fields: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TrainConfig.model_validate({"model": MODEL, **fields})


def _config(**fields: Any) -> Any:
    return TrainConfig.model_validate({"model": MODEL, "init_from": "mv_a014046ec3b5", **fields})


def test_RUN_1_10_freeze_takes_nodes_with_weights_and_leaves_something_to_train() -> None:
    report = _report()
    assert finetune.freeze_problems(_config(freeze=["c", "bn"]), report) == []

    by_node = {
        p["loc"][-1]: p["msg"]
        for p in finetune.freeze_problems(_config(freeze=["relu", "nope"]), report)
    }
    assert by_node == {0: "no node 'nope'", 1: "node 'relu' has no weights to freeze"}

    reset = finetune.freeze_problems(_config(reset_head=True, freeze=["head"]), report)
    assert [p["msg"] for p in reset] == ["a reset head must train"]
    everything = finetune.freeze_problems(_config(freeze=["c", "bn", "head"]), report)
    assert [p["code"] for p in everything] == ["freeze_invalid"]
    assert "nothing would train" in everything[0]["msg"]


def test_RUN_1_10_a_reference_model_is_not_frozen() -> None:
    config = SimpleNamespace(freeze=["0"], model=SimpleNamespace(graph=None), reset_head=False)
    assert [p["code"] for p in finetune.freeze_problems(config, None)] == ["freeze_unsupported"]


def test_RUN_1_10_frozen_parameters_need_no_gradients_or_optimizer_state() -> None:
    report = _report()
    frozen = finetune.frozen_parameters(report, ["c", "bn"])
    conv = next(n.parameters for n in report.nodes if n.id == "c")
    norm = next(n.parameters for n in report.nodes if n.id == "bn")
    assert frozen == conv + norm
    total = report.total_parameters
    memory = finetune.trainable_memory(report.memory, total, frozen)
    assert memory.weights_bytes == report.memory.weights_bytes, "every weight is still loaded"
    trainable = (total - frozen) / total
    assert memory.gradients_bytes == round(report.memory.gradients_bytes * trainable)
    assert memory.optimizer_bytes == round(report.memory.optimizer_bytes * trainable)
    assert memory.total_bytes < report.memory.total_bytes
    assert finetune.trainable_memory(report.memory, total, 0) == report.memory
