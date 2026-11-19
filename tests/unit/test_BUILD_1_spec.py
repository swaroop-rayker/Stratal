"""BUILD-1.1, 1.2, 1.4, 1.5, 1.6, 1.9: layer specs, graph checks, shapes and counts (ADR-0017).

Pure Python, as the API runs it. The same figures are checked against PyTorch in
``tests/unit/test_BUILD_1_parity.py`` (torch image).
"""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from stratal.build import layers as L
from stratal.build.shapes import Shape
from stratal.build.spec import (
    GraphSpec,
    Node,
    Report,
    canonical,
    chain,
    identity,
    structure_hash,
    validate_graph,
)

IMAGE = Shape("image", (3, 32, 32))
TOKENS = Shape("tokens", (20,), vocab=1000)
LAYER: TypeAdapter[Any] = TypeAdapter(L.Layer)


def check(spec: GraphSpec, shape: Shape = IMAGE, classes: int = 10, **kw: Any) -> Report:
    return validate_graph(spec, input_shape=shape, output_size=classes, **kw)


def codes(report: Report) -> list[str]:
    return [p.code for p in report.problems]


def graph(nodes: dict[str, Any], edges: list[tuple[str, str]]) -> GraphSpec:
    return GraphSpec(nodes=[Node(id=k, layer=v) for k, v in nodes.items()], edges=edges)


# ---------------------------------------------------------------------------- BUILD-1.1


SMALL_CNN = chain(
    "small_cnn",
    L.Conv2d(out_channels=32, bias=False),
    L.BatchNorm(),
    L.ReLU(),
    L.MaxPool2d(),
    L.Conv2d(out_channels=64),
    L.ReLU(),
    L.GlobalAvgPool(),
)

TEXT = chain(
    "text",
    L.Embedding(embedding_dim=32),
    L.PositionalEncoding(),
    L.Attention(num_heads=4),
    L.LayerNorm(),
    L.LSTM(hidden_size=16, output="sequence", implementation="scratch"),
    L.GRU(hidden_size=16, output="sequence"),
    L.RNN(hidden_size=8, nonlinearity="relu"),
    L.Dropout(p=0.1),
    L.Linear(out_features=8),
    L.LeakyReLU(),
    L.GELU(),
    L.Tanh(),
    L.Sigmoid(),
    L.Softmax(),
)


def test_BUILD_1_1_every_layer_type_can_be_added_and_configured() -> None:
    image = check(
        chain(
            "all_image",
            L.Conv2d(out_channels=8, kernel_size=5, padding=2, stride=1, dilation=1),
            L.BatchNorm(momentum=0.2),
            L.ReLU(),
            L.MaxPool2d(kernel_size=2),
            L.AvgPool2d(kernel_size=2, stride=2),
            L.Flatten(),
            L.Linear(out_features=16, bias=False),
            L.BatchNorm(),
        )
    )
    text = check(TEXT, TOKENS)

    assert image.valid and text.valid, (image.problems, text.problems)
    used = {n.type for n in image.nodes} | {n.type for n in text.nodes}
    used |= {"global_avg_pool", "add", "concat"}  # in the residual and concat tests below
    assert used == {t.model_fields["type"].default for t in L.LAYER_TYPES}


def test_BUILD_1_1_the_schema_lists_every_layer_type_with_its_defaults() -> None:
    schema = LAYER.json_schema()
    names = {v["properties"]["type"]["const"] for v in schema["$defs"].values()}
    assert names == {t.model_fields["type"].default for t in L.LAYER_TYPES}
    conv = schema["$defs"]["Conv2d"]["properties"]
    assert conv["kernel_size"]["default"] == 3 and conv["kernel_size"]["maximum"] == 15
    assert conv["kernel_size"]["description"], "help text for the generated form (UI-2.3)"
    assert "merges" not in conv and "gates" not in schema["$defs"]["LSTM"]["properties"]


# ---------------------------------------------------------------------------- BUILD-1.2


