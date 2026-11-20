"""Layer specs: one Pydantic model per layer type (BUILD-1.1, BUILD-1.2, ADR-0017 rule 3).

Each model is data the user edits (its fields, with ranges, defaults and help
text that become SCR-08's form, UI-2.3) plus two things the builder needs and
PyTorch would otherwise have to tell us, computed here in plain Python:

- ``apply(inputs)``: the output shape for the given input shapes, or a
  :class:`~stratal.build.shapes.SpecProblem` that says in words why not, with a
  stable code (BUILD-1.4, BUILD-1.6);
- the parameter count of the layer that ``stratal.build.compile`` will create,
  equal to ``sum(p.numel())`` (BUILD-1.5; a parity test checks it against PyTorch).

Sizes that follow from the input (``in_features``, ``in_channels``, the
vocabulary...) are inferred. ``in_features`` and ``in_channels`` may also be
given; a value that disagrees with the input is a ``shape_mismatch``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from stratal.build.shapes import Shape, SpecProblem
from stratal.data.dataset_types import Family

__all__ = ["Family"]


@dataclass(frozen=True)
class Applied:
    shape: Shape
    parameters: int


class _Layer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    merges: ClassVar[bool] = False
    """Only ``add`` and ``concat`` take more than one input (ADR-0017 rule 1)."""

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        raise NotImplementedError

    def family(self) -> Family | None:
        """The model family this layer makes a graph part of, if any (ADR-0017 rule 5)."""
        return None


def _one(inputs: Sequence[Shape]) -> Shape:
    if len(inputs) != 1:  # the graph checks arity first; this guards direct calls
        raise SpecProblem("too_many_inputs", f"takes one input, got {len(inputs)}")
    return inputs[0]


def _float(shape: Shape, what: str) -> None:
    if shape.kind == "tokens":
        raise SpecProblem(
            "tokens_need_embedding",
            f"{what} needs numbers, but its input is integer tokens: add an embedding first",
        )


def _kinds(shape: Shape, allowed: tuple[str, ...], what: str, hint: str) -> None:
    if shape.kind not in allowed:
        raise SpecProblem(
            "rank_mismatch",
            f"{what} takes {' or '.join(allowed)} input, got {shape.describe()}: {hint}",
        )


# ---------------------------------------------------------------------------- dense


class Linear(_Layer):
    type: Literal["linear"] = "linear"
    out_features: int = Field(default=128, ge=1, le=65_536, description="Output features.")
    bias: bool = Field(default=True, description="Learn an additive bias.")
    in_features: int | None = Field(
        default=None,
        ge=1,
        le=1_048_576,
        description="Input features. Leave empty to take them from the input; a value "
        "that disagrees with the input is an error (BUILD-1.4).",
    )

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "A linear layer")
        _kinds(shape, ("vector", "sequence"), "A linear layer", "flatten or pool first")
        width = shape.dims[-1]
        if self.in_features is not None and self.in_features != width:
            raise SpecProblem(
                "shape_mismatch",
                f"in_features is {self.in_features}, but its input has {width} features",
            )
        out = Shape(shape.kind, (*shape.dims[:-1], self.out_features))
        return Applied(out, width * self.out_features + (self.out_features if self.bias else 0))


# ---------------------------------------------------------------------------- convolution


def _spatial(size: int, kernel: int, stride: int, padding: int, dilation: int = 1) -> int:
    """PyTorch's output length for a convolution or pooling window (floor mode)."""
    return (size + 2 * padding - dilation * (kernel - 1) - 1) // stride + 1


def _below_one(shape: Shape, height: int, width: int, what: str) -> None:
    if height < 1 or width < 1:
        raise SpecProblem(
            "below_one_pixel",
            f"{what} would shrink {shape.dims[1]}×{shape.dims[2]} below 1×1 "
            f"(to {height}×{width}): use a smaller kernel or stride, padding, or fewer "
            "downsampling layers (BUILD-1.6)",
        )


