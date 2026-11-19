# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Reference models, built from a :class:`~stratal.train.config.ModelSpec` (ADR-0014 rule 2).

Until the model builder exists (M9), training runs these two models. Both are
small on purpose: CIFAR-10's 32-px images, a 6 GB card shared with everything
else, and a CPU fallback that should still be usable (RUN-1.9).
"""

from torch import nn

from stratal.train.config import ModelSpec


def _mlp(input_shape: tuple[int, int, int], num_classes: int) -> nn.Module:
    channels, height, width = input_shape
    return nn.Sequential(
        nn.Flatten(),
        nn.Linear(channels * height * width, 256),
        nn.ReLU(),
        nn.Dropout(0.2),
        nn.Linear(256, 128),
        nn.ReLU(),
        nn.Linear(128, num_classes),
    )


def _block(c_in: int, c_out: int) -> list[nn.Module]:
    """Two 3×3 convolutions with batch norm and ReLU, then a 2×2 max pool."""
    return [
        nn.Conv2d(c_in, c_out, 3, padding=1, bias=False),
        nn.BatchNorm2d(c_out),
        nn.ReLU(),
        nn.Conv2d(c_out, c_out, 3, padding=1, bias=False),
        nn.BatchNorm2d(c_out),
        nn.ReLU(),
        nn.MaxPool2d(2),
    ]


def _small_cnn(input_shape: tuple[int, int, int], num_classes: int) -> nn.Module:
    channels = input_shape[0]
    return nn.Sequential(
        *_block(channels, 32),
        *_block(32, 64),
        *_block(64, 128),
        nn.AdaptiveAvgPool2d(1),
        nn.Flatten(),
        nn.Linear(128, num_classes),
    )


_BUILDERS = {"mlp": _mlp, "small_cnn": _small_cnn}


def build_model(spec: ModelSpec, input_shape: tuple[int, int, int], num_classes: int) -> nn.Module:
    """A new, randomly initialised model for images of ``input_shape`` (C, H, W).

    A builder graph compiles through :mod:`stratal.build.compile` (ADR-0017 rule 8)."""
    if spec.graph is not None:
        from stratal.build.compile import compile_graph
        from stratal.build.shapes import Shape

        return compile_graph(spec.graph, Shape("image", tuple(input_shape)), num_classes)
    return _BUILDERS[spec.name](input_shape, num_classes)


def parameter_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
