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
from stratal.db.models import (
    Dataset,
    DatasetVersion,
    Job,
    JobStage,
    JobTransition,
    ModelLineage,
    ModelVersion,
    ProcessSample,
    ProcessSample1m,
    ResourceSample,
    ResourceSample1m,
)
from stratal.db.models import Event as EventRow
from stratal.mon import diagnose as diag
from stratal.mon import history as hist
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


# -- MON-4.5: trends across the last N runs of one stage (ADR-0020 rule 12) ----------------


@dataclass
class _Attempts:
    running_s: float = 0.0
    attempts: int = 0
    paused: bool = False
    worker_lost: bool = False


def _attempts(session: Session, job_ids: Sequence[str]) -> dict[str, _Attempts]:
    """Per job: seconds running across attempts, how many attempts, whether a person paused
    it, and whether the watchdog requeued it after its worker was lost."""
    rows = session.execute(
        select(JobTransition.job_id, JobTransition.to_state, JobTransition.at, JobTransition.actor)
        .where(JobTransition.job_id.in_(job_ids))
        .order_by(JobTransition.job_id, JobTransition.id)
    ).all()
    out: dict[str, _Attempts] = {}
    opened: dict[str, datetime] = {}
    for job_id, state, at, actor in rows:
        entry = out.setdefault(job_id, _Attempts())
        if state in ("running", "stalled"):
            if job_id not in opened:
                opened[job_id] = at
                entry.attempts += 1 if state == "running" else 0
            continue
        start = opened.pop(job_id, None)
        if start is not None:
            entry.running_s += (at - start).total_seconds()
        if state == "paused" and actor != "watchdog":
            entry.paused = True
        if state == "queued" and actor == "watchdog":
            entry.worker_lost = True
    return out


def _stage_list(session: Session) -> list[Json]:
    rows = session.execute(
        select(
            JobStage.name,
            JobStage.queue,
            func.count().filter(JobStage.cache_hit.is_(False)),
            func.count().filter(JobStage.cache_hit.is_(True)),
            func.max(JobStage.started_at),
        )
        .where(JobStage.status == "completed")
        .group_by(JobStage.name, JobStage.queue)
        .order_by(func.max(JobStage.started_at).desc().nulls_last())
    ).all()
    return [
        {
            "stage": name,
            "queue": q,
            "runs": int(fresh),
            "reused": int(cached),
            "last_at": _iso(last),
        }
        for name, q, fresh, cached, last in rows
    ]


def _history_row(run: hist.StageRun, key: str, attempts: int) -> Json:
    secs = hist.seconds(run)
    labelled = run.stage == "label"
    items = hist.number(run.metrics.get("images")) if labelled else None
    computed = hist.number(run.metrics.get("captions_computed")) if labelled else None
    return {
        "job_id": run.job_id,
        "kind": run.kind,
        "started_at": _iso(run.started_at),
        "group": key,
        "reused": run.cache_hit,
        "attempts": max(attempts, 1),
        "running_s": round(secs, 1) if secs is not None else None,
        "epochs": hist.epochs_run(run) if run.stage in hist.TRAINING_STAGES else None,
        "time": hist.time_value(run)[0],
        "rate": hist.rate(run),
        "items": int(items) if items is not None else None,
        "computed": int(computed) if computed is not None else None,
        "peaks": {
            "vram_used_mb": hist.number(run.peaks.get("vram_used_mb")),
            "torch_allocated_mb": hist.number(run.peaks.get("torch_max_allocated_mb")),
            "ram_used_mb": hist.number(run.peaks.get("ram_used_mb")),
            "gpu_temp_c": hist.number(run.peaks.get("gpu_temp_c")),
            "rss_mb": hist.number(run.peaks.get("rss_mb")),
        },
        "notes": hist.notes(run),
    }