class Conv2d(_Layer):
    type: Literal["conv2d"] = "conv2d"
    out_channels: int = Field(default=32, ge=1, le=4096, description="Output channels.")
    kernel_size: int = Field(default=3, ge=1, le=15, description="Square kernel, in pixels.")
    stride: int = Field(default=1, ge=1, le=8, description="Step between windows.")
    padding: int = Field(default=1, ge=0, le=15, description="Zero padding on each side.")
    dilation: int = Field(default=1, ge=1, le=8, description="Spacing between kernel taps.")
    bias: bool = Field(default=True, description="Learn an additive bias per channel.")
    in_channels: int | None = Field(
        default=None,
        ge=1,
        le=4096,
        description="Input channels. Leave empty to take them from the input.",
    )

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "A convolution")
        _kinds(shape, ("image",), "A convolution", "it works on images (channels × H × W)")
        channels, height, width = shape.dims
        if self.in_channels is not None and self.in_channels != channels:
            raise SpecProblem(
                "shape_mismatch",
                f"in_channels is {self.in_channels}, but its input has {channels} channels",
            )
        args = (self.kernel_size, self.stride, self.padding, self.dilation)
        h, w = _spatial(height, *args), _spatial(width, *args)
        _below_one(shape, h, w, "This convolution")
        weights = self.out_channels * channels * self.kernel_size**2
        return Applied(
            Shape("image", (self.out_channels, h, w)),
            weights + (self.out_channels if self.bias else 0),
        )

    def family(self) -> Family | None:
        return "cnn"


class _Pool(_Layer):
    kernel_size: int = Field(default=2, ge=1, le=15, description="Square window, in pixels.")
    stride: int | None = Field(
        default=None, ge=1, le=15, description="Step between windows; empty means the window."
    )
    padding: int = Field(
        default=0, ge=0, le=7, description="Padding on each side; at most half the window."
    )

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Pooling")
        _kinds(shape, ("image",), "Pooling", "it works on images")
        if 2 * self.padding > self.kernel_size:
            raise SpecProblem(
                "invalid_padding",
                f"padding {self.padding} is more than half the window {self.kernel_size}",
            )
        channels, height, width = shape.dims
        stride = self.stride or self.kernel_size
        h = _spatial(height, self.kernel_size, stride, self.padding)
        w = _spatial(width, self.kernel_size, stride, self.padding)
        _below_one(shape, h, w, "This pooling")
        return Applied(Shape("image", (channels, h, w)), 0)


class MaxPool2d(_Pool):
    type: Literal["max_pool2d"] = "max_pool2d"


class AvgPool2d(_Pool):
    type: Literal["avg_pool2d"] = "avg_pool2d"


class GlobalAvgPool(_Layer):
    """Mean over the spatial axes of an image, or over the positions of a sequence."""

    type: Literal["global_avg_pool"] = "global_avg_pool"

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Global average pooling")
        _kinds(shape, ("image", "sequence"), "Global average pooling", "nothing to pool")
        features = shape.dims[0] if shape.kind == "image" else shape.dims[1]
        return Applied(Shape("vector", (features,)), 0)


# ---------------------------------------------------------------------------- normalisation


class BatchNorm(_Layer):
    """Batch normalisation: over channels for an image, over features for a vector."""

    type: Literal["batch_norm"] = "batch_norm"
    momentum: float = Field(default=0.1, gt=0, le=1, description="Running-average momentum.")
    eps: float = Field(default=1e-5, gt=0, lt=1, description="Added to the variance.")

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Batch normalisation")
        _kinds(shape, ("image", "vector"), "Batch normalisation", "use layer_norm for sequences")
        return Applied(shape, 2 * shape.dims[0])


class LayerNorm(_Layer):
    """Layer normalisation over the last axis (features)."""

    type: Literal["layer_norm"] = "layer_norm"
    eps: float = Field(default=1e-5, gt=0, lt=1, description="Added to the variance.")

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Layer normalisation")
        _kinds(shape, ("vector", "sequence"), "Layer normalisation", "use batch_norm for images")
        return Applied(shape, 2 * shape.dims[-1])


class Dropout(_Layer):
    type: Literal["dropout"] = "dropout"
    p: float = Field(default=0.5, ge=0, lt=1, description="Share of values zeroed in training.")

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Dropout")
        return Applied(shape, 0)


class Flatten(_Layer):
    type: Literal["flatten"] = "flatten"

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Flatten")
        return Applied(Shape("vector", (shape.elements,)), 0)


# ---------------------------------------------------------------------------- sequences


