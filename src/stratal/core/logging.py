"""Structured JSON logging (NFR-10).

Every line is one JSON object and always carries ``job_id`` and ``version_id``.
They are ``null`` until a job binds them with :func:`bind_job_context`.
Standard-library loggers (uvicorn, SQLAlchemy) are routed through the same
renderer, so nothing in a container's output is unstructured.
"""

import logging
import sys
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger


def _ensure_ids(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("job_id", None)
    event_dict.setdefault("version_id", None)
    return event_dict


_SHARED: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    _ensure_ids,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Send structlog and standard-library logging to ``stream`` as JSON lines."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    structlog.configure(
        processors=[
            *_SHARED,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger whose lines carry the bound job context."""
    logger: Any = structlog.get_logger(name)
    return logger


def bind_job_context(*, job_id: str | None = None, version_id: str | None = None) -> None:
    """Attach IDs to every later log line in this context (thread or task)."""
    if job_id is not None:
        structlog.contextvars.bind_contextvars(job_id=job_id)
    if version_id is not None:
        structlog.contextvars.bind_contextvars(version_id=version_id)


def clear_job_context() -> None:
    """Remove the bound IDs."""
    structlog.contextvars.unbind_contextvars("job_id", "version_id")
