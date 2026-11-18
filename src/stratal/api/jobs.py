"""Jobs, events, telemetry and the host script's endpoints (docs/API.md §3.3, §3.4, §3.8)."""

import contextlib
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.api import schemas
from stratal.core.bus import LIVE_GLOBAL, Bus
from stratal.core.errors import ValidationFailed
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Event as EventRow
from stratal.db.models import (
    Job,
    ProcessSample,
    ProcessSample1m,
    ResourceSample,
    ResourceSample1m,
)
from stratal.mon.events import Event, Level
from stratal.mon.store import insert_events
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue, PipelineRequest
from stratal.runtime.states import ACTIVE, TRANSITIONS
from stratal.train.config import TrainRequest

router = APIRouter()

EnqueueFn = Callable[[Enqueue], None]
Json = dict[str, Any]


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def get_enqueue() -> EnqueueFn:
    """Dependency, so tests can run stages themselves instead of through Celery."""
    from stratal.runtime.celery_app import enqueue_stage

    return enqueue_stage


def get_bus() -> Iterator[Bus]:
    bus = Bus(get_settings().redis_url, timeout_s=2.0)
    try:
        yield bus
    finally:
        bus.close()


Db = Annotated[Session, Depends(get_session)]
Queue = Annotated[EnqueueFn, Depends(get_enqueue)]
Redis = Annotated[Bus, Depends(get_bus)]
Limit = Annotated[int, Query(ge=1, le=500)]
JOB_STATES = frozenset(TRANSITIONS)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def event_view(row: EventRow) -> Json:
    return {
        "id": row.id,
        "event_uid": str(row.event_uid),
        "ts": _iso(row.ts),
        "job_id": row.job_id,
        "stage": row.stage,
        "substage": row.substage,
        "version_id": row.version_id,
        "type": row.type,
        "level": row.level,
        "step": row.step,
        "total": row.total,
        "message": row.message,
        "metrics": row.metrics,
        "reason": row.reason,
        "suggested_action": row.suggested_action,
        "attempt": row.attempt,
    }


def _columns(row: Any) -> Json:
    values: Json = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        values[column.name] = _iso(value) if isinstance(value, datetime) else value
    return values


def events_after(
    session: Session,
    *,
    after: int,
    limit: int,
    job_id: str | None = None,
    level: str | None = None,
    stage: str | None = None,
    min_level: str | None = None,
    tail: bool = False,
) -> list[EventRow]:
    """Events after a cursor, oldest first. With ``tail``, the newest ``limit`` that match
    (still oldest first), so a log can open on its latest lines (SCR-04, MON-4.3)."""
    query = select(EventRow).where(EventRow.id > after)
    if job_id is not None:
        query = query.where(EventRow.job_id == job_id)
    if level is not None:
        query = query.where(EventRow.level == level)
    if min_level is not None:
        query = query.where(EventRow.level.in_(LEVELS[LEVELS.index(min_level) :]))
    if stage is not None:
        query = query.where(EventRow.stage == stage)
    if tail:
        newest = list(session.scalars(query.order_by(EventRow.id.desc()).limit(limit)))
        return newest[::-1]
    return list(session.scalars(query.order_by(EventRow.id).limit(limit)))


LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


def _page(rows: list[EventRow], limit: int, *, tail: bool = False) -> Json:
    full = len(rows) == limit and not tail  # a tail is already the newest page
    return {
        "items": [event_view(row) for row in rows],
        "next_cursor": str(rows[-1].id) if full else None,
    }


# -- jobs ----------------------------------------------------------------------


JobRequest = Annotated[PipelineRequest | TrainRequest, Field(discriminator="kind")]


