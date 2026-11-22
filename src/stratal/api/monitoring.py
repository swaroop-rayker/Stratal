"""SCR-13's endpoints: telemetry over a range, alerts, diagnostics, model loads and
unloads, and two jobs overlaid (MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4; ADR-0020
rule 13; docs/API.md §3.13)."""

import os
import typing
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import Float, cast, func, literal, select
from sqlalchemy.orm import Session

from stratal.api import schemas
from stratal.api.jobs import Db, event_view
from stratal.core.errors import NotFound, ValidationFailed
from stratal.core.settings import get_settings
from stratal.db.models import Event as EventRow
from stratal.db.models import (
    Job,
    JobStage,
    JobTransition,
    ProcessSample,
    ProcessSample1m,
    ResourceSample,
    ResourceSample1m,
)
from stratal.mon import diagnose as diag
from stratal.mon.events import utc_now
from stratal.runtime import limits

router = APIRouter(tags=["monitoring"])
Json = dict[str, Any]
Limit = Annotated[int, Query(ge=1, le=500)]
TRAINING_KINDS = ("train", "hpo_trial")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _range(
    from_: datetime | None, to: datetime | None, *, default: timedelta
) -> tuple[datetime, datetime]:
    end = to or utc_now()
    start = from_ or end - default
    if start >= end:
        raise ValidationFailed(
            "'from' must be before 'to'.",
            suggested_action="Pick a range whose start is before its end.",
            errors=[{"loc": ["query", "from"], "msg": "must be before 'to'", "code": "range"}],
        )
    return start, end


# ---------------------------------------------------------------- telemetry over a range

_MACHINE = (
    "cpu_percent",
    "ram_used_mb",
    "ram_total_mb",
    "gpu_util_percent",
    "vram_used_mb",
    "vram_total_mb",
    "gpu_temp_c",
    "disk_read_mb_s",
)


@router.get("/telemetry", response_model=schemas.TelemetryRange)
def telemetry_range(
    session: Db,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    max_points: Annotated[int, Query(ge=10, le=2000)] = 600,
) -> Json:
    """The machine over a range for SCR-13's lanes: at most ``max_points`` time buckets,
    each the mean of its samples. Beyond the raw retention the one-minute averages are
    used and the point says so (NFR-12). With the jobs that ran in the range and their
    stages, for the jobs strip. Default: the last hour."""
    start, end = _range(from_, to, default=timedelta(hours=1))
    bucket = max((end - start) / max_points, timedelta(seconds=1))

    def points(model: Any, averaged: bool) -> list[Json]:
        at = func.date_bin(bucket, model.ts, literal(start)).label("at")
        rows = session.execute(
            select(at, *(func.avg(cast(getattr(model, c), Float)).label(c) for c in _MACHINE))
            .where(model.ts >= start, model.ts <= end)
            .group_by(at)
            .order_by(at)
        ).all()
        out: list[Json] = []
        for row in rows:
            point: Json = {"ts": _iso(row[0]), "minute_average": averaged}
            for i, name in enumerate(_MACHINE, start=1):
                value = row[i]
                point[name] = None if value is None else round(float(value), 2)
            out.append(point)
        return out

    old = points(ResourceSample1m, True)
    raw = points(ResourceSample, False)
    seen = {p["ts"] for p in raw}
    merged = sorted([p for p in old if p["ts"] not in seen] + raw, key=lambda p: str(p["ts"]))
    jobs = session.scalars(
        select(Job)
        .where(Job.started_at.is_not(None), Job.started_at <= end)
        .where((Job.finished_at.is_(None)) | (Job.finished_at >= start))
        .order_by(Job.started_at)
        .limit(200)
    ).all()
    job_rows: list[Json] = []
    for job in jobs:
        stages = session.scalars(
            select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.idx)
        ).all()
        job_rows.append(
            {
                "job_id": job.id,
                "kind": job.kind,
                "state": job.state,
                "started_at": _iso(job.started_at),
                "finished_at": _iso(job.finished_at),
                "stages": [
                    {
                        "name": s.name,
                        "status": s.status,
                        "started_at": _iso(s.started_at),
                        "finished_at": _iso(s.finished_at),
                    }
                    for s in stages
                    if s.started_at is not None
                ],
            }
        )
    return {
        "from": _iso(start),
        "to": _iso(end),
        "bucket_s": bucket.total_seconds(),
        "points": merged,
        "jobs": job_rows,
    }


