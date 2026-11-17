"""Simple stand-ins for the owner's training memory units (RUN-1.4 v2, RUN-1.5).

The training shell calls these until the owner writes the real ones in
``stratal.train.memory`` (ADR-0012). They are deliberately simple:

- :class:`FakeTrainingState` only drops its two references. With no other
  reference left that frees everything, but it does not clear gradients or
  optimizer state explicitly, as the real ``close()`` must.
- :func:`halving_suggestion` halves the batch, whatever the memory figures say.
- :func:`plain_oom_error` names the two batch sizes and nothing else.
"""

from stratal.core.errors import GpuOom
from stratal.train.memory import TrainingState


class FakeTrainingState(TrainingState):
    def close(self) -> None:
        self.model = None
        self.optimizer = None


def halving_suggestion(
    *, failed_batch: int, fixed_mb: float, per_sample_mb: float, cap_mb: float
) -> int | None:
    del fixed_mb, per_sample_mb, cap_mb
    return failed_batch // 2 or None


def plain_oom_error(
    *, failed_batch: int, suggested: int | None, peak_mb: float, cap_mb: float
) -> GpuOom:
    del peak_mb, cap_mb
    if suggested is None:
        return GpuOom(
            "Out of GPU memory even with a batch of 1.",
            suggested_action="Use a smaller model or set device: cpu.",
        )
    return GpuOom(
        f"Out of GPU memory: reduce batch size from {failed_batch} → {suggested}.",
        suggested_action=f"Set the run's batch_size to {suggested} and start it again.",
    )
