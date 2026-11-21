# pyright: basic, reportMissingImports=false
# PyTorch exists only in the worker images, so this module is checked in basic mode
# (DECISIONS.md, "untyped libraries behind one module each").
"""Compile a graph spec to a PyTorch module (ADR-0017 rule 8).

``compile_graph`` is what :func:`stratal.train.reference.build_model` calls for
``{"name": "graph"}``, so a built model trains, checkpoints and registers
exactly like a reference one. The spec is validated first with the same
plain-Python code the API uses; the compiled module's shapes and parameter
counts are checked against those figures by ``tests/unit/test_BUILD_1_parity.py``.
"""

from typing import Any

import torch
from torch import nn

from stratal.build import layers as L
from stratal.build import scratch
from stratal.build.shapes import Shape
from stratal.build.spec import INPUT, OUTPUT, GraphSpec, canonical, input_order, validate_graph
from stratal.core.errors import ValidationFailed


class GraphModule(nn.Module):
    """Runs the nodes in topological order, then the locked head (ADR-0017 rules 5 and 8).

    Parameters are named ``nodes.<id>.…`` and ``head.…``, so a spec's weights load
    into the next run of the same spec (ADR-0015).
    """

    def __init__(self, spec: GraphSpec, input_shape: Shape, num_classes: int) -> None:
        super().__init__()
        form = canonical(spec)
        report = validate_graph(form, input_shape=input_shape, output_size=num_classes)
        if not report.valid or report.head is None:
            raise ValidationFailed(
                "The model spec has problems: " + "; ".join(p.message for p in report.problems),
                suggested_action="Fix them on SCR-08 (or POST /model-specs/validate) first.",
            )
        shapes: dict[str, Shape] = {INPUT: input_shape}
        for node in report.nodes:
            assert node.shape is not None
            shapes[node.id] = Shape.from_json(node.shape)
        incoming: dict[str, list[str]] = {}
        for src, dst in form.edges:
            incoming.setdefault(dst, []).append(src)
        self.order: list[str] = [n.id for n in form.nodes]
        self.sources: dict[str, list[str]] = {i: input_order(incoming[i]) for i in self.order}
        self.last: str = incoming[OUTPUT][0]
        self.nodes = nn.ModuleDict(
            {
                n.id: build_layer(n.layer, [shapes[s] for s in self.sources[n.id]])
                for n in form.nodes
            }
        )
        self.head = nn.Linear(report.head.in_features, num_classes)
        # Drop each value once its last reader has run, so a long chain keeps one or two.
        last_use: dict[str, int] = {}
        for index, node_id in enumerate(self.order):
            for source in self.sources[node_id]:
                last_use[source] = index
        self.free_after: list[list[str]] = [[] for _ in self.order]
        for value, index in last_use.items():
            if value != self.last:
                self.free_after[index].append(value)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        values: dict[str, torch.Tensor] = {INPUT: x}
        for index, node_id in enumerate(self.order):
            module = self.nodes[node_id]
            inputs = [values[s] for s in self.sources[node_id]]
            values[node_id] = module(inputs) if isinstance(module, _Merge) else module(inputs[0])
            for done in self.free_after[index]:
                del values[done]
        return self.head(values[self.last])


def compile_graph(spec: GraphSpec, input_shape: Shape, num_classes: int) -> GraphModule:
    return GraphModule(spec, input_shape, num_classes)


# ---------------------------------------------------------------------------- layers


class _Merge(nn.Module):
    pass


class _Add(_Merge):
    def forward(self, inputs: list[torch.Tensor]) -> torch.Tensor:
        total = inputs[0]
        for value in inputs[1:]:
            total = total + value
        return total


class _Concat(_Merge):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def forward(self, inputs: list[torch.Tensor]) -> torch.Tensor:
        return torch.cat(inputs, dim=self.dim)


class _GlobalAvgPool(nn.Module):
    def __init__(self, dims: tuple[int, ...]) -> None:
        super().__init__()
        self.dims = dims

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x.mean(dim=self.dims)


class _Recurrent(nn.Module):
    """PyTorch's RNN, LSTM or GRU, batch first, returning every step or the final state."""

    def __init__(self, core: nn.Module, *, last: bool, directions: int) -> None:
        super().__init__()
        self.core, self.last, self.directions = core, last, directions

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, state = self.core(x)
        if not self.last:
            return out
        hidden = state[0] if isinstance(state, tuple) else state  # LSTM: (h, c)
        return torch.cat(list(hidden[-self.directions :]), dim=-1)


