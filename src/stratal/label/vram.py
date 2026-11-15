"""The VRAM manager, v1 (RUN-1.4, RUN-1.5, BUG5, ADR-0006).

An owner unit (ADR-0012), written by the agent at the owner's request on
2026-11-15. How and why it works is explained in ``docs/learning/sys-design.md``.

The label stage loads one model at a time through this class and unloads it
before loading the next (the captioner, then CLIP). Job processes also exit at
the end of the stage, which frees everything (ADR-0006 rule 1); the manager's
unload check is what proves a long-lived process could safely reuse the card.

It talks to the GPU only through :class:`~stratal.label.models.GpuBackend`:
:class:`stratal.label.hf.TorchGpu` on the card, and
:class:`stratal.testing.fake_models.FakeGpu` in CPU tests, whose memory comes
back only when the fake weights are really garbage-collected.

Tests: ``tests/unit/test_RUN_1_4_vram_manager.py`` (CPU) and
``tests/gpu/test_BUG5_vram.py`` (the real card).
"""

import gc
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from stratal.core.errors import VramInsufficient
from stratal.label.models import GpuBackend, GpuModel
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink

TOLERANCE_MB = 1.0
"""How far above its baseline allocated memory may stay after an unload and still count as freed.

Measured on the RTX 4050 on 2026-11-15: after Florence-2 captioned a batch and
CLIP encoded one, close, garbage collection and ``empty_cache`` brought
``memory_allocated`` back to exactly its baseline, cuBLAS workspaces included.
So the tolerance only absorbs rounding; the smallest real leak, one layer's
weights, is many megabytes.
"""


@dataclass(frozen=True)
class VramReading:
    allocated_mb: float
    reserved_mb: float
    free_mb: float


@dataclass(frozen=True)
class UnloadReport:
    name: str
    before: VramReading
    """Just before unloading."""
    after: VramReading
    """After unloading and emptying the cache."""
    baseline_allocated_mb: float
    """Allocated memory measured just before the model was loaded."""
    returned_to_baseline: bool