class Embedding(_Layer):
    type: Literal["embedding"] = "embedding"
    embedding_dim: int = Field(default=64, ge=1, le=4096, description="Features per token.")
    padding_idx: int | None = Field(
        default=None, ge=0, description="A token ID whose vector stays zero, e.g. padding."
    )

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        if shape.kind != "tokens" or shape.vocab is None:
            raise SpecProblem(
                "rank_mismatch", f"An embedding takes integer tokens, got {shape.describe()}"
            )
        if self.padding_idx is not None and self.padding_idx >= shape.vocab:
            raise SpecProblem(
                "invalid_padding_idx",
                f"padding_idx {self.padding_idx} is outside the vocabulary of {shape.vocab:,}",
            )
        return Applied(
            Shape("sequence", (shape.dims[0], self.embedding_dim)),
            shape.vocab * self.embedding_dim,
        )


class _Recurrent(_Layer):
    hidden_size: int = Field(default=128, ge=1, le=4096, description="Features per direction.")
    num_layers: int = Field(default=1, ge=1, le=8, description="Stacked recurrent layers.")
    bidirectional: bool = Field(default=False, description="Also read the sequence backwards.")
    dropout: float = Field(
        default=0.0, ge=0, lt=1, description="Dropout between stacked layers (needs 2 or more)."
    )
    output: Literal["sequence", "last"] = Field(
        default="last",
        description="'last': the final step's features, a vector; 'sequence': every step's.",
    )

    gates: ClassVar[int] = 1

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "A recurrent layer")
        _kinds(shape, ("sequence",), "A recurrent layer", "embed tokens or use a sequence")
        if self.dropout > 0 and self.num_layers == 1:
            raise SpecProblem(
                "dropout_needs_layers",
                "dropout applies between stacked layers, so it needs num_layers of 2 or more",
            )
        directions = 2 if self.bidirectional else 1
        size, hidden = shape.dims[1], self.hidden_size
        parameters = 0
        for layer in range(self.num_layers):
            width = size if layer == 0 else hidden * directions
            per_direction = self.gates * (hidden * width + hidden * hidden + 2 * hidden)
            parameters += directions * per_direction
        features = hidden * directions
        out = (
            Shape("sequence", (shape.dims[0], features))
            if self.output == "sequence"
            else Shape("vector", (features,))
        )
        return Applied(out, parameters)

    def family(self) -> Family | None:
        return "rnn"


class RNN(_Recurrent):
    type: Literal["rnn"] = "rnn"
    nonlinearity: Literal["tanh", "relu"] = Field(default="tanh", description="Activation.")


class LSTM(_Recurrent):
    type: Literal["lstm"] = "lstm"
    implementation: Literal["torch", "scratch"] = Field(
        default="torch",
        description="'torch': PyTorch's LSTM; 'scratch': the from-scratch cell (BUILD-1.8).",
    )
    gates: ClassVar[int] = 4


class GRU(_Recurrent):
    type: Literal["gru"] = "gru"
    gates: ClassVar[int] = 3


class Attention(_Layer):
    """Multi-head self-attention, from scratch (BUILD-1.8)."""

    type: Literal["attention"] = "attention"
    num_heads: int = Field(default=4, ge=1, le=64, description="Heads; must divide the features.")
    dropout: float = Field(default=0.0, ge=0, lt=1, description="Dropout on attention weights.")
    causal: bool = Field(default=False, description="Each position sees only earlier ones.")

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Attention")
        _kinds(shape, ("sequence",), "Attention", "embed tokens first")
        width = shape.dims[1]
        if width % self.num_heads:
            raise SpecProblem(
                "heads_do_not_divide",
                f"{width} features do not split into {self.num_heads} heads "
                "(d_model % num_heads must be 0, BUILD-1.6)",
            )
        return Applied(shape, 4 * width * width + 4 * width)

    def family(self) -> Family | None:
        return "transformer"


class PositionalEncoding(_Layer):
    """Sinusoidal position information added to a sequence (BUILD-1.8). No parameters."""

    type: Literal["positional_encoding"] = "positional_encoding"
    max_len: int = Field(default=512, ge=1, le=8192, description="Longest sequence it encodes.")
    dropout: float = Field(default=0.0, ge=0, lt=1, description="Dropout after adding.")

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "Positional encoding")
        _kinds(shape, ("sequence",), "Positional encoding", "embed tokens first")
        length, width = shape.dims
        if width % 2:
            raise SpecProblem(
                "odd_model_dim", f"positional encoding needs an even width, got {width}"
            )
        if length > self.max_len:
            raise SpecProblem(
                "sequence_too_long",
                f"the sequence has {length} positions; max_len is {self.max_len}",
            )
        return Applied(shape, 0)


