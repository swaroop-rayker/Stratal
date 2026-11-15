"""Where stage code sends its events.

Stage code talks only to an :class:`EventSink`. From the command line the sink
writes log lines; inside a job the runtime supplies a
:class:`stratal.runtime.context.JobContext`, which feeds the event stream
(MON-1.1). Stage code is the same in both cases.
"""

from collections.abc import Mapping
from typing import Protocol

from stratal.core.logging import get_logger
from stratal.mon.events import Level

Metrics = Mapping[str, float | int | str]

__all__ = ["EventSink", "Level", "LogSink", "Metrics"]


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
        )
