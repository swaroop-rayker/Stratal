"""Deterministic stand-ins for models and the GPU, for CPU tests (ADR-0012).

- :class:`FakeCaptioner` describes an image by its size and average colour, can
  be told to run out of memory above a batch size, and records every image it
  was asked to caption, so tests can prove nothing was captioned twice.
- :class:`FakeClipEncoder` maps images and texts to vectors so that a caption
  produced by :class:`FakeCaptioner` for an image points the same way as the image.
- :class:`FakeGpu` keeps an allocation count that drops only when the fake
  weights are actually garbage-collected, so a leaked reference shows up as VRAM
  that did not return to baseline, as it would on the real card (BUG5).
"""

import hashlib
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from PIL import Image, ImageStat

from stratal.label.models import CudaOutOfMemory, ModelInfo, Vector

_COLOURS = ("red", "green", "blue", "grey")
DIMENSIONS = 8


def _colour(image: Image.Image) -> str:
    r, g, b = (int(v) for v in ImageStat.Stat(image.convert("RGB")).mean[:3])
    spread = max(r, g, b) - min(r, g, b)
    if spread < 24:
        return "grey"
    return _COLOURS[(r, g, b).index(max(r, g, b))]


def fake_caption(image: Image.Image) -> str:
    return f"a {_colour(image)} picture, {image.width} by {image.height}"


@dataclass
class FakeGpu:
    """A card of ``total`` MB. Allocations are released only by garbage collection."""

    total: float = 6141.0
    context: float = 90.0
    allocated: float = 0.0
    cached: float = 0.0
    calls: list[str] = field(default_factory=lambda: list[str]())

    def allocated_mb(self) -> float:
        return self.allocated

    def reserved_mb(self) -> float:
        return self.allocated + self.cached

    def free_mb(self) -> float:
        return self.total - self.context - self.reserved_mb()

    def total_mb(self) -> float:
        return self.total

    def synchronize(self) -> None:
        self.calls.append("synchronize")

    def empty_cache(self) -> None:
        self.calls.append("empty_cache")
        self.cached = 0.0

    def hold(self, owner: object, mb: float) -> None:
        """Count ``mb`` as allocated until ``owner`` is garbage-collected."""
        self.allocated += mb
        weakref.finalize(owner, self._release, mb)

    def _release(self, mb: float) -> None:
        self.allocated -= mb
        self.cached += mb  # freed tensors stay in PyTorch's cache until empty_cache


class FakeWeights:
    """Stands for a model's tensors on the card."""

    def __init__(self, gpu: FakeGpu, mb: float) -> None:
        gpu.hold(self, mb)


class FakeCaptioner:
    def __init__(
        self,
        *,
        gpu: FakeGpu | None = None,
        weights_mb: float = 250.0,
        oom_above: int | None = None,
        info: ModelInfo | None = None,
    ) -> None:
        self._info = info or ModelInfo("fake-captioner", "0" * 40, "Fake captioner", "fp16")
        self._weights: FakeWeights | None = FakeWeights(gpu, weights_mb) if gpu else None
        self.oom_above = oom_above
        self.batch_sizes: list[int] = []
        self.seen: list[str] = []
        """``fake_caption`` of every image captioned successfully, in order."""

    @property
    def info(self) -> ModelInfo:
        return self._info

    def caption(self, images: Sequence[Image.Image]) -> list[str]:
        self.batch_sizes.append(len(images))
        if self.oom_above is not None and len(images) > self.oom_above:
            raise CudaOutOfMemory(len(images), "fake: batch larger than the card allows")
        captions = [fake_caption(image) for image in images]
        self.seen.extend(captions)
        return captions

    def close(self) -> None:
        self._weights = None


def _text_vector(text: str) -> Vector:
    for index, colour in enumerate(_COLOURS):
        if f"a {colour} picture" in text:
            return [3.0 if i == index else 0.0 for i in range(DIMENSIONS)]
    digest = hashlib.sha256(text.encode()).digest()
    return [(byte - 128) / 128 for byte in digest[:DIMENSIONS]]


class FakeClipEncoder:
    """Images point along their colour's axis; a matching fake caption does too."""

    def __init__(self, *, gpu: FakeGpu | None = None, weights_mb: float = 300.0) -> None:
        self._info = ModelInfo("fake-clip", "1" * 40, "Fake CLIP", "fp16")
        self._weights: FakeWeights | None = FakeWeights(gpu, weights_mb) if gpu else None
        self.image_batches: list[int] = []

    @property
    def info(self) -> ModelInfo:
        return self._info

    def encode_images(self, images: Sequence[Image.Image]) -> list[Vector]:
        self.image_batches.append(len(images))
        vectors: list[Vector] = []
        for image in images:
            index = _COLOURS.index(_colour(image))
            vectors.append([2.0 if i == index else 0.1 for i in range(DIMENSIONS)])
        return vectors

    def encode_texts(self, texts: Sequence[str]) -> list[Vector]:
        return [_text_vector(text) for text in texts]

    def close(self) -> None:
        self._weights = None


class ConstantSimilarity:
    """Stand-in for the owner's CLIP similarity: the same score for every pair.

    Deliberately not a real similarity, so it gives nothing of LABEL-1.3 away.
    Tests of the stage around it only need a score per pair.
    """

    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.calls = 0

    def __call__(
        self, image_vectors: Sequence[Vector], text_vectors: Sequence[Vector]
    ) -> list[float]:
        self.calls += 1
        return [self.value] * len(image_vectors)


def filter_nothing(items: Sequence[object], *, threshold: float) -> dict[str, str]:
    """Stand-in for the owner's filter rule: marks no row."""
    return {}


class ScriptedFilter:
    """Stand-in for the owner's filter rule that marks the rows it was given."""

    def __init__(self, marks: Mapping[str, str]) -> None:
        self.marks = dict(marks)

    def __call__(self, items: Sequence[object], *, threshold: float) -> dict[str, str]:
        return dict(self.marks)