@pytest.mark.parametrize(
    ("layer", "field"),
    [
        ({"type": "linear", "out_features": 0}, "out_features"),
        ({"type": "conv2d", "kernel_size": 16}, "kernel_size"),
        ({"type": "dropout", "p": 1.0}, "p"),
        ({"type": "lstm", "num_layers": 9}, "num_layers"),
        ({"type": "attention", "num_heads": 0}, "num_heads"),
        ({"type": "batch_norm", "momentum": 0}, "momentum"),
    ],
)
def test_BUILD_1_2_out_of_range_values_are_rejected_with_a_message(
    layer: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        LAYER.validate_python(layer)
    error = caught.value.errors()[0]
    assert field in [str(part) for part in error["loc"]]
    assert error["msg"]


def test_BUILD_1_2_unknown_types_and_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LAYER.validate_python({"type": "transformer_xl"})
    with pytest.raises(ValidationError):
        LAYER.validate_python({"type": "relu", "inplace": True})
    with pytest.raises(ValidationError, match="fixed node"):
        Node(id="input", layer=L.ReLU())
    with pytest.raises(ValidationError):
        Node(id="Has Spaces", layer=L.ReLU())


# ---------------------------------------------------------------------------- BUILD-1.4


def test_BUILD_1_4_a_mismatched_linear_input_is_caught_at_build_time() -> None:
    spec = chain("mlp", L.Flatten(), L.Linear(in_features=1000, out_features=64), L.ReLU())

    report = check(spec)

    assert not report.valid and codes(report) == ["shape_mismatch"]
    problem = report.problems[0]
    assert problem.node_id == "n2" and "3072" in problem.message and "1000" in problem.message
    assert report.nodes[0].shape_text == "vector 3072", "what is known before it is shown"


def test_BUILD_1_4_shapes_are_inferred_layer_by_layer() -> None:
    report = check(SMALL_CNN)

    assert report.valid, report.problems
    assert [n.shape_text for n in report.nodes] == [
        "image 32×32×32",
        "image 32×32×32",
        "image 32×32×32",
        "image 32×16×16",
        "image 64×16×16",
        "image 64×16×16",
        "vector 64",
    ]


@pytest.mark.parametrize(
    ("layers", "code"),
    [
        ([L.Linear()], "rank_mismatch"),  # an image straight into a linear layer
        ([L.Conv2d(in_channels=1)], "shape_mismatch"),
        ([L.Flatten(), L.Conv2d()], "rank_mismatch"),
        ([L.Embedding()], "rank_mismatch"),
        ([L.Conv2d()], "output_not_vector"),
    ],
)
def test_BUILD_1_4_invalid_stacks_are_blocked(layers: list[Any], code: str) -> None:
    assert code in codes(check(chain("bad", *layers)))


# ---------------------------------------------------------------------------- BUILD-1.5


def test_BUILD_1_5_parameter_count_and_memory_follow_the_formula() -> None:
    report = check(SMALL_CNN, optimizer="adam")

    conv1 = 32 * 3 * 3 * 3  # no bias
    bn = 2 * 32
    conv2 = 64 * 32 * 9 + 64
    head = 64 * 10 + 10
    assert [n.parameters for n in report.nodes] == [conv1, bn, 0, 0, conv2, 0, 0]
    total = conv1 + bn + conv2 + head
    assert report.total_parameters == total and report.head is not None
    assert report.head.parameters == head
    memory = report.memory
    assert memory is not None
    assert (memory.weights_bytes, memory.gradients_bytes, memory.optimizer_bytes) == (
        4 * total,
        4 * total,
        8 * total,
    )
    assert memory.total_bytes == 16 * total
    sgd = check(SMALL_CNN, optimizer="sgd").memory
    momentum = check(SMALL_CNN, optimizer="sgd_momentum").memory
    assert sgd is not None and momentum is not None
    assert (sgd.optimizer_bytes, momentum.optimizer_bytes) == (0, 4 * total)


def test_BUILD_1_5_recurrent_and_attention_counts() -> None:
    sequence = Shape("sequence", (10, 6))
    lstm = L.LSTM(hidden_size=5, num_layers=2, bidirectional=True).apply([sequence])
    # layer 1: 2 directions × 4 gates × (5×6 + 5×5 + 2×5); layer 2's input is 10 wide
    assert lstm.parameters == 2 * 4 * (30 + 25 + 10) + 2 * 4 * (50 + 25 + 10)
    assert lstm.shape == Shape("vector", (10,))
    assert L.GRU(hidden_size=5).apply([sequence]).parameters == 3 * (30 + 25 + 10)
    assert L.Attention(num_heads=2).apply([sequence]).parameters == 4 * 36 + 4 * 6
    assert L.Embedding(embedding_dim=7).apply([TOKENS]).parameters == 7000


# ---------------------------------------------------------------------------- BUILD-1.6


def test_BUILD_1_6_d_model_must_divide_into_heads() -> None:
    spec = chain("t", L.Embedding(embedding_dim=30), L.Attention(num_heads=4), L.GlobalAvgPool())
    assert codes(check(spec, TOKENS)) == ["heads_do_not_divide"]


def test_BUILD_1_6_a_conv_stack_never_shrinks_below_one_pixel() -> None:
    spec = chain("c", *[L.MaxPool2d() for _ in range(6)], L.Flatten())
    report = check(spec)
    assert codes(report) == ["below_one_pixel"] and report.problems[0].node_id == "n6"


def test_BUILD_1_6_a_convolution_kernel_larger_than_its_input() -> None:
    spec = chain("c", L.Conv2d(kernel_size=7, padding=0), L.Flatten())
    assert codes(check(spec, Shape("image", (3, 5, 5)))) == ["below_one_pixel"]


def test_BUILD_1_6_pooling_padding_is_at_most_half_the_window() -> None:
    spec = chain("c", L.MaxPool2d(kernel_size=2, padding=2), L.Flatten())
    assert codes(check(spec)) == ["invalid_padding"]


def test_BUILD_1_6_positional_encoding_needs_an_even_width() -> None:
    spec = chain("t", L.Embedding(embedding_dim=31), L.PositionalEncoding(), L.GlobalAvgPool())
    assert codes(check(spec, TOKENS)) == ["odd_model_dim"]


def test_BUILD_1_6_positional_encoding_covers_the_sequence() -> None:
    spec = chain("t", L.Embedding(), L.PositionalEncoding(max_len=10), L.GlobalAvgPool())
    assert codes(check(spec, TOKENS)) == ["sequence_too_long"]


def test_BUILD_1_6_recurrent_dropout_needs_stacked_layers() -> None:
    spec = chain("r", L.Embedding(), L.GRU(dropout=0.2))
    assert codes(check(spec, TOKENS)) == ["dropout_needs_layers"]


def test_BUILD_1_6_tokens_must_be_embedded_first() -> None:
    assert codes(check(chain("t", L.Dropout()), TOKENS)) == ["tokens_need_embedding"]


def test_BUILD_1_6_padding_idx_lies_in_the_vocabulary() -> None:
    spec = chain("t", L.Embedding(padding_idx=1000), L.GlobalAvgPool())
    assert codes(check(spec, TOKENS)) == ["invalid_padding_idx"]


def test_BUILD_1_6_the_output_needs_a_vector() -> None:
    assert codes(check(chain("c", L.Conv2d()))) == ["output_not_vector"]


def test_BUILD_1_6_merges_need_two_inputs_of_matching_shape() -> None:
    one = graph({"a": L.Add(), "f": L.Flatten()}, [("input", "a"), ("a", "f"), ("f", "output")])
    assert codes(check(one)) == ["merge_needs_two"]
    mismatched = graph(
        {"c": L.Conv2d(out_channels=8), "a": L.Add(), "g": L.GlobalAvgPool()},
        [("input", "c"), ("input", "a"), ("c", "a"), ("a", "g"), ("g", "output")],
    )
    assert codes(check(mismatched)) == ["merge_shape_mismatch"]


def test_BUILD_1_6_only_merges_take_several_inputs() -> None:
    spec = graph(
        {"c": L.Conv2d(out_channels=3), "r": L.ReLU(), "g": L.GlobalAvgPool()},
        [("input", "c"), ("input", "r"), ("c", "r"), ("r", "g"), ("g", "output")],
    )
    assert codes(check(spec)) == ["too_many_inputs"]


def test_BUILD_1_6_cycles_and_loose_ends_are_reported() -> None:
    cyclic = graph(
        {"a": L.ReLU(), "b": L.ReLU(), "g": L.GlobalAvgPool()},
        [("input", "a"), ("a", "b"), ("b", "a"), ("input", "g"), ("g", "output")],
    )
    assert sorted(codes(check(cyclic))) == ["cycle", "cycle"]
    loose = graph(
        {"a": L.ReLU(), "z": L.ReLU(), "g": L.GlobalAvgPool()},
        [("input", "a"), ("input", "g"), ("g", "output")],
    )
    report = check(loose)
    assert [(p.code, p.node_id) for p in report.problems] == [
        ("dangling", "a"),
        ("unreachable", "z"),
    ]
    assert codes(check(graph({}, []))) == ["unreachable", "output_inputs"]


def test_BUILD_1_6_edges_must_name_nodes_and_ids_must_be_unique() -> None:
    spec = GraphSpec(
        nodes=[Node(id="a", layer=L.ReLU()), Node(id="a", layer=L.ReLU())],
        edges=[("input", "a"), ("a", "nowhere"), ("a", "output")],
    )
    assert {"duplicate_id", "unknown_node"} <= set(codes(check(spec)))


def test_BUILD_1_6_a_problem_does_not_cascade_downstream() -> None:
    spec = chain("c", L.Linear(), L.ReLU(), L.Linear(out_features=4))
    assert codes(check(spec)) == ["rank_mismatch"], "one cause, one problem"


# ---------------------------------------------------------------------------- BUILD-1.9


RESIDUAL = graph(
    {
        "stem": L.Conv2d(out_channels=16),
        "c1": L.Conv2d(out_channels=16),
        "r1": L.ReLU(),
        "c2": L.Conv2d(out_channels=16),
        "skip": L.Add(),
        "r2": L.ReLU(),
        "pool": L.GlobalAvgPool(),
    },
    [
        ("input", "stem"),
        ("stem", "c1"),
        ("c1", "r1"),
        ("r1", "c2"),
        ("c2", "skip"),
        ("stem", "skip"),
        ("skip", "r2"),
        ("r2", "pool"),
        ("pool", "output"),
    ],
)


def test_BUILD_1_9_a_residual_block_validates() -> None:
    report = check(RESIDUAL)
    assert report.valid, report.problems
    skip = next(n for n in report.nodes if n.id == "skip")
    assert skip.inputs == ["c2", "stem"] and skip.shape_text == "image 16×32×32"
    assert report.family == "cnn"


def test_BUILD_1_9_concat_joins_channels_and_features() -> None:
    spec = graph(
        {
            "a": L.Conv2d(out_channels=4),
            "b": L.Conv2d(out_channels=6),
            "j": L.Concat(),
            "g": L.GlobalAvgPool(),
        },
        [("input", "a"), ("input", "b"), ("a", "j"), ("b", "j"), ("j", "g"), ("g", "output")],
    )
    report = check(spec)
    assert report.valid and report.head is not None and report.head.in_features == 10


# ---------------------------------------------------------------------------- identity (BUILD-1.7)


def test_BUILD_1_7_canonical_form_ignores_listing_order_and_label() -> None:
    shuffled = RESIDUAL.model_copy(
        update={
            "label": "renamed",
            "nodes": list(reversed(RESIDUAL.nodes)),
            "edges": list(reversed(RESIDUAL.edges)),
        }
    )
    assert canonical(shuffled).nodes == canonical(RESIDUAL).nodes
    assert structure_hash(shuffled) == structure_hash(RESIDUAL)
    assert identity(shuffled) == identity(RESIDUAL) and "label" not in identity(RESIDUAL)
    assert [n.id for n in canonical(RESIDUAL).nodes][:2] == ["stem", "c1"]


def test_BUILD_1_7_explicit_defaults_hash_the_same() -> None:
    plain = chain("a", L.ReLU(), L.GlobalAvgPool())
    spelled = GraphSpec.model_validate_json(plain.model_dump_json())
    assert structure_hash(plain) == structure_hash(spelled)
    different = chain("a", L.LeakyReLU(), L.GlobalAvgPool())
    assert structure_hash(different) != structure_hash(plain)


def test_families_are_derived_from_the_layers() -> None:
    assert check(SMALL_CNN).family == "cnn"
    assert check(TEXT, TOKENS).family == "transformer"
    assert check(chain("m", L.Flatten(), L.Linear())).family == "mlp"


# ---------------------------------------------------------------------------- the train config


def test_BUILD_1_1_reference_specs_serialize_exactly_as_in_m8() -> None:
    """ADR-0017 rule 2: earlier runs' config hashes and lineage keys do not change."""
    from stratal.train.config import ModelSpec, TrainConfig

    spec = ModelSpec(name="small_cnn")
    assert spec.model_dump(mode="json") == {"name": "small_cnn"}
    assert spec.identity() == {"name": "small_cnn"} and spec.display == "small_cnn"
    config = TrainConfig.model_validate({"model": {"name": "small_cnn"}})
    assert config.model_dump(mode="json")["model"] == {"name": "small_cnn"}


def test_BUILD_1_1_a_graph_spec_is_a_train_config_model() -> None:
    from stratal.train.config import ModelSpec, TrainConfig

    config = TrainConfig.model_validate(
        {"model": {"name": "graph", "graph": SMALL_CNN.model_dump(mode="json")}}
    )
    assert config.model.display == "small_cnn"
    renamed = ModelSpec(name="graph", graph=SMALL_CNN.model_copy(update={"label": "other"}))
    assert renamed.identity() == config.model.identity(), "the label is not in the lineage key"
    with pytest.raises(ValidationError, match="graph"):
        ModelSpec(name="graph")
    with pytest.raises(ValidationError, match="graph"):
        ModelSpec(name="mlp", graph=SMALL_CNN)
