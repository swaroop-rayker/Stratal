"""The job context: what stage code is given to talk to the outside (API.md §5).

It is an :class:`stratal.mon.sink.EventSink`, so a stage emits through it
exactly as it would to a log, and it answers "should I stop?". Stage code never
touches Redis, PostgreSQL or Celery.
"""

import time
from collections.abc import Callable

from stratal.core.logging import bind_job_context, get_logger
from stratal.mon.emitter import Emitter
from stratal.mon.eta import EtaEstimator
from stratal.mon.events import Event, EventType, Level, MetricValue
from stratal.mon.sink import Metrics

log = get_logger("stratal.job")


class JobCancelled(Exception):
    """Raised at a safe point when the job has been asked to stop."""


class JobContext:
    def __init__(
        self,
        *,
        job_id: str,
        attempt: int,
        stage: str,
        emitter: Emitter,
        should_stop: Callable[[], bool] | None = None,
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

    def set_version(self, version_id: str) -> None:
        """Tag later events and log lines with the version being produced."""
        self.version_id = version_id
        bind_job_context(version_id=version_id)

    def should_stop(self) -> bool:
        return self._should_stop() if self._should_stop is not None else False

    def raise_if_cancelled(self) -> None:
        """Call at a safe point: between batches, after a commit."""
        if self.should_stop():
            raise JobCancelled(self.job_id)

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
    ) -> None:
        """An activity message, or a progress report when ``step`` is given.

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
            "stage_progress" if step is not None else "activity",
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
