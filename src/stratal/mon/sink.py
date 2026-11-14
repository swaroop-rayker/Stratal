"""Where stage code sends its events.

Stage code talks only to an :class:`EventSink`. In M1 the sink writes log
lines; in M2 the job runtime supplies one that feeds the event stream
(MON-1.1). Stage code does not change when that happens.
"""

from collections.abc import Mapping
from typing import Literal, Protocol

from stratal.core.logging import get_logger

Level = Literal["DEBUG", "INFO", "WARN", "ERROR"]
Metrics = Mapping[str, float | int | str]


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
    ) -> None: ...


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
    ) -> None:
        method = {"DEBUG": "debug", "INFO": "info", "WARN": "warning", "ERROR": "error"}[level]
        getattr(self._log, method)(
            message,
            stage=stage,
            substage=substage,
            step=step,
            total=total,
            metrics=dict(metrics) if metrics else None,
        )
