"""The job context: what stage code is given to talk to the outside (API.md §5).

It is an :class:`stratal.mon.sink.EventSink`, so a stage emits through it
exactly as it would to a log, and it answers "should I stop?". Stage code never
touches Redis, PostgreSQL or Celery.
"""

import os
import signal
import time
from collections.abc import Callable
from typing import Literal

from stratal.core.logging import bind_job_context, get_logger
from stratal.mon.emitter import Emitter
from stratal.mon.eta import EtaEstimator
from stratal.mon.events import Event, EventType, Level, MetricValue
from stratal.mon.sink import Metrics, SinkType

log = get_logger("stratal.job")

CRASH_AT_ENV = "STRATAL_TEST_CRASH_AT"
"""Test-only crash point for the NFR-5 kill tests. ``<stage>:step:<k>`` kills the
stage process with SIGKILL right after that stage reports progress at or past step
``k``; ``<stage>:event:<n>`` right after its ``n``-th event; ``<stage>:substage:<name>``
right after its first event of that substage. It fires on a job's first attempt
only, so a retry runs through. Events are spooled before the kill, and stages
report progress after a batch is cached, so the kill lands just after a durable
point, as a real crash between batches would."""


class _CrashPoint:
    def __init__(self, spec: str) -> None:
        stage, kind, at = spec.split(":")
        if kind not in ("step", "event", "substage"):
            raise ValueError(f"{CRASH_AT_ENV}: kind must be step, event or substage, not {kind!r}")
        self.stage, self.kind = stage, kind
        self.substage = at if kind == "substage" else None
        self.number = int(at) if kind != "substage" else 0
        self.events = 0

    def reached(self, event: Event) -> bool:
        if event.stage != self.stage:
            return False
        self.events += 1
        if self.kind == "substage":
            return event.substage == self.substage
        if self.kind == "event":
            return self.events >= self.number
        return event.step is not None and event.step >= self.number


def _crash_point(attempt: int) -> _CrashPoint | None:
    spec = os.environ.get(CRASH_AT_ENV)
    return _CrashPoint(spec) if spec and attempt == 1 else None


class JobCancelled(Exception):
    """Raised at a safe point when the job has been asked to stop."""


class JobPaused(Exception):
    """Raised at a safe point when the job has been asked to pause (RUN-1.2, ADR-0014 rule 6).

    The stage's work so far is in its caches, or in a checkpoint for training, so
    resuming runs the stage again and it continues from there.
    """


class StageHandoff(Exception):
    """Raised by a stage that should run on another queue instead (RUN-1.9): a training run
    whose device benchmark found the CPU faster moves from the GPU queue to the CPU queue."""

    def __init__(self, queue: Literal["cpu", "gpu"], reason: str) -> None:
        super().__init__(reason)
        self.queue = queue
        self.reason = reason


class StageWaiting(Exception):
    """Raised by a stage that has handed its work to another job and should run again when
    that job ends: a deep-learning study's step, after it created the next trial's job
    (ADR-0018 rule 5). The stage becomes ``waiting``; the job stays ``running``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


StopRequest = Literal["cancel", "pause"]


class JobContext:
    def __init__(
        self,
        *,
        job_id: str,
        attempt: int,
        stage: str,
        emitter: Emitter,
        should_stop: Callable[[], bool | str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.job_id = job_id
        self.attempt = attempt
        self.stage = stage
        self.version_id: str | None = None
        self._emitter = emitter
        self._should_stop = should_stop
        self._clock = clock
        self._estimators: dict[str | None, EtaEstimator] = {}
        self._crash = _crash_point(attempt)

    def set_version(self, version_id: str) -> None:
        """Tag later events and log lines with the version being produced."""
        self.version_id = version_id
        bind_job_context(version_id=version_id)

    def stop_request(self) -> StopRequest | None:
        """Whether the job was asked to stop, and how. ``should_stop`` may answer
        ``True`` (cancel), ``"cancel"`` or ``"pause"``."""
        value = self._should_stop() if self._should_stop is not None else None
        if value is True or value == "cancel":
            return "cancel"
        if value == "pause":
            return "pause"
        return None

    def should_stop(self) -> bool:
        return self.stop_request() is not None

    def raise_if_cancelled(self) -> None:
        """Call at a safe point: between batches, after a commit. Raises
        :class:`JobCancelled` or :class:`JobPaused` if the job was asked to stop."""
        request = self.stop_request()
        if request == "cancel":
            raise JobCancelled(self.job_id)
        if request == "pause":
            raise JobPaused(self.job_id)

    def emit(
        self,
        *,
        stage: str,
        message: str,
        level: Level = "INFO",
        substage: str | None = None,
        step: int | None = None,
        total: int | None = None,
        metrics: Metrics | None = None,
        reason: str | None = None,
        suggested_action: str | None = None,
        event_type: SinkType = "activity",
    ) -> None:
        """An activity message (or a model load or unload, ``event_type``), or a
        progress report when ``step`` is given.

        Progress reports get throughput and ETA added (MON-1.5), measured on
        the monotonic clock.
        """
        merged: dict[str, MetricValue] = dict(metrics) if metrics else {}
        if step is not None:
            estimator = self._estimators.setdefault(substage, EtaEstimator())
            estimate = estimator.update(self._clock(), step, total)
            if estimate is not None:
                merged["items_per_s"] = round(estimate.throughput, 3)
                if estimate.eta_seconds is not None:
                    merged["eta_s"] = round(estimate.eta_seconds, 1)
        self.event(
            "stage_progress" if step is not None else event_type,
            message,
            stage=stage,
            level=level,
            substage=substage,
            step=step,
            total=total,
            metrics=merged,
            reason=reason,
            suggested_action=suggested_action,
        )

    def event(
        self,
        event_type: EventType,
        message: str,
        *,
        stage: str | None = None,
        level: Level = "INFO",
        substage: str | None = None,
        step: int | None = None,
        total: int | None = None,
        metrics: dict[str, MetricValue] | None = None,
        reason: str | None = None,
        suggested_action: str | None = None,
    ) -> None:
        event = Event(
            job_id=self.job_id,
            attempt=self.attempt,
            stage=stage or self.stage,
            substage=substage,
            version_id=self.version_id,
            type=event_type,
            level=level,
            step=step,
            total=total,
            message=message,
            metrics=metrics or None,
            reason=reason,
            suggested_action=suggested_action,
        )
        method = {"DEBUG": "debug", "INFO": "info", "WARN": "warning", "ERROR": "error"}[level]
        getattr(log, method)(
            message, stage=event.stage, substage=substage, type=event_type, step=step, total=total
        )
        self._emitter.emit(event)
        if self._crash is not None and self._crash.reached(event):
            log.warning("test_crash_point", stage=event.stage, step=step)
            os.kill(os.getpid(), signal.SIGKILL)