# ---------------------------------------------------------------- alerts (MON-2.6, MON-2.7)


def _caused(session: Session, uids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[EventRow]]:
    out: dict[uuid.UUID, list[EventRow]] = {}
    if not uids:
        return out
    for row in session.scalars(
        select(EventRow).where(EventRow.caused_by.in_(uids)).order_by(EventRow.ts, EventRow.id)
    ):
        assert row.caused_by is not None
        out.setdefault(row.caused_by, []).append(row)
    return out


def _paired(
    session: Session, fired: Sequence[EventRow]
) -> list[tuple[EventRow, EventRow | None, list[EventRow]]]:
    """Each firing with its clearing and the other events it caused."""
    caused = _caused(session, [row.event_uid for row in fired])
    out: list[tuple[EventRow, EventRow | None, list[EventRow]]] = []
    for row in fired:
        linked = caused.get(row.event_uid, [])
        cleared = next((e for e in linked if (e.metrics or {}).get("state") == "cleared"), None)
        actions = [e for e in linked if e is not cleared]
        out.append((row, cleared, actions))
    return out


def _lasted(fired: EventRow, cleared: EventRow | None) -> float | None:
    return None if cleared is None else round((cleared.ts - fired.ts).total_seconds(), 1)


@router.get("/system/alerts", response_model=schemas.SystemAlerts)
def system_alerts(
    session: Db,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    job_id: str | None = None,
    limit: Limit = 50,
) -> Json:
    """Alerts fired in the range, newest first, each with its clearing and the actions it
    caused (pause, stalled, a failure with its suggested batch). Default: the last hour."""
    start, end = _range(from_, to, default=timedelta(hours=1))
    query = select(EventRow).where(
        EventRow.type == "alert",
        EventRow.metrics["state"].astext == "fired",
        EventRow.ts >= start,
        EventRow.ts <= end,
    )
    if job_id is not None:
        query = query.where(EventRow.job_id == job_id)
    fired = session.scalars(
        query.order_by(EventRow.ts.desc(), EventRow.id.desc()).limit(limit)
    ).all()
    env = get_settings()
    training = limits.read(session, env)
    monitoring = limits.read_monitoring(session, env)
    return {
        "items": [
            {
                "code": str((row.metrics or {}).get("code")),
                "firing": cleared is None,
                "fired": event_view(row),
                "cleared": event_view(cleared) if cleared is not None else None,
                "actions": [event_view(a) for a in actions],
                "lasted_s": _lasted(row, cleared),
            }
            for row, cleared, actions in _paired(session, fired)
        ],
        "thresholds": {
            "vram_percent": float(monitoring.vram_alert_percent),
            "ram_percent": float(monitoring.ram_alert_percent),
            "pause_c": float(training.thermal_pause_c),
            "resume_c": float(training.thermal_resume_c),
            "stall_s": float(training.stall_timeout_s),
        },
    }


# ---------------------------------------------------------------- diagnostics (MON-3.2)