@router.get("/system/history", response_model=schemas.SystemHistory)
def system_history(
    session: Db,
    stage: str | None = None,
    queue: str | None = None,
    group: str | None = None,
    n: Annotated[int, Query(ge=1, le=100)] = 20,
    reused: bool = False,
) -> Json:
    """The last ``n`` completed runs of one stage in one group of like runs (MON-4.5): the
    rate in the stage's own unit, running time across attempts (per epoch for training) and
    the MON-2.5 peaks. Without ``stage``, the most recently run stage; without ``group``, the
    latest run's group; ``group=all`` for every group. Stages reused from the cache are left
    out unless ``reused``."""
    stages = _stage_list(session)
    result: Json = {
        "stages": stages,
        "stage": None,
        "queue": None,
        "rate_unit": None,
        "time_unit": "s",
        "groups": [],
        "group": None,
        "n": n,
        "runs": [],
    }
    if not stages:
        return result
    if stage is None:
        stage, queue = stages[0]["stage"], stages[0]["queue"]
    elif queue is None:
        queue = next((s["queue"] for s in stages if s["stage"] == stage), None)
    if not any(s["stage"] == stage and s["queue"] == queue for s in stages):
        raise NotFound(
            f"No completed run of {stage} on the {queue} queue.",
            suggested_action="Pick a stage from the list.",
        )
    query = (
        select(JobStage, Job.kind)
        .join(Job, Job.id == JobStage.job_id)
        .where(JobStage.name == stage, JobStage.queue == queue, JobStage.status == "completed")
        .order_by(JobStage.started_at.desc().nulls_last(), JobStage.job_id.desc())
        .limit(2000)
    )
    if not reused:
        query = query.where(JobStage.cache_hit.is_(False))
    found = [(row, kind) for row, kind in session.execute(query).all()]
    job_ids = [row.job_id for row, _ in found]
    single = set(
        session.scalars(
            select(JobStage.job_id)
            .where(JobStage.job_id.in_(job_ids))
            .group_by(JobStage.job_id)
            .having(func.count() == 1)
        )
    )
    timing = _attempts(session, job_ids)
    inputs = {row.input_version_id for row, _ in found if row.input_version_id}
    slugs: dict[str, str] = {
        v: s
        for v, s in session.execute(
            select(DatasetVersion.version_id, Dataset.slug)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(DatasetVersion.version_id.in_(inputs))
        ).all()
    }
    lineages: dict[str, str] = {
        j: name
        for j, name in session.execute(
            select(ModelVersion.job_id, ModelLineage.name)
            .join(ModelLineage, ModelLineage.id == ModelVersion.lineage_id)
            .where(ModelVersion.job_id.in_(job_ids))
        ).all()
        if j is not None
    }
    runs: list[tuple[hist.StageRun, str, int]] = []
    groups: dict[str, Json] = {}
    for row, kind in found:
        t = timing.get(row.job_id, _Attempts())
        stage_seconds = (
            (row.finished_at - row.started_at).total_seconds()
            if row.finished_at and row.started_at
            else None
        )
        run = hist.StageRun(
            job_id=row.job_id,
            kind=kind,
            stage=row.name,
            queue=row.queue,
            started_at=row.started_at,
            config=row.config or {},
            metrics=row.metrics or {},
            peaks=row.peaks or {},
            throughput=row.throughput,
            cache_hit=row.cache_hit,
            stage_seconds=stage_seconds,
            running_s=t.running_s if row.job_id in single and t.running_s > 0 else None,
            paused=t.paused,
            worker_lost=t.worker_lost,
            dataset_slug=slugs.get(row.input_version_id or ""),
            lineage_name=lineages.get(row.job_id),
        )
        key, label = hist.group(run)
        runs.append((run, key, t.attempts))
        groups.setdefault(key, {"key": key, "label": label, "runs": 0})["runs"] += 1
    latest = runs[0][1] if runs else None
    if group is None:
        group = latest
    elif group == "all":
        group = None
    elif group not in groups:
        raise NotFound(
            f"No group {group} in {stage}.", suggested_action="Pick a group from the list."
        )
    chosen = [r for r in runs if group is None or r[1] == group][:n]
    result.update(
        {
            "stage": stage,
            "queue": queue,
            "rate_unit": hist.RATE_UNITS.get(stage or ""),
            "time_unit": "s/epoch" if stage in hist.TRAINING_STAGES else "s",
            "groups": list(groups.values()),
            "group": group,
            "runs": [_history_row(run, key, attempts) for run, key, attempts in chosen],
        }
    )
    return result