class VramManager:
    """Keeps at most one model on the GPU and proves it is gone after unloading.

    Must satisfy:

    1. ``load(loader, name=..., estimate_mb=...)`` measures the baseline, refuses
       with :class:`~stratal.core.errors.VramInsufficient` (and a
       ``suggested_action``) if the card's free memory is below ``estimate_mb``,
       otherwise calls ``loader()`` and returns the model (ADR-0006 rule 4).
    2. At most one model at a time (RUN-1.4): ``load`` while a model is loaded
       raises ``RuntimeError`` without calling ``loader``.
    3. ``load`` emits an INFO event before (``"Loading Florence-2-base to GPU (0.9
       GB)"``, the estimate in GiB to one decimal) and after (with the time
       taken). The after-event's metrics are ``allocated_mb_before``,
       ``allocated_mb_after``, ``reserved_mb_before`` and ``reserved_mb_after``
       (MON-3.3). ``name`` is what the messages call the model.
    4. ``unload()`` calls the model's ``close()``, drops every reference the
       manager holds, collects garbage, synchronises, empties PyTorch's cache, and
       measures again. ``returned_to_baseline`` is true when allocated memory is
       back to the baseline, within a tolerance you choose and document (RUN-1.4.AC1).
    5. ``unload`` emits ``"Unloading <name>, freeing VRAM"`` and a result event.
       If memory did not return, it emits a WARN whose reason says how many MB
       are still held and whose action says the process will be restarted, and
       still returns the report (BUG5). It does not raise for that.
    6. ``unload`` with nothing loaded raises ``RuntimeError``.
    7. ``loaded`` is the name of the model on the card, or ``None``.

    The caller promises not to keep references to the model after ``unload``;
    one surviving reference is exactly the failure behind BUG5, and the check in
    rule 4 is there to catch it.
    """

    def __init__(self, gpu: GpuBackend, *, sink: EventSink, stage: str = "label") -> None:
        self._gpu = gpu
        self._sink = sink
        self._stage = stage
        self._model: GpuModel | None = None
        self._name: str | None = None
        self._baseline: VramReading | None = None

    @property
    def loaded(self) -> str | None:
        return self._name

    def _read(self) -> VramReading:
        """Measure after queued GPU work has finished (E-008)."""
        self._gpu.synchronize()
        return VramReading(self._gpu.allocated_mb(), self._gpu.reserved_mb(), self._gpu.free_mb())

    def _release_cached(self) -> None:
        gc.collect()
        self._gpu.synchronize()
        self._gpu.empty_cache()

    def load[M: GpuModel](self, loader: Callable[[], M], *, name: str, estimate_mb: float) -> M:
        if self._model is not None:
            raise RuntimeError(
                f"{self._name} is still on the GPU; unload it before loading {name} (RUN-1.4)."
            )
        if not math.isfinite(estimate_mb) or estimate_mb < 0:
            raise ValueError(f"estimate_mb must be a finite number of MB, got {estimate_mb!r}")
        # Blocks this process cached earlier are free for the new model; give them
        # back first, so the free figure and the baseline are both honest.
        self._release_cached()
        before = self._read()
        if before.free_mb < estimate_mb:
            raise VramInsufficient(
                f"Loading {name} needs about {estimate_mb / 1024:.1f} GB of free VRAM; "
                f"the card has {before.free_mb / 1024:.1f} GB free.",
                suggested_action="Close other programs that use the GPU (nvidia-smi lists them) "
                "and retry. If nothing else is running, lower the stage's batch size and its "
                "VRAM estimate.",
            )
        self._sink.emit(
            stage=self._stage,
            message=f"Loading {name} to GPU ({estimate_mb / 1024:.1f} GB)",
            metrics={
                "estimate_mb": round(estimate_mb, 1),
                "free_mb_before": round(before.free_mb, 1),
            },
        )
        started = time.monotonic()
        try:
            model = loader()
        except BaseException:
            self._release_cached()  # whatever a half-finished load allocated
            raise
        seconds = time.monotonic() - started
        after = self._read()
        self._model, self._name, self._baseline = model, name, before
        self._sink.emit(
            stage=self._stage,
            message=f"Loaded {name} to GPU "
            f"({(after.allocated_mb - before.allocated_mb) / 1024:.1f} GB, {seconds:.1f} s)",
            metrics={**_metrics(before, after), "load_s": round(seconds, 2)},
        )
        return model

    def unload(self) -> UnloadReport:
        model, name, baseline = self._model, self._name, self._baseline
        if model is None or name is None or baseline is None:
            raise RuntimeError("No model is loaded; there is nothing to unload.")
        before = self._read()
        self._sink.emit(stage=self._stage, message=f"Unloading {name}, freeing VRAM")
        self._model = self._name = self._baseline = None
        try:
            model.close()
        finally:
            del model
        self._release_cached()
        after = self._read()
        held = after.allocated_mb - baseline.allocated_mb
        returned = held <= TOLERANCE_MB
        metrics = {
            **_metrics(before, after),
            "baseline_allocated_mb": round(baseline.allocated_mb, 1),
        }
        if returned:
            freed = before.allocated_mb - after.allocated_mb
            self._sink.emit(
                stage=self._stage,
                message=f"Unloaded {name}: {freed / 1024:.2f} GB freed, "
                "allocated VRAM back to its baseline",
                metrics=metrics,
            )
        else:
            self._sink.emit(
                stage=self._stage,
                level="WARN",
                message=f"Unloaded {name}, but {held:.0f} MB of VRAM is still allocated",
                reason=f"{held:.0f} MB more is allocated than before {name} was loaded: "
                "something still holds its tensors (BUG5).",
                suggested_action="The process will be restarted to free it: a job's process "
                "exits when its stage ends (ADR-0006 rules 1 and 6). Look for a reference "
                "still kept to the model.",
                metrics=metrics,
            )
        return UnloadReport(name, before, after, baseline.allocated_mb, returned)


def _metrics(before: VramReading, after: VramReading) -> dict[str, MetricValue]:
    """VRAM before and after, from PyTorch and from the driver (MON-3.3)."""
    return {
        "allocated_mb_before": round(before.allocated_mb, 1),
        "allocated_mb_after": round(after.allocated_mb, 1),
        "reserved_mb_before": round(before.reserved_mb, 1),
        "reserved_mb_after": round(after.reserved_mb, 1),
        "free_mb_before": round(before.free_mb, 1),
        "free_mb_after": round(after.free_mb, 1),
    }
