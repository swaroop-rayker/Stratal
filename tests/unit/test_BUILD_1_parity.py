# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the worker images, so the type checker cannot see it here.
"""BUILD-1.4, BUILD-1.5, BUILD-1.9: the plain-Python figures match PyTorch (ADR-0017 rule 4).

Random valid specs are compiled; for each, every node's real output shape must
equal the inferred one, and ``sum(p.numel())`` and torchinfo's count must equal
the builder's parameter count. Runs in the torch image (check.ps1's torch step).
"""

import random
from typing import Any

import pytest

torch = pytest.importorskip("torch")
torchinfo = pytest.importorskip("torchinfo")

from stratal.build import layers as L  # noqa: E402
from stratal.build.compile import compile_graph  # noqa: E402
from stratal.build.shapes import Shape  # noqa: E402
from stratal.build.spec import GraphSpec, Node, chain, validate_graph  # noqa: E402

pytestmark = pytest.mark.torch
SPECS = 300


def _image_layer(rng: random.Random) -> Any:
    return rng.choice(
        [
            lambda: L.Conv2d(
                out_channels=rng.choice([4, 8, 16]),
                kernel_size=rng.choice([1, 3, 5]),
                stride=rng.choice([1, 1, 2]),
                padding=rng.choice([0, 1, 2]),
                dilation=rng.choice([1, 1, 2]),
                bias=rng.random() < 0.5,
            ),
            lambda: L.MaxPool2d(kernel_size=2),
            lambda: L.AvgPool2d(kernel_size=3, stride=2, padding=1),
            lambda: L.BatchNorm(),
            lambda: L.ReLU(),
            lambda: L.LeakyReLU(negative_slope=0.2),
            lambda: L.Dropout(p=0.1),
        ]
    )()


def _vector_layer(rng: random.Random) -> Any:
    return rng.choice(
        [
            lambda: L.Linear(out_features=rng.choice([8, 16, 32]), bias=rng.random() < 0.7),
            lambda: L.BatchNorm(),
            lambda: L.LayerNorm(),
            lambda: L.GELU(),
            lambda: L.Tanh(),
            lambda: L.Sigmoid(),
            lambda: L.Softmax(),
            lambda: L.Dropout(),
        ]
    )()


def _text_chain(rng: random.Random) -> list[Any]:
    width = rng.choice([8, 16, 24])
    layers: list[Any] = [L.Embedding(embedding_dim=width, padding_idx=rng.choice([None, 0]))]
    if rng.random() < 0.5:
        layers += [L.PositionalEncoding(max_len=64), L.Attention(num_heads=rng.choice([1, 2, 4]))]
        layers += [L.LayerNorm()]
    recurrent = rng.choice([L.RNN, L.LSTM, L.GRU])
    extra: dict[str, Any] = {}
    if recurrent is L.LSTM:
        extra["implementation"] = rng.choice(["torch", "scratch"])
    layers_n = rng.choice([1, 2])
    layers.append(
        recurrent(
            hidden_size=rng.choice([6, 12]),
            num_layers=layers_n,
            bidirectional=rng.random() < 0.5,
            dropout=0.1 if layers_n > 1 else 0.0,
            output=rng.choice(["sequence", "last"]),
            **extra,
        )
    )
    if layers[-1].output == "sequence":
        layers.append(rng.choice([L.GlobalAvgPool(), L.Flatten()]))
    layers.append(_vector_layer(rng))
    return layers


def _image_chain(rng: random.Random) -> list[Any]:
    layers = [_image_layer(rng) for _ in range(rng.randint(1, 6))]
    layers.append(rng.choice([L.GlobalAvgPool(), L.Flatten()]))
    layers += [_vector_layer(rng) for _ in range(rng.randint(0, 3))]
    return layers