@router.get("/system/diagnostics", response_model=schemas.SystemDiagnostics)
def system_diagnostics(
    session: Db,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Limit = 50,
) -> Json:
    """*Now*: every running stage's expected resource and what was observed over the last
    30 s, with the diagnosis the watchdog would give; *Earlier*: past diagnoses in the
    range, with how long each lasted."""
    start, end = _range(from_, to, default=timedelta(hours=1))
    now = session.scalar(select(func.now()))
    assert now is not None
    disk = float(limits.read_monitoring(session, get_settings()).disk_bottleneck_mb_s)
    live: list[Json] = []
    rows = session.execute(
        select(Job, JobStage)
        .join(JobStage, (JobStage.job_id == Job.id) & (JobStage.status == "running"))
        .where(Job.state.in_(("running", "stalled")))
        .order_by(Job.id)
    ).all()
    for job, stage in rows:
        window = diag.read_window(
            session, job_id=job.id, stage=stage.name, started_at=stage.started_at, now=now
        )
        seen = window.observed
        found = diag.diagnose(
            seen, queue=stage.queue, running_for_s=window.running_for_s, disk_threshold_mb_s=disk
        )
        live.append(
            {
                "job_id": job.id,
                "stage": stage.name,
                "queue": stage.queue,
                "expected": list(diag.expected(stage.name, stage.queue)),
                "running_for_s": round(window.running_for_s, 1),
                "observed": {
                    "samples": seen.samples,
                    "gpu_percent": None if seen.gpu_percent is None else round(seen.gpu_percent, 1),
                    "cpu_cores": None if seen.cpu_cores is None else round(seen.cpu_cores, 2),
                    "capacity_cores": seen.capacity_cores,
                    "loader_workers": seen.loader_workers,
                    "disk_read_mb_s": round(seen.disk_read_mb_s, 1),
                },
                "thresholds": {
                    "gpu_low_percent": diag.GPU_LOW,
                    "cpu_saturated_share": diag.SATURATED,
                    "disk_mb_s": disk,
                },
                "diagnosis": None
                if found is None
                else {
                    "code": found.code,
                    "message": found.message,
                    "reason": found.reason,
                    "suggested_action": found.action,
                },
            }
        )
    fired = session.scalars(
        select(EventRow)
        .where(
            EventRow.type == "diagnostic",
            EventRow.metrics["state"].astext == "fired",
            EventRow.ts >= start,
            EventRow.ts <= end,
        )
        .order_by(EventRow.ts.desc(), EventRow.id.desc())
        .limit(limit)
    ).all()
    return {
        "live": live,
        "logical_cpus": os.cpu_count() or 1,
        "items": [
            {
                "fired": event_view(row),
                "cleared": event_view(cleared) if cleared is not None else None,
                "lasted_s": _lasted(row, cleared),
            }
            for row, cleared, _actions in _paired(session, fired)
        ],
    }


# ---------------------------------------------------------------- loads and unloads (MON-3.3)


@router.get("/system/models", response_model=schemas.EventList)
def system_models(
    session: Db,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Limit = 50,
) -> Json:
    """Model loads and unloads in the range, newest first, with PyTorch's and the card's
    VRAM before and after in ``metrics`` (``allocated_mb_*``, ``reserved_mb_*``,
    ``card_used_mb_*``)."""
    start, end = _range(from_, to, default=timedelta(hours=1))
    rows = session.scalars(
        select(EventRow)
        .where(
            EventRow.type.in_(("model_loaded", "model_unloaded")),
            EventRow.ts >= start,
            EventRow.ts <= end,
        )
        .order_by(EventRow.ts.desc(), EventRow.id.desc())
        .limit(limit)
    ).all()
    return {"items": [event_view(r) for r in rows], "next_cursor": None}


# ---------------------------------------------------------------- two jobs overlaid (MON-4.4)