# ---------------------------------------------------------------------------- activations


class _Activation(_Layer):
    def apply(self, inputs: Sequence[Shape]) -> Applied:
        shape = _one(inputs)
        _float(shape, "An activation")
        return Applied(shape, 0)


class ReLU(_Activation):
    type: Literal["relu"] = "relu"


class LeakyReLU(_Activation):
    type: Literal["leaky_relu"] = "leaky_relu"
    negative_slope: float = Field(default=0.01, ge=0, le=1, description="Slope below zero.")


class GELU(_Activation):
    type: Literal["gelu"] = "gelu"


class Tanh(_Activation):
    type: Literal["tanh"] = "tanh"


class Sigmoid(_Activation):
    type: Literal["sigmoid"] = "sigmoid"


class Softmax(_Activation):
    """Softmax over the last axis."""

    type: Literal["softmax"] = "softmax"


# ---------------------------------------------------------------------------- merges


def _merged(inputs: Sequence[Shape], what: str) -> None:
    if len(inputs) < 2:
        raise SpecProblem("merge_needs_two", f"{what} joins two or more inputs, got {len(inputs)}")
    for shape in inputs:
        _float(shape, what)
    if len({s.kind for s in inputs}) > 1:
        kinds = ", ".join(s.describe() for s in inputs)
        raise SpecProblem("merge_shape_mismatch", f"{what} needs inputs of one kind, got {kinds}")


class Add(_Layer):
    """Element-wise sum of its inputs, e.g. a residual connection (BUILD-1.9)."""

    type: Literal["add"] = "add"
    merges: ClassVar[bool] = True

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        _merged(inputs, "Add")
        if len({s.dims for s in inputs}) > 1:
            shapes = ", ".join(s.describe() for s in inputs)
            raise SpecProblem("merge_shape_mismatch", f"Add needs equal shapes, got {shapes}")
        return Applied(inputs[0], 0)


class Concat(_Layer):
    """Join inputs along the channel axis of images, the feature axis otherwise."""

    type: Literal["concat"] = "concat"
    merges: ClassVar[bool] = True

    def apply(self, inputs: Sequence[Shape]) -> Applied:
        _merged(inputs, "Concat")
        axis = 0 if inputs[0].kind in ("image", "vector") else 1
        rest = {tuple(d for i, d in enumerate(s.dims) if i != axis) for s in inputs}
        if len(rest) > 1:
            shapes = ", ".join(s.describe() for s in inputs)
            raise SpecProblem(
                "merge_shape_mismatch",
                f"Concat needs inputs equal except on the joined axis, got {shapes}",
            )
        dims = list(inputs[0].dims)
        dims[axis] = sum(s.dims[axis] for s in inputs)
        return Applied(Shape(inputs[0].kind, tuple(dims)), 0)


Layer = Annotated[
    Linear
    | Conv2d
    | MaxPool2d
    | AvgPool2d
    | GlobalAvgPool
    | BatchNorm
    | LayerNorm
    | Dropout
    | Flatten
    | Embedding
    | RNN
    | LSTM
    | GRU
    | Attention
    | PositionalEncoding
    | ReLU
    | LeakyReLU
    | GELU
    | Tanh
    | Sigmoid
    | Softmax
    | Add
    | Concat,
    Field(discriminator="type"),
]

LAYER_TYPES: tuple[type[_Layer], ...] = (
    Linear,
    Conv2d,
    MaxPool2d,
    AvgPool2d,
    GlobalAvgPool,
    BatchNorm,
    LayerNorm,
    Dropout,
    Flatten,
    Embedding,
    RNN,
    LSTM,
    GRU,
    Attention,
    PositionalEncoding,
    ReLU,
    LeakyReLU,
    GELU,
    Tanh,
    Sigmoid,
    Softmax,
    Add,
    Concat,
)


def head_parameters(features: int, outputs: int) -> int:
    """The locked output head: a linear layer from the graph's last vector (ADR-0017 rule 5)."""
    return features * outputs + outputs