def _residual(rng: random.Random) -> GraphSpec:
    channels = rng.choice([4, 8])
    merge = rng.choice([L.Add(), L.Concat()])
    nodes = {
        "stem": L.Conv2d(out_channels=channels),
        "a": L.Conv2d(out_channels=channels),
        "b": L.ReLU(),
        "join": merge,
        "pool": L.GlobalAvgPool(),
    }
    edges = [
        ("input", "stem"),
        ("stem", "a"),
        ("a", "b"),
        ("b", "join"),
        ("stem", "join"),
        ("join", "pool"),
        ("pool", "output"),
    ]
    return GraphSpec(nodes=[Node(id=k, layer=v) for k, v in nodes.items()], edges=edges)


def _random_spec(rng: random.Random) -> tuple[GraphSpec, Shape]:
    kind = rng.random()
    if kind < 0.45:
        size = rng.choice([8, 16, 32])
        return chain("img", *_image_chain(rng)), Shape("image", (3, size, size))
    if kind < 0.85:
        return chain("txt", *_text_chain(rng)), Shape("tokens", (rng.choice([5, 12]),), vocab=50)
    return _residual(rng), Shape("image", (3, 16, 16))


def _batch(shape: Shape) -> Any:
    if shape.kind == "tokens":
        assert shape.vocab is not None
        return torch.randint(0, shape.vocab, (2, *shape.dims))
    return torch.randn(2, *shape.dims)


def test_BUILD_1_5_counts_and_shapes_match_pytorch_on_random_specs() -> None:
    rng = random.Random(9)
    checked = 0
    for _ in range(SPECS * 3):
        spec, shape = _random_spec(rng)
        report = validate_graph(spec, input_shape=shape, output_size=5)
        if not report.valid:
            continue  # random stacks may shrink an image too far; that is BUILD-1.6's job
        torch.manual_seed(checked)
        model = compile_graph(spec, shape, 5).eval()
        seen: dict[str, tuple[int, ...]] = {}
        for node_id, module in model.nodes.items():
            module.register_forward_hook(
                lambda _m, _i, out, node_id=node_id, seen=seen: seen.__setitem__(
                    node_id, tuple(out.shape[1:])
                )
            )
        with torch.no_grad():
            out = model(_batch(shape))

        assert tuple(out.shape) == (2, 5)
        expected = {n.id: tuple(n.shape["dims"]) for n in report.nodes if n.shape is not None}
        assert seen == expected, spec.model_dump_json()
        assert sum(p.numel() for p in model.parameters()) == report.total_parameters, (
            spec.model_dump_json()
        )
        summary = torchinfo.summary(model, input_data=[_batch(shape)], verbose=0)
        assert summary.total_params == report.total_parameters
        checked += 1
        if checked == SPECS:
            break
    assert checked == SPECS, f"only {checked} valid random specs"


def test_BUILD_1_9_a_residual_block_builds_and_trains() -> None:
    torch.manual_seed(0)
    spec = _residual(random.Random(1))
    model = compile_graph(spec, Shape("image", (3, 16, 16)), 3)
    images = torch.randn(32, 3, 16, 16)
    labels = (images.mean(dim=(1, 2, 3)) > 0).long() + (images[:, 0].mean(dim=(1, 2)) > 0).long()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
    losses = []
    for _ in range(60):
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(images), labels)
        loss.backward()
        optimizer.step()
        losses.append(loss.item())

    assert losses[-1] < 0.5 * losses[0], (losses[0], losses[-1])
    names = set(model.state_dict())
    assert {"nodes.stem.weight", "nodes.a.weight", "head.weight"} <= names, "stable names"


def test_BUILD_1_4_compiling_an_invalid_spec_is_refused() -> None:
    from stratal.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="in_features"):
        compile_graph(
            chain("bad", L.Flatten(), L.Linear(in_features=7)), Shape("image", (3, 8, 8)), 2
        )


def test_BUILD_1_1_a_graph_spec_trains_through_the_factory() -> None:
    """ADR-0014 rule 2: the train stage's factory builds a graph like a reference model."""
    from stratal.train.config import ModelSpec
    from stratal.train.reference import build_model

    spec = ModelSpec(name="graph", graph=chain("g", L.Conv2d(out_channels=4), L.GlobalAvgPool()))
    model = build_model(spec, (3, 8, 8), 4)
    assert model(torch.randn(2, 3, 8, 8)).shape == (2, 4)