class ScratchLSTM(nn.Module):
    """A stacked, optionally bidirectional LSTM made of BUILD-1.8's cell (``implementation:
    scratch``). Same parameter count as ``torch.nn.LSTM``; one cell per layer and direction."""

    def __init__(self, input_size: int, spec: L.LSTM) -> None:
        super().__init__()
        self.directions = 2 if spec.bidirectional else 1
        self.last = spec.output == "last"
        self.dropout = nn.Dropout(spec.dropout)
        self.cells = nn.ModuleList()
        for layer in range(spec.num_layers):
            width = input_size if layer == 0 else spec.hidden_size * self.directions
            for _ in range(self.directions):
                self.cells.append(scratch.ScratchLSTMCell(width, spec.hidden_size))
        self.num_layers = spec.num_layers

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        finals: list[torch.Tensor] = []
        for layer in range(self.num_layers):
            if layer:
                x = self.dropout(x)
            outputs, finals = [], []
            for direction in range(self.directions):
                cell = self.cells[layer * self.directions + direction]
                steps = range(x.shape[1]) if direction == 0 else range(x.shape[1] - 1, -1, -1)
                state: tuple[torch.Tensor, torch.Tensor] | None = None
                seq: list[torch.Tensor] = []
                for t in steps:
                    h, c = cell(x[:, t], state)
                    state = (h, c)
                    seq.append(h)
                if direction:
                    seq.reverse()
                outputs.append(torch.stack(seq, dim=1))
                assert state is not None
                finals.append(state[0])
            x = torch.cat(outputs, dim=-1)
        return torch.cat(finals, dim=-1) if self.last else x


def build_layer(layer: Any, inputs: list[Shape]) -> nn.Module:
    """One node's module, for input shapes already checked by :func:`validate_graph`."""
    shape = inputs[0]
    match layer:
        case L.Linear():
            return nn.Linear(shape.dims[-1], layer.out_features, bias=layer.bias)
        case L.Conv2d():
            return nn.Conv2d(
                shape.dims[0],
                layer.out_channels,
                layer.kernel_size,
                stride=layer.stride,
                padding=layer.padding,
                dilation=layer.dilation,
                bias=layer.bias,
            )
        case L.MaxPool2d():
            return nn.MaxPool2d(layer.kernel_size, layer.stride or layer.kernel_size, layer.padding)
        case L.AvgPool2d():
            return nn.AvgPool2d(layer.kernel_size, layer.stride or layer.kernel_size, layer.padding)
        case L.GlobalAvgPool():
            return _GlobalAvgPool((2, 3) if shape.kind == "image" else (1,))
        case L.BatchNorm():
            norm = nn.BatchNorm2d if shape.kind == "image" else nn.BatchNorm1d
            return norm(shape.dims[0], eps=layer.eps, momentum=layer.momentum)
        case L.LayerNorm():
            return nn.LayerNorm(shape.dims[-1], eps=layer.eps)
        case L.Dropout():
            return nn.Dropout(layer.p)
        case L.Flatten():
            return nn.Flatten()
        case L.Embedding():
            assert shape.vocab is not None
            return nn.Embedding(shape.vocab, layer.embedding_dim, padding_idx=layer.padding_idx)
        case L.LSTM() if layer.implementation == "scratch":
            return ScratchLSTM(shape.dims[1], layer)
        case L.RNN() | L.LSTM() | L.GRU():
            kind = {"rnn": nn.RNN, "lstm": nn.LSTM, "gru": nn.GRU}[layer.type]
            extra = {"nonlinearity": layer.nonlinearity} if isinstance(layer, L.RNN) else {}
            core = kind(
                shape.dims[1],
                layer.hidden_size,
                num_layers=layer.num_layers,
                batch_first=True,
                bidirectional=layer.bidirectional,
                dropout=layer.dropout,
                **extra,
            )
            directions = 2 if layer.bidirectional else 1
            return _Recurrent(core, last=layer.output == "last", directions=directions)
        case L.Attention():
            return scratch.MultiHeadSelfAttention(
                shape.dims[1], layer.num_heads, dropout=layer.dropout, causal=layer.causal
            )
        case L.PositionalEncoding():
            return scratch.PositionalEncoding(
                shape.dims[1], max_len=layer.max_len, dropout=layer.dropout
            )
        case L.ReLU():
            return nn.ReLU()
        case L.LeakyReLU():
            return nn.LeakyReLU(layer.negative_slope)
        case L.GELU():
            return nn.GELU()
        case L.Tanh():
            return nn.Tanh()
        case L.Sigmoid():
            return nn.Sigmoid()
        case L.Softmax():
            return nn.Softmax(dim=-1)
        case L.Add():
            return _Add()
        case L.Concat():
            return _Concat(1 if shape.kind in ("image", "vector") else 2)
    raise TypeError(f"no module for layer type {layer.type!r}")
