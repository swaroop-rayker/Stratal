"""The VRAM manager, v1 (RUN-1.4, RUN-1.5, BUG5, ADR-0006). An owner unit (ADR-0012).

The label stage loads one model at a time through this class and unloads it
before loading the next (the captioner, then CLIP). Job processes also exit at
the end of the stage, which frees everything (ADR-0006 rule 1); the manager's
unload check is what proves a long-lived process could safely reuse the card.

It talks to the GPU only through :class:`~stratal.label.models.GpuBackend`:
:class:`stratal.label.hf.TorchGpu` on the card, and
:class:`stratal.testing.fake_models.FakeGpu` in CPU tests, whose memory comes
back only when the fake weights are really garbage-collected.

Tests: ``tests/unit/test_RUN_1_4_vram_manager.py`` (CPU) and
``tests/gpu/test_BUG5_vram.py`` (the real card); strict expected failures until
this is written.
"""

from collections.abc import Callable
from dataclasses import dataclass

from stratal.label.models import GpuBackend, GpuModel
from stratal.mon.sink import EventSink


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
        raise NotImplementedError("Owner unit RUN-1.4: VramManager")

    @property
    def loaded(self) -> str | None:
        raise NotImplementedError("Owner unit RUN-1.4: VramManager.loaded")

    def load[M: GpuModel](self, loader: Callable[[], M], *, name: str, estimate_mb: float) -> M:
        raise NotImplementedError("Owner unit RUN-1.4: VramManager.load")

    def unload(self) -> UnloadReport:
        raise NotImplementedError("Owner unit RUN-1.4: VramManager.unload")