@router.post("/jobs", status_code=202, tags=["jobs"], response_model=schemas.Job)
def create_job(request: JobRequest, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Start a job: ``kind: pipeline``, or ``kind: train`` (M8, RUN-1.1)."""
    with session.begin():
        if isinstance(request, TrainRequest):
            job, first = job_service.create_train_job(session, request)
        else:
            job, first = job_service.create_pipeline_job(session, request)
        view = job_service.job_view(session, job)
    enqueue(first)
    job_service.after_commit(bus, view["id"])
    return view


@router.get("/jobs", tags=["jobs"], response_model=schemas.JobList)
def list_jobs(
    session: Db,
    state: str | None = None,
    kind: str | None = None,
    dataset: str | None = None,
    q: str | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Json:
    """Jobs, newest first. ``state`` takes a comma-separated list (``running,queued``);
    ``q`` keeps jobs whose ID contains it; ``counts`` gives jobs per state under the other
    filters (SCR-07, RUN-1.1 slice)."""
    states = [s.strip() for s in state.split(",") if s.strip()] if state else []
    unknown = sorted(set(states) - JOB_STATES)
    if unknown:
        raise ValidationFailed(
            f"Unknown job state {unknown[0]!r}.",
            suggested_action=f"Use one or more of: {', '.join(sorted(JOB_STATES))}.",
            errors=[{"loc": ["query", "state"], "msg": "unknown state", "code": "enum"}],
        )
    filters: list[Any] = []
    if kind is not None:
        filters.append(Job.kind == kind)
    if dataset is not None:
        filters.append(Job.spec["dataset"].astext == dataset)
    if q:
        filters.append(Job.id.contains(q.strip(), autoescape=True))
    query = select(Job).where(*filters)
    if states:
        query = query.where(Job.state.in_(states))
    if cursor is not None:
        query = query.where(Job.id < cursor)
    rows = list(session.scalars(query.order_by(Job.id.desc()).limit(limit)))
    counted = session.execute(
        select(Job.state, func.count()).where(*filters).group_by(Job.state)
    ).all()
    counts = {s: 0 for s in sorted(JOB_STATES)} | {s: int(n) for s, n in counted}
    return {
        "items": [job_service.job_view(session, job) for job in rows],
        "next_cursor": rows[-1].id if len(rows) == limit else None,
        "counts": counts,
    }


@router.get("/jobs/{job_id}", tags=["jobs"], response_model=schemas.Job)
def get_job(job_id: str, session: Db) -> Json:
    return job_service.job_view(session, job_service.get_job(session, job_id))


@router.post("/jobs/{job_id}/cancel", status_code=202, tags=["jobs"], response_model=schemas.Job)
def cancel_job(job_id: str, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Request cancellation. ``409`` if the job has already finished. Cancelling a paused
    training run also queues retention, which removes its checkpoint (REG-2.6)."""
    with session.begin():
        job, running = job_service.request_cancel(session, job_id)
        cleanup = None if running else job_service.retention_after_run(session, job)
        view = job_service.job_view(session, job)
    if running:
        bus.set_control(job_id, "cancel")  # fast path; jobs.desired_state is the durable copy
    job_service.after_commit(bus, job_id)
    if cleanup is not None:
        enqueue(cleanup)
        job_service.after_commit(bus, cleanup.job_id)
    return view


@router.post("/jobs/{job_id}/pause", status_code=202, tags=["jobs"], response_model=schemas.Job)
def pause_job(job_id: str, session: Db, bus: Redis) -> Json:
    """Ask a running job to pause at its next safe point (RUN-1.2). ``409`` if it is not
    running, or is being cancelled."""
    with session.begin():
        job = job_service.request_pause(session, job_id)
        view = job_service.job_view(session, job)
    bus.set_control(job_id, "pause")  # fast path; jobs.desired_state is the durable copy
    job_service.after_commit(bus, job_id)
    return view


@router.post("/jobs/{job_id}/resume", status_code=202, tags=["jobs"], response_model=schemas.Job)
def resume_job(job_id: str, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Requeue a paused job from the stage that paused (RUN-1.2). ``409`` if it is not
    paused."""
    with session.begin():
        job, resume = job_service.request_resume(session, job_id)
        view = job_service.job_view(session, job)
    bus.clear_control(job_id)
    enqueue(resume)
    job_service.after_commit(bus, job_id)
    return view


@router.post("/jobs/{job_id}/retry", status_code=202, tags=["jobs"], response_model=schemas.Job)
def retry_job(job_id: str, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Requeue a failed job. Finished stages are reused. ``409`` if it is not failed."""
    with session.begin():
        job, resume = job_service.request_retry(session, job_id)
        view = job_service.job_view(session, job)
    bus.clear_control(job_id)
    enqueue(resume)
    job_service.after_commit(bus, job_id)
    return view


@router.get("/jobs/{job_id}/events", tags=["jobs"], response_model=schemas.EventList)
def job_events(
    job_id: str,
    session: Db,
    level: Level | None = None,
    stage: str | None = None,
    after: int = 0,
    limit: Limit = 50,
    cursor: str | None = None,
    min_level: Annotated[Level | None, Query(description="This level and above.")] = None,
    tail: Annotated[bool, Query(description="The newest `limit` events, oldest first.")] = False,
) -> Json:
    job_service.get_job(session, job_id)
    start = int(cursor) if cursor and cursor.isdigit() else after
    rows = events_after(
        session,
        after=start,
        limit=limit,
        job_id=job_id,
        level=level,
        stage=stage,
        min_level=min_level,
        tail=tail,
    )
    return _page(rows, limit, tail=tail)


@router.get("/jobs/{job_id}/telemetry", tags=["jobs"], response_model=schemas.Telemetry)
def job_telemetry(
    job_id: str,
    session: Db,
    stage: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    max_points: Annotated[int, Query(ge=10, le=5000)] = 600,
) -> Json:
    """Samples for charts, thinned evenly to at most ``max_points`` per series.

    Samples older than the raw retention come from the 1-minute averages (NFR-12),
    one point per minute, without the per-core list.
    """
    job_service.get_job(session, job_id)

    def rows(model: Any) -> list[Json]:
        query = select(model).where(model.job_id == job_id)
        if stage is not None:
            query = query.where(model.stage == stage)
        if from_ is not None:
            query = query.where(model.ts >= from_)
        if to is not None:
            query = query.where(model.ts <= to)
        return [_columns(row) for row in session.scalars(query.order_by(model.ts, model.id))]

    def averaged(model: Any, **missing: Any) -> list[Json]:
        out: list[Json] = []
        for row in rows(model):
            row.pop("samples")
            out.append({**row, **missing})
        return out

    def series(points: list[Json]) -> list[Json]:
        points.sort(key=lambda point: datetime.fromisoformat(str(point["ts"])))
        step = max(1, -(-len(points) // max_points))
        return points[::step]

    resource = averaged(ResourceSample1m, active_jobs=None, cpu_per_core=[]) + rows(ResourceSample)
    process = averaged(ProcessSample1m) + rows(ProcessSample)
    return {"resource": series(resource), "process": series(process)}


# -- activity log and telemetry ------------------------------------------------


@router.get("/events", tags=["monitoring"], response_model=schemas.EventList)
def all_events(
    session: Db,
    level: Level | None = None,
    stage: str | None = None,
    job_id: str | None = None,
    after: int = 0,
    limit: Limit = 50,
    cursor: str | None = None,
    min_level: Annotated[Level | None, Query(description="This level and above.")] = None,
    tail: Annotated[bool, Query(description="The newest `limit` events, oldest first.")] = False,
) -> Json:
    """Events across all jobs, oldest first (MON-4.3)."""
    start = int(cursor) if cursor and cursor.isdigit() else after
    rows = events_after(
        session,
        after=start,
        limit=limit,
        job_id=job_id,
        level=level,
        stage=stage,
        min_level=min_level,
        tail=tail,
    )
    return _page(rows, limit, tail=tail)


@router.get("/telemetry/latest", tags=["monitoring"], response_model=schemas.ResourceSample | None)
def latest_sample(session: Db, response: Response) -> Json | None:
    """The most recent resource sample; ``204`` if the sampler has not written one yet."""
    row = session.scalars(
        select(ResourceSample).order_by(ResourceSample.id.desc()).limit(1)
    ).first()
    if row is None:
        response.status_code = 204
        return None
    return _columns(row)


# -- host script ---------------------------------------------------------------


class PowerEvent(BaseModel):
    type: Literal["suspend", "resume"]
    at: datetime


@router.get("/host/keep-awake", tags=["host"])
def keep_awake(session: Db) -> Json:
    """Should Windows be kept awake right now? Polled by the host script (ADR-0008)."""
    active = list(session.scalars(select(Job.id).where(Job.state.in_(ACTIVE)).order_by(Job.id)))
    return {"keep_awake": bool(active), "active_jobs": active}


@router.post("/host/power-events", status_code=202, tags=["host"])
def power_event(body: PowerEvent, session: Db, bus: Redis) -> Json:
    """Record a suspend or resume reported by the host script, so a gap on a job's
    timeline is explained (BUG4)."""
    resumed = body.type == "resume"
    event = Event(
        ts=body.at,
        type="system",
        level="INFO" if resumed else "WARN",
        message="Windows resumed from sleep" if resumed else "Windows is going to sleep",
        reason=None if resumed else "The host reported a suspend.",
        suggested_action=None
        if resumed
        else "Running jobs pause with the machine and continue or are requeued on resume.",
        metrics={"source": "host_script", "power": body.type},
    )
    with session.begin():
        insert_events(session, [event])
    with contextlib.suppress(Exception):
        bus.publish(LIVE_GLOBAL, {"kind": "event", "job_id": None})
    return {"accepted": True}
