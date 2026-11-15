"""Live streams over server-sent events (docs/API.md §4, MON-1.4, OC-2).

PostgreSQL is the only source of events; Redis pub/sub is only the doorbell.
A stream first subscribes, then sends everything after the client's cursor from
PostgreSQL, and from then on reads PostgreSQL again whenever the doorbell rings.
Replay and live are therefore the same query, so a reconnecting client
(``Last-Event-ID``) can neither miss an event nor see one twice.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from stratal.api.jobs import event_view, events_after
from stratal.core.bus import LIVE_GLOBAL, live_channel, subscribe
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.runtime import jobs as job_service

router = APIRouter(tags=["streams"])

PING_S = 15.0
_BATCH = 500


def sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _new_events(job_id: str | None, cursor: int) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        return [
            event_view(row)
            for row in events_after(session, after=cursor, limit=_BATCH, job_id=job_id)
        ]


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
    with Session(get_engine()) as session:
        try:
            return job_service.job_view(session, job_service.get_job(session, job_id))
        except Exception:
            return None


def _cursor(last_event_id: str | None, after: int | None) -> int:
    if last_event_id is not None and last_event_id.strip().isdigit():
        return int(last_event_id)
    return after or 0


async def _stream(
    request: Request, *, job_id: str | None, cursor: int, idle_s: float
) -> AsyncIterator[str]:
    channel = live_channel(job_id) if job_id is not None else LIVE_GLOBAL
    latest_process: dict[str, Any] = {}
    quiet = 0.0
    first = True
    async for received in subscribe(get_settings().redis_url, [channel], idle_s=idle_s):
        if await request.is_disconnected():
            break
        message: dict[str, Any] = received or {}
        kind = message.get("kind")
        if kind == "sample":
            if "process" in message:
                latest_process = dict(message["process"])
            else:
                yield sse("sample", {**message.get("resource", {}), **_torch(latest_process)})
                quiet = 0.0
            continue
        # A doorbell, a timeout, or the first turn after subscribing: read what is new.
        sent = False
        while True:
            rows = await run_in_threadpool(_new_events, job_id, cursor)
            for row in rows:
                cursor = int(row["id"])
                yield sse("event", row, cursor)
            sent = sent or bool(rows)
            if len(rows) < _BATCH:
                break
        target = job_id if job_id is not None else message.get("job_id")
        if target and (kind == "job" or (first and job_id is not None)):
            snapshot = await run_in_threadpool(_job_snapshot, str(target))
            if snapshot is not None:
                yield sse("job", snapshot)
                sent = True
        first = False
        quiet = 0.0 if sent else quiet + (idle_s if received is None else 0.0)
        if quiet >= PING_S:
            quiet = 0.0
            yield ": ping\n\n"


def _torch(process: dict[str, Any]) -> dict[str, Any]:
    keys = ("rss_mb", "torch_allocated_mb", "torch_reserved_mb", "torch_max_allocated_mb")
    return {key: process.get(key) for key in keys if key in process}


def _response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/stream")
async def job_stream(
    job_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """Live feed for one job: its events, job snapshots and samples."""
    await run_in_threadpool(_require_job, job_id)
    cursor = _cursor(last_event_id, after)
    return _response(_stream(request, job_id=job_id, cursor=cursor, idle_s=1.0))


@router.get("/stream")
async def global_stream(
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """Global live feed: every job's events and state changes, and telemetry."""
    cursor = _cursor(last_event_id, after)
    if last_event_id is None and after is None:
        cursor = await run_in_threadpool(_latest_event_id)  # live only, no history
    return _response(_stream(request, job_id=None, cursor=cursor, idle_s=1.0))


def _require_job(job_id: str) -> None:
    with Session(get_engine()) as session:
        job_service.get_job(session, job_id)


def _latest_event_id() -> int:
    from sqlalchemy import func, select

    from stratal.db.models import Event as EventRow

    with Session(get_engine()) as session:
        return int(session.execute(select(func.max(EventRow.id))).scalar_one() or 0)
