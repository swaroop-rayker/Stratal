"""What labeling needs from a model and from the GPU, without importing PyTorch.

The owner's units (caption loop, CLIP similarity, VRAM manager) are written
against these protocols, so they type-check in strict mode and run in CPU tests
with the fakes in :mod:`stratal.testing.fake_models`. The real implementations
are in :mod:`stratal.label.hf`, the one module that imports torch.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

Vector = list[float]


@dataclass(frozen=True)
class ModelInfo:
    """Which model produced a value: written next to it in the manifest (LABEL-1.1.AC1)."""

    name: str
    """Allow-list name, e.g. ``florence-2-base``."""
    revision: str
    """Full commit hash of the weights."""
    display: str
    """For messages, e.g. ``Florence-2-base``."""
    dtype: str
    """``fp16`` or ``fp32``."""


class CudaOutOfMemory(Exception):
    """One model call ran out of VRAM. Nothing was kept from it; the caller may retry smaller.

    Adapters raise this instead of PyTorch's own error, after releasing what the
    failed call allocated, so code above them never needs to import torch.
    """

    def __init__(self, batch_size: int, detail: str) -> None:
        super().__init__(f"out of VRAM with a batch of {batch_size}: {detail}")
        self.batch_size = batch_size
        self.detail = detail


class GpuModel(Protocol):
    @property
    def info(self) -> ModelInfo: ...

    def close(self) -> None:
        """Drop this object's references to the weights. Calling it twice is harmless."""
        ...


class Captioner(GpuModel, Protocol):
    def caption(self, images: Sequence[Image.Image]) -> list[str]:
        """One caption per image, in order. Raises :class:`CudaOutOfMemory`."""
        ...


class ClipEncoder(GpuModel, Protocol):
    def encode_images(self, images: Sequence[Image.Image]) -> list[Vector]:
        """One embedding per image, not normalised. Raises :class:`CudaOutOfMemory`."""
        ...

    def encode_texts(self, texts: Sequence[str]) -> list[Vector]:
        """One embedding per text (truncated to the model's 77 tokens), not normalised."""
        ...


class GpuBackend(Protocol):
    """The GPU as the VRAM manager sees it (ADR-0006). All figures in MB."""

    def allocated_mb(self) -> float:
        """Held by live tensors (``torch.cuda.memory_allocated``)."""
        ...

    def reserved_mb(self) -> float:
        """Held by PyTorch's caching allocator, live or not (``memory_reserved``)."""
        ...

    def free_mb(self) -> float:
        """Free on the card as the driver reports it (``mem_get_info``)."""
        ...

    def total_mb(self) -> float: ...

    def synchronize(self) -> None:
        """Wait until queued GPU work has finished (E-008: measure after, not before)."""
        ...

    def empty_cache(self) -> None:
        """Give cached, unused blocks back to the driver."""
        ...
