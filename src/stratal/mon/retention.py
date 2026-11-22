"""Telemetry retention (NFR-12): raw samples kept 7 days, then 1-minute averages.

Run hourly by the ``beat`` service (:mod:`stratal.runtime.celery_app`). Each batch
averages whole minutes older than the cutoff into ``*_1m`` and deletes the raw rows
it averaged, in one transaction. A crash or a second run therefore never counts a
minute twice, and a minute is never half moved.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Result, Select, delete, func, insert, select
from sqlalchemy.orm import Session

from stratal.db.models import ProcessSample, ProcessSample1m, ResourceSample, ResourceSample1m

RESOURCE_AVERAGED = (
    "interval_s",
    "cpu_percent",
    "ram_used_mb",
    "ram_available_mb",
    "ram_total_mb",
    "swap_used_mb",
    "gpu_util_percent",
    "gpu_mem_util_percent",
    "vram_used_mb",
    "vram_total_mb",
    "gpu_temp_c",
    "gpu_power_w",
    "disk_read_mb_s",
    "disk_write_mb_s",
)
PROCESS_AVERAGED = ("rss_mb", "torch_allocated_mb", "torch_reserved_mb", "cpu_cores")
PROCESS_MAXIMUM = ("torch_max_allocated_mb",)

BATCH = timedelta(hours=6)
"""Raw time moved per transaction: about 21,600 rows of each table at 1 s sampling."""


@dataclass(frozen=True)
class Downsampled:
    resource_rows: int
    """Raw ``resource_samples`` rows replaced by averages."""
    process_rows: int
    """Raw ``process_samples`` rows replaced by averages."""
    minutes: int
    """Rows written to the two ``*_1m`` tables."""


def cutoff(now: datetime, keep_days: float) -> datetime:
    """The start of the minute ``keep_days`` before ``now``: older minutes are moved."""
    edge = now - timedelta(days=keep_days)
    return edge.replace(second=0, microsecond=0)


def _resource_select(start: datetime, end: datetime) -> Select[tuple[object, ...]]:
    minute = func.date_trunc("minute", ResourceSample.ts).label("ts")
    return (
        select(
            minute,
            ResourceSample.job_id,
            ResourceSample.stage,
            func.count().label("samples"),
            *(func.avg(getattr(ResourceSample, name)).label(name) for name in RESOURCE_AVERAGED),
        )
        .where(ResourceSample.ts >= start, ResourceSample.ts < end)
        .group_by(minute, ResourceSample.job_id, ResourceSample.stage)
    )


def _process_select(start: datetime, end: datetime) -> Select[tuple[object, ...]]:
    minute = func.date_trunc("minute", ProcessSample.ts).label("ts")
    return (
        select(
            minute,
            ProcessSample.job_id,
            ProcessSample.stage,
            func.count().label("samples"),
            *(func.avg(getattr(ProcessSample, name)).label(name) for name in PROCESS_AVERAGED),
            *(func.max(getattr(ProcessSample, name)).label(name) for name in PROCESS_MAXIMUM),
        )
        .where(ProcessSample.ts >= start, ProcessSample.ts < end)
        .group_by(minute, ProcessSample.job_id, ProcessSample.stage)
    )


def _oldest(session: Session) -> datetime | None:
    oldest = [
        session.scalar(select(func.min(ResourceSample.ts))),
        session.scalar(select(func.min(ProcessSample.ts))),
    ]
    present = [ts for ts in oldest if ts is not None]
    return min(present) if present else None


def _deleted(result: Result[Any]) -> int:
    """Rows a DELETE removed (a DML statement's result is a cursor result)."""
    return cast("CursorResult[Any]", result).rowcount


def downsample(session: Session, *, now: datetime, keep_days: float = 7.0) -> Downsampled:
    """Move every whole minute older than ``keep_days`` to the 1-minute tables.

    Commits once per batch of :data:`BATCH`, so a large backlog is moved in bounded
    transactions and progress survives an interruption.
    """
    end = cutoff(now, keep_days)
    totals = Downsampled(0, 0, 0)
    while True:
        oldest = _oldest(session)
        if oldest is None or oldest >= end:
            return totals
        start = oldest.replace(second=0, microsecond=0)
        stop = min(start + BATCH, end)
        resource_columns = ["ts", "job_id", "stage", "samples", *RESOURCE_AVERAGED]
        process_columns = ["ts", "job_id", "stage", "samples", *PROCESS_AVERAGED, *PROCESS_MAXIMUM]
        minutes = len(
            session.execute(
                insert(ResourceSample1m)
                .from_select(resource_columns, _resource_select(start, stop))
                .returning(ResourceSample1m.id)
            ).all()
        )
        minutes += len(
            session.execute(
                insert(ProcessSample1m)
                .from_select(process_columns, _process_select(start, stop))
                .returning(ProcessSample1m.id)
            ).all()
        )
        resource = _deleted(
            session.execute(
                delete(ResourceSample).where(ResourceSample.ts >= start, ResourceSample.ts < stop)
            )
        )
        process = _deleted(
            session.execute(
                delete(ProcessSample).where(ProcessSample.ts >= start, ProcessSample.ts < stop)
            )
        )
        session.commit()
        totals = Downsampled(
            totals.resource_rows + resource,
            totals.process_rows + process,
            totals.minutes + minutes,
        )
