"""GPU memory in training: the VRAM manager v2 and out-of-memory handling (RUN-1.4, RUN-1.5).

Owner units (ADR-0012). The agent wrote the stubs, the contracts below and the
tests; the owner writes the bodies marked ``raise NotImplementedError``.

Training reuses the phase-1 :class:`~stratal.label.vram.VramManager`: it loads one
GPU model at a time, refuses a load that does not fit, and proves after unloading
that allocated memory is back at its baseline (RUN-1.4.AC1). What is new in
training is *what* must be freed. Besides the weights there are their gradients
and the optimizer's state (Adam keeps two values per parameter), so
:class:`TrainingState` is the object the manager loads and unloads, and its
``close()`` is what has to free all of it.

Out-of-memory in training fails the job with a suggested batch size instead of
halving the batch and carrying on, as captioning does: a different batch size
changes the optimisation, so the owner of the run chooses (ADR-0006 rule 5,
ADR-0014 rule 10).

PyTorch is installed only in the GPU and ML images, so this module does not
import it; the model and optimizer are typed ``Any``.

Tests: ``tests/unit/test_RUN_1_4_training_state.py``,
``tests/unit/test_RUN_1_5_training_oom.py`` (CPU, with plain fakes) and
``tests/gpu/test_RUN_1_4_training_unload.py`` (the real card).
"""

from typing import Any

from stratal.core.errors import GpuOom
from stratal.label.models import ModelInfo

SAFETY_MARGIN = 0.9
"""Share of the VRAM cap a suggested batch may plan to use (RUN-1.5).

Activation memory per sample is measured at the failed batch and is only an
estimate; the allocator also fragments. Planning for 90 % of the cap leaves room.
"""


class TrainingState:
    """A model and its optimizer, as one thing the VRAM manager loads and unloads (RUN-1.4).

    The constructor and ``info`` are plumbing. ``close()`` is the owner unit.

    ``close()`` must satisfy:

    1. Every parameter's gradient is set to ``None`` (not zeroed: a zeroed
       gradient still holds its memory).
    2. The optimizer's per-parameter state is emptied (for Adam: the two
       running averages per parameter), and so is any other state it keeps on
       the device.
    3. This object drops its own references: ``model`` and ``optimizer`` become
       ``None``. The caller promises to hold no other reference (as in v1).
    4. Calling it twice is harmless.
    5. After ``close()``, :meth:`VramManager.unload` finds allocated memory back
       at the baseline it measured before the load, within v1's tolerance
       (RUN-1.4.AC1). This is checked on the card, after a few real training
       steps with Adam, in ``tests/gpu/test_RUN_1_4_training_unload.py``.
    """

    def __init__(self, model: Any, optimizer: Any, *, info: ModelInfo) -> None:
        self.model: Any = model
        self.optimizer: Any = optimizer
        self._info = info

    @property
    def info(self) -> ModelInfo:
        return self._info

    def close(self) -> None:
        raise NotImplementedError("RUN-1.4 v2: TrainingState.close is the owner's to write")


def suggest_batch_size(
    *, failed_batch: int, fixed_mb: float, per_sample_mb: float, cap_mb: float
) -> int | None:
    """The batch size to suggest after a training step ran out of VRAM (RUN-1.5).

    ``fixed_mb`` is what does not grow with the batch: weights, gradients,
    optimizer state and the CUDA context. ``per_sample_mb`` is the activation
    memory one sample needs, measured at the failed batch. ``cap_mb`` is the
    PyTorch allocator's cap (0.85 of the card, ADR-0006).

    Must satisfy:

    1. Returns the largest power of two ``b`` with ``b < failed_batch`` and
       ``fixed_mb + b * per_sample_mb <= SAFETY_MARGIN * cap_mb``.
    2. Returns ``None`` when no such ``b`` exists: even a batch of 1 would not
       fit, or ``failed_batch`` is already 1.
    3. Raises ``ValueError`` when ``failed_batch < 1``, or when ``fixed_mb``,
       ``per_sample_mb`` or ``cap_mb`` is negative or not finite, or ``cap_mb`` is 0.
    4. Depends on nothing but its arguments.
    """
    raise NotImplementedError("RUN-1.5: suggest_batch_size is the owner's to write")


def training_oom_error(
    *, failed_batch: int, suggested: int | None, peak_mb: float, cap_mb: float
) -> GpuOom:
    """The typed error a training job fails with after running out of VRAM (RUN-1.5, MON-1.3).

    Must satisfy:

    1. Returns (does not raise) a :class:`~stratal.core.errors.GpuOom`.
    2. With a suggestion, its message contains ``"reduce batch size from
       <failed> → <suggested>"`` exactly as MON-1.3.AC1 writes it (``"from 32 →
       16"``), and its ``suggested_action`` tells the person to set the run's
       ``batch_size`` to the suggestion and start it again.
    3. Without one, the message says that even a batch of 1 does not fit, and
       ``suggested_action`` offers a smaller model or ``device: cpu``.
    4. The detail gives ``peak_mb`` and ``cap_mb`` in GB to one decimal
       (MB / 1024, as the VRAM manager's messages do), so the event explains
       itself: 5100 MB is "5.0 GB", 5220 MB is "5.1 GB".
    """
    raise NotImplementedError("RUN-1.5: training_oom_error is the owner's to write")
