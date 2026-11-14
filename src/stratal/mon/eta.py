"""ETA from a moving average of throughput (MON-1.5).

Fed with ``(seconds, step)`` readings from a monotonic clock, never the wall
clock: a laptop sleep moves the wall clock by minutes and the monotonic clock
by almost nothing (ADR-0008).
"""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    throughput: float
    """Items per second over the window."""
    eta_seconds: float | None
    """Seconds left, or ``None`` when the total is unknown."""


class EtaEstimator:
    """Throughput over the last ``window`` readings, and the time left at that rate.

    A window, not the average since the start, so the estimate follows a stage
    that speeds up or slows down. Not a single interval either, which would
    jump with every slow batch.
    """

    def __init__(self, window: int = 20) -> None:
        if window < 2:
            raise ValueError("window must hold at least two readings")
        self._readings: deque[tuple[float, int]] = deque(maxlen=window)

    def update(self, now_s: float, step: int, total: int | None) -> Estimate | None:
        """Record a reading. Returns ``None`` until a rate can be measured."""
        if self._readings and step < self._readings[-1][1]:
            self._readings.clear()  # the counter restarted: a new substage
        self._readings.append((now_s, step))
        first_s, first_step = self._readings[0]
        elapsed = now_s - first_s
        done = step - first_step
        if elapsed <= 0 or done <= 0:
            return None
        throughput = done / elapsed
        eta = None if total is None else max(total - step, 0) / throughput
        return Estimate(throughput, eta)
