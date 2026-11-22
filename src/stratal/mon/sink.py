"""Where stage code sends its events.

Stage code talks only to an :class:`EventSink`. From the command line the sink
writes log lines; inside a job the runtime supplies a
:class:`stratal.runtime.context.JobContext`, which feeds the event stream
(MON-1.1). Stage code is the same in both cases.
"""

from collections.abc import Mapping
from typing import Literal, Protocol

from stratal.core.logging import get_logger
from stratal.mon.events import Event, Level

Metrics = Mapping[str, float | int | str]
SinkType = Literal["activity", "model_loaded", "model_unloaded"]
"""What a stage may say besides progress: an activity, or a model load or unload
(MON-3.3, ADR-0020 rule 9)."""

__all__ = ["EventSink", "Level", "LogSink", "Metrics", "SinkType", "StreamSink"]


class EventSink(Protocol):
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
        """Say what the stage is doing. WARN and ERROR need ``reason`` and
        ``suggested_action`` (MON-1.3)."""
        ...


class LogSink:
    """Writes each event as a structured log line."""

    def __init__(self) -> None:
        self._log = get_logger("stratal.events")

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
        method = {"DEBUG": "debug", "INFO": "info", "WARN": "warning", "ERROR": "error"}[level]
        getattr(self._log, method)(
            message,
            stage=stage,
            substage=substage,
            step=step,
            total=total,
            metrics=dict(metrics) if metrics else None,
            reason=reason,
            suggested_action=suggested_action,
            event_type=event_type,
        )


class Emits(Protocol):
    def emit(self, event: Event) -> None: ...


class StreamSink:
    """Sends a service's events to the event stream with no job (the inference service's
    loads and unloads, MON-3.3), and to its log as well."""

    def __init__(self, emitter: Emits) -> None:
        self._emitter = emitter
        self._log = LogSink()

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
        self._log.emit(
            stage=stage,
            message=message,
            level=level,
            substage=substage,
            step=step,
            total=total,
            metrics=metrics,
            reason=reason,
            suggested_action=suggested_action,
            event_type=event_type,
        )
        try:
            self._emitter.emit(
                Event(
                    stage=stage,
                    substage=substage,
                    type=event_type,
                    level=level,
                    step=step,
                    total=total,
                    message=message,
                    metrics=dict(metrics) if metrics else None,
                    reason=reason,
                    suggested_action=suggested_action,
                )
            )
        except Exception as exc:  # telemetry never breaks the service (NFR-13)
            self._log.emit(stage=stage, message=f"event not sent: {str(exc)[:200]}", level="DEBUG")