@dataclass(frozen=True)
class _Timeline:
    """Maps a timestamp to the job's x: seconds spent running, or the epoch reached."""

    spans: list[tuple[datetime, datetime]]
    epochs: list[datetime]
    """Epoch boundaries: the training start, then the end of each epoch's validation."""

    def running_s(self, ts: datetime) -> float | None:
        done = 0.0
        for begin, end in self.spans:
            if begin <= ts <= end:
                return done + (ts - begin).total_seconds()
            done += (end - begin).total_seconds()
        return None

    def epoch(self, ts: datetime, last: int) -> float | None:
        b = self.epochs
        if len(b) < 2 or ts < b[0] or ts > b[min(last, len(b) - 1)]:
            return None
        for k in range(1, min(last, len(b) - 1) + 1):
            if ts <= b[k]:
                width = (b[k] - b[k - 1]).total_seconds() or 1e-9
                return k - 1 + (ts - b[k - 1]).total_seconds() / width
        return None


def _timeline(session: Session, job: Job) -> _Timeline:
    moves = session.execute(
        select(JobTransition.to_state, JobTransition.at)
        .where(JobTransition.job_id == job.id)
        .order_by(JobTransition.id)
    ).all()
    spans: list[tuple[datetime, datetime]] = []
    opened: datetime | None = None
    for state, at in moves:
        if state in ("running", "stalled"):
            opened = opened or at
        elif opened is not None:
            spans.append((opened, at))
            opened = None
    if opened is not None:
        spans.append((opened, job.finished_at or utc_now()))
    epochs: list[datetime] = []
    if job.kind in TRAINING_KINDS:
        start = session.scalar(
            select(func.min(EventRow.ts)).where(
                EventRow.job_id == job.id,
                EventRow.substage == "train",
                EventRow.metrics["epoch"].astext == "1",
            )
        )
        ends = session.execute(
            select(EventRow.metrics["epoch"].astext, EventRow.ts).where(
                EventRow.job_id == job.id,
                EventRow.substage == "validate",
                EventRow.metrics.has_key("epoch"),
            )
        ).all()
        by_epoch: dict[int, datetime] = {}
        for e, t in ends:
            if e and e.isdigit() and int(e) >= 1:
                k = int(e)
                by_epoch[k] = min(t, by_epoch.get(k, t))
        if start is not None and by_epoch:
            epochs = [start]
            for k in range(1, max(by_epoch) + 1):
                if k not in by_epoch:
                    break
                epochs.append(by_epoch[k])
    return _Timeline(spans, epochs)


def cast_json(value: Any) -> Json:
    """A JSON object from a stored config, typed."""
    return typing.cast(Json, value)


def _label(session: Session, job: Job) -> str:
    stage = session.scalars(
        select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.idx)
    ).first()
    config: Json = (stage.config if stage is not None else None) or {}
    raw: Any = config.get("model")
    if not isinstance(raw, dict):
        return job.kind
    model = cast_json(raw)
    graph: Any = model.get("graph")
    labels = cast_json(graph) if isinstance(graph, dict) else {}
    name = str(labels.get("label") or model.get("name") or job.kind)
    epochs = config.get("epochs")
    return f"{name} · {epochs} epochs" if epochs else str(name)


