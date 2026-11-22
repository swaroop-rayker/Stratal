"""Writing events to PostgreSQL, and the summaries derived from them and from samples."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stratal.db.models import Event as EventRow
from stratal.db.models import Job, JobStage, ProcessSample, ResourceSample
from stratal.mon.events import Event


def insert_events(session: Session, events: list[Event]) -> int:
    """Insert events, skipping any whose ``event_uid`` is already stored.

    The emitter creates the ID, so delivering an event twice (a collector
    restart between insert and acknowledge) stores it once.
    """
    if not events:
        return 0
    events = [_linked(session, event) for event in events]
    statement = (
        insert(EventRow)
        .values([event.to_row() for event in events])
        .on_conflict_do_nothing(index_elements=["event_uid"])
        .returning(EventRow.id)
    )
    return len(session.execute(statement).all())


LINKED_FAILURES = {"gpu_oom": "vram_high", "host_oom_killed": "ram_high"}
"""A failure with one of these codes links to a recent alert of its job (ADR-0020 rule 5)."""
LINK_WINDOW = timedelta(seconds=60)
"""How long after an alert cleared a failure still counts as caused by it."""


def _linked(session: Session, event: Event) -> Event:
    """``event`` with ``caused_by`` set to the VRAM or RAM alert that preceded it: one
    still fired, or cleared less than a minute before (MON-2.7)."""
    code = (event.metrics or {}).get("code")
    if event.caused_by is not None or event.job_id is None or code not in LINKED_FAILURES:
        return event
    alert = session.execute(
        select(EventRow.event_uid, EventRow.ts)
        .where(
            EventRow.type == "alert",
            EventRow.job_id == event.job_id,
            EventRow.metrics["code"].astext == LINKED_FAILURES[str(code)],
            EventRow.metrics["state"].astext == "fired",
            EventRow.ts <= event.ts,
        )
        .order_by(EventRow.ts.desc())
        .limit(1)
    ).first()
    if alert is None:
        return event
    uid: uuid.UUID = alert[0]
    cleared = session.scalar(
        select(EventRow.ts)
        .where(EventRow.caused_by == uid, EventRow.metrics["state"].astext == "cleared")
        .limit(1)
    )
    if cleared is not None and cleared < event.ts - LINK_WINDOW:
        return event
    return event.model_copy(update={"caused_by": uid})


def stage_peaks(session: Session, job_id: str, stage: str) -> dict[str, float | None]:
    """Peak values over every sample tagged with this job and stage (MON-2.5)."""
    resource = session.execute(
        select(
            func.max(ResourceSample.ram_used_mb),
            func.max(ResourceSample.vram_used_mb),
            func.max(ResourceSample.gpu_temp_c),
            func.max(ResourceSample.cpu_percent),
        ).where(ResourceSample.job_id == job_id, ResourceSample.stage == stage)
    ).one()
    process = session.execute(
        select(
            func.max(ProcessSample.rss_mb),
            func.max(ProcessSample.torch_max_allocated_mb),
        ).where(ProcessSample.job_id == job_id, ProcessSample.stage == stage)
    ).one()
    return {
        "ram_used_mb": resource[0],
        "vram_used_mb": resource[1],
        "gpu_temp_c": resource[2],
        "cpu_percent": resource[3],
        "rss_mb": process[0],
        "torch_max_allocated_mb": process[1],
    }


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def refresh_summary(session: Session, job_id: str, *, now: datetime | None = None) -> None:
    """Recompute each stage's peaks and the job summary (MON-2.5)."""
    job = session.get(Job, job_id)
    if job is None:
        return
    stages = session.scalars(
        select(JobStage).where(JobStage.job_id == job_id).order_by(JobStage.idx)
    ).all()
    entries: list[dict[str, Any]] = []
    for stage in stages:
        if stage.started_at is not None:
            stage.peaks = stage_peaks(session, job_id, stage.name)
        entries.append(
            {
                "idx": stage.idx,
                "name": stage.name,
                "status": stage.status,
                "duration_s": _seconds(stage.started_at, stage.finished_at or now),
                "throughput": stage.throughput,
                "peaks": stage.peaks,
            }
        )
    job.summary = {
        "duration_s": _seconds(job.started_at, job.finished_at or now),
        "stages": entries,
    }
