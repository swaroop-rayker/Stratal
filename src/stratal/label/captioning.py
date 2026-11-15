"""The caption batching loop (LABEL-1.1, LABEL-1.2, BUG2). An owner unit (ADR-0012).

The label stage (:mod:`stratal.label.stage`) does everything around it: it finds
the accepted rows, looks each preprocessed image up in the derivation cache,
loads the captioner through the VRAM manager, and passes only the images that
have no caption yet. This function captions them, in batches, and hands each
finished batch back through ``commit``, which stores it in the cache. That is
what makes a killed stage resume without redoing work (LABEL-1.2): next time the
stage passes only what was not committed.

Tests: ``tests/unit/test_LABEL_1_1_caption_loop.py`` (strict expected failures
until this is written). Fakes: :class:`stratal.testing.fake_models.FakeCaptioner`.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PIL import Image

from stratal.label.models import Captioner
from stratal.mon.sink import EventSink

ALGORITHM_VERSION = 1
"""Bump when a change here can change a caption; it is part of the cache key."""


@dataclass(frozen=True)
class CaptionItem:
    key: str
    """SHA-256 of the preprocessed image: the cache key, and what ``commit`` is keyed by."""
    row_key: str
    """The manifest row, for messages only."""


@dataclass(frozen=True)
class CaptionReport:
    captioned: int
    """Images captioned and committed by this call."""
    batches: int
    """Successful calls to ``captioner.caption``."""
    final_batch_size: int
    """The batch size in use at the end, after any reductions."""
    oom_retries: int
    """How many times a batch ran out of VRAM and was retried smaller."""


OpenImage = Callable[[CaptionItem], Image.Image]
Commit = Callable[[Mapping[str, str]], None]


def caption_items(
    items: Sequence[CaptionItem],
    *,
    captioner: Captioner,
    batch_size: int,
    open_image: OpenImage,
    commit: Commit,
    sink: EventSink,
    checkpoint: Callable[[], None],
    stage: str = "label",
) -> CaptionReport:
    """Caption every item, in batches, committing each batch as soon as it is done.

    Must satisfy:

    1. Every item is captioned exactly once, by ``captioner.caption``, in batches
       of at most ``batch_size`` images, in the order given (LABEL-1.1).
    2. As soon as a batch succeeds, ``commit`` is called with ``{item.key:
       caption}`` for exactly that batch, before the next batch starts. Nothing
       is committed for a batch that failed (LABEL-1.2).
    3. ``checkpoint()`` is called before each batch. It raises when the job must
       stop (cancel, or a test's simulated kill); let that propagate untouched.
    4. Images are opened with ``open_image`` one batch at a time and not kept
       afterwards: at most one batch of images is in memory (NFR-2).
    5. After each batch an INFO event is emitted with ``step`` (batches done) and
       ``total`` (batches planned) and a message of the form
       ``"Captioning batch 42/313 · Florence-2-base · bs=16 · fp16"`` (MON-1.2,
       MON-3.1), using ``captioner.info.display`` and ``.dtype``, with metrics
       ``images_per_s`` and ``batch_size``.
    6. When ``captioner.caption`` raises :class:`~stratal.label.models.CudaOutOfMemory`
       (BUG2, RUN-1.5): use a smaller batch and retry the same images. Emit a
       WARN event whose ``reason`` says the batch ran out of VRAM and whose
       ``suggested_action`` names the new size. Never skip an image. If a batch
       of one image fails, raise :class:`~stratal.core.errors.GpuOom` with a
       ``suggested_action``.
    7. If the captioner returns a different number of captions than images, raise
       :class:`~stratal.core.errors.StratalError` naming the model: that is a
       broken adapter, not something to work around.
    8. ``batch_size`` below 1 is a ``ValueError``. An empty ``items`` returns a
       report of zeros without calling the captioner.

    Left to you: how much smaller (halving is the usual choice), whether a
    reduced size grows again after some successes, and how ``total`` is
    recomputed when the batch size changes.
    """
    raise NotImplementedError("Owner unit LABEL-1.1/LABEL-1.2: caption_items")