def _thin(points: list[Json], cap: int) -> list[Json]:
    step = max(1, -(-len(points) // cap))
    return points[::step]


def _side(session: Session, job: Job, timeline: _Timeline, align: str, last: int, cap: int) -> Json:
    def x_of(ts: datetime) -> float | None:
        return timeline.epoch(ts, last) if align == "epoch" else timeline.running_s(ts)

    machine: list[Json] = []
    for model, averaged in ((ResourceSample1m, True), (ResourceSample, False)):
        for row in session.scalars(select(model).where(model.job_id == job.id).order_by(model.ts)):
            x = x_of(row.ts)
            if x is None:
                continue
            machine.append(
                {
                    "x": round(x, 4),
                    "gpu_util_percent": row.gpu_util_percent,
                    "vram_used_mb": row.vram_used_mb,
                    "gpu_temp_c": row.gpu_temp_c,
                    "cpu_percent": row.cpu_percent,
                    "ram_used_mb": row.ram_used_mb,
                    "minute_average": averaged,
                }
            )
    process: list[Json] = []
    for model in (ProcessSample1m, ProcessSample):
        for row in session.scalars(select(model).where(model.job_id == job.id).order_by(model.ts)):
            x = x_of(row.ts)
            if x is None:
                continue
            process.append(
                {
                    "x": round(x, 4),
                    "rss_mb": row.rss_mb,
                    "torch_allocated_mb": row.torch_allocated_mb,
                    "cpu_cores": row.cpu_cores,
                }
            )
    machine.sort(key=lambda p: p["x"])
    process.sort(key=lambda p: p["x"])

    def mean(values: list[float | None]) -> float | None:
        kept = [v for v in values if v is not None]
        return round(sum(kept) / len(kept), 2) if kept else None

    def peak(values: list[float | None]) -> float | None:
        kept = [v for v in values if v is not None]
        return round(max(kept), 2) if kept else None

    running = sum((e - b).total_seconds() for b, e in timeline.spans)
    per_epoch = None
    if align == "epoch" and len(timeline.epochs) > last:
        per_epoch = round((timeline.epochs[last] - timeline.epochs[0]).total_seconds() / last, 2)
    return {
        "job_id": job.id,
        "kind": job.kind,
        "state": job.state,
        "label": _label(session, job),
        "started_at": _iso(job.started_at),
        "running_s": round(running, 1),
        "epochs": max(len(timeline.epochs) - 1, 0) or None,
        "machine": _thin(machine, cap),
        "process": _thin(process, cap),
        "summary": {
            "seconds_per_epoch": per_epoch,
            "gpu_mean_percent": mean([p["gpu_util_percent"] for p in machine]),
            "vram_peak_mb": peak([p["vram_used_mb"] for p in machine]),
            "torch_allocated_peak_mb": peak([p["torch_allocated_mb"] for p in process]),
            "rss_mean_mb": mean([p["rss_mb"] for p in process]),
            "cpu_cores_mean": mean([p["cpu_cores"] for p in process]),
            "gpu_temp_max_c": peak([p["gpu_temp_c"] for p in machine]),
        },
    }


@router.get("/system/compare", response_model=schemas.JobOverlay)
def compare_jobs(
    session: Db,
    a: str,
    b: str,
    align: Literal["time", "epoch"] = "time",
    max_points: Annotated[int, Query(ge=10, le=2000)] = 600,
) -> Json:
    """Two jobs' resource curves on one axis (MON-4.4, K7): by time spent running (pauses
    and queueing removed), or by epoch for two training runs, over the epochs both ran.
    ``epoch_unavailable`` says why epoch alignment cannot be used, when it cannot."""
    jobs: list[Job] = []
    for job_id in (a, b):
        job = session.get(Job, job_id)
        if job is None:
            raise NotFound(f"No job {job_id}.", suggested_action="Pick a job from the list.")
        jobs.append(job)
    timelines = [_timeline(session, job) for job in jobs]
    reason: str | None = None
    for job in jobs:
        if job.kind not in TRAINING_KINDS:
            reason = f"By epoch needs two training runs: {job.id} is a {job.kind}."
            break
    if reason is None and any(len(t.epochs) < 2 for t in timelines):
        missing = next(job.id for job, t in zip(jobs, timelines, strict=True) if len(t.epochs) < 2)
        reason = f"By epoch needs a finished epoch in each run: {missing} has none."
    if align == "epoch" and reason is not None:
        raise ValidationFailed(
            reason,
            suggested_action="Align by time instead, or pick two training runs.",
            errors=[{"loc": ["query", "align"], "msg": reason, "code": "epoch_unavailable"}],
        )
    common = min(len(t.epochs) - 1 for t in timelines) if reason is None else 0
    return {
        "align": align,
        "common_epochs": common or None,
        "epoch_unavailable": reason,
        "a": _side(session, jobs[0], timelines[0], align, common, max_points),
        "b": _side(session, jobs[1], timelines[1], align, common, max_points),
    }
