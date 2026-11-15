"""The caption batching loop (LABEL-1.1, LABEL-1.2, BUG2).

An owner unit (ADR-0012), written by the agent at the owner's request on
2026-11-15. How and why it works is explained in ``docs/learning/sys-design.md``.

The label stage (:mod:`stratal.label.stage`) does everything around it: it finds
the accepted rows, looks each preprocessed image up in the derivation cache,
loads the captioner through the VRAM manager, and passes only the images that
have no caption yet. This function captions them, in batches, and hands each
finished batch back through ``commit``, which stores it in the cache. That is
what makes a killed stage resume without redoing work (LABEL-1.2): next time the
stage passes only what was not committed.

Tests: ``tests/unit/test_LABEL_1_1_caption_loop.py``,
``tests/unit/test_LABEL_M4_unit_decisions.py`` and, on the card,
``tests/gpu/test_BUG5_vram.py``. Fakes: :class:`stratal.testing.fake_models.FakeCaptioner`.
"""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PIL import Image

from stratal.core.errors import GpuOom, StratalError
from stratal.label.models import Captioner, CudaOutOfMemory
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

    The choices the contract left open:

    - **Halve on out-of-memory**, down to 1. Halving finds a size that fits in
      at most log2(batch_size) retries, and each retry repeats only the images
      of the batch that failed, never anything already committed.
    - **Never grow back.** Every image is a preprocessed copy of at most 768 px
      (DATA-6.1), so a batch that ran out of VRAM once would very likely do so
      again; growing back would buy little speed and pay one out-of-memory
      error each time. The reduced size is reported, so the next run can start
      there (``final_batch_size``).
    - **``total`` is recomputed** after every batch as the batches done plus the
      remaining images at the current size, so it grows when the size shrinks
      and the progress bar stays truthful.
    - Images of a failed batch are opened again for the retry, rather than
      kept, so rule 4 holds even while retrying.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be at least 1, got {batch_size}")
    if not items:
        return CaptionReport(captioned=0, batches=0, final_batch_size=batch_size, oom_retries=0)
    info = captioner.info
    size = batch_size
    position = batches = retries = 0
    started = time.monotonic()
    sink.emit(
        stage=stage,
        message=f"Captioning {len(items):,} images · {info.display} · bs={size} · {info.dtype}",
        metrics={"images": len(items), "batch_size": size},
    )
    while position < len(items):
        checkpoint()
        batch = items[position : position + size]
        images = [open_image(item) for item in batch]
        batch_started = time.monotonic()
        try:
            captions = captioner.caption(images)
        except CudaOutOfMemory as exc:
            del images
            if len(batch) == 1:
                raise GpuOom(
                    f"{info.display} ran out of VRAM on a single image ({exc.detail}).",
                    suggested_action="Free the GPU (close other programs that use it), or lower "
                    "stages.label.num_beams or max_new_tokens.",
                ) from None
            smaller = max(1, len(batch) // 2)
            retries += 1
            sink.emit(
                stage=stage,
                level="WARN",
                message=f"Out of VRAM with a batch of {len(batch)}; retrying with {smaller}",
                reason=f"PyTorch ran out of VRAM captioning {len(batch)} images at once "
                f"({exc.detail}).",
                suggested_action=f"Nothing to do: the stage continues at bs={smaller}. "
                f"Set stages.label.caption_batch_size to {smaller} to skip this retry next time.",
                metrics={"batch_size": smaller, "oom_retries": retries},
            )
            size = smaller
            continue
        if len(captions) != len(batch):
            raise StratalError(
                f"{info.display} returned {len(captions)} captions for {len(batch)} images.",
                suggested_action="The captioner adapter is broken; check stratal.label.hf.",
            )
        commit({item.key: caption for item, caption in zip(batch, captions, strict=True)})
        del images
        position += len(batch)
        batches += 1
        total = batches + math.ceil((len(items) - position) / size)
        elapsed = time.monotonic() - started
        sink.emit(
            stage=stage,
            message=f"Captioning batch {batches}/{total} · {info.display}"
            f" · bs={size} · {info.dtype}",
            step=batches,
            total=total,
            metrics={
                "images_per_s": round(position / max(elapsed, 1e-9), 2),
                "batch_size": size,
                "batch_latency_s": round(time.monotonic() - batch_started, 3),
                "captioned": position,
            },
        )
    return CaptionReport(
        captioned=position, batches=batches, final_batch_size=size, oom_retries=retries
    )
