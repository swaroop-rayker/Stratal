"""The collector: Redis stream → PostgreSQL → live viewers (ADR-0005, MON-1.4).

Reads the stream as a consumer group, so an entry stays in Redis until it has
been stored and acknowledged. If the collector dies between the two, the entry
is delivered again and the unique ``event_uid`` makes the second insert a no-op.
"""

import contextlib
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from stratal.core.bus import LIVE_GLOBAL, Bus, StreamEntry, live_channel
from stratal.core.logging import get_logger
from stratal.db.models import Job, JobStage, ProcessSample
from stratal.mon.emitter import KIND_EVENT, KIND_PROCESS_SAMPLE
from stratal.mon.events import Event
from stratal.mon.store import insert_events, refresh_summary

log = get_logger(__name__)

_PROCESS_FIELDS = (
    "ts",
    "job_id",
    "stage",
    "rss_mb",
    "torch_allocated_mb",
    "torch_reserved_mb",
    "torch_max_allocated_mb",
    "cpu_cores",
)


def apply_to_stage(session: Session, event: Event) -> None:
    """Keep the stage's row current from an event, so views read one row instead of
    replaying the log (ARCHITECTURE.md §5.1)."""
    if event.job_id is None or event.stage is None or event.type == "job_state":
        return
    stage = session.scalars(
        select(JobStage)
        .where(JobStage.job_id == event.job_id, JobStage.name == event.stage)
        .order_by((JobStage.status == "running").desc(), JobStage.idx)
    ).first()
    if stage is None:
        return
    if event.level != "DEBUG" and event.type in ("activity", "stage_progress", "stage_started"):
        stage.activity = event.message
        # Activity is a sign of life too: long silent phases report activity at least
        # once a minute, so they are not taken for a hang (RUN-1.7, ADR-0014 rule 7).
        stage.last_progress_at = event.ts
    if event.metrics:
        stage.metrics = {**(stage.metrics or {}), **event.metrics}
    if event.step is not None:
        stage.step = event.step
        stage.total = event.total
        stage.last_progress_at = event.ts
        metrics = event.metrics or {}
        rate, eta = metrics.get("items_per_s"), metrics.get("eta_s")
        if isinstance(rate, int | float):
            stage.throughput = float(rate)
        stage.eta_seconds = float(eta) if isinstance(eta, int | float) else None
    if event.type in ("stage_completed", "stage_failed"):
        stage.eta_seconds = 0.0 if event.type == "stage_completed" else None
        session.flush()
        refresh_summary(session, event.job_id, now=event.ts)


def write_batch(session: Session, events: list[Event], samples: list[dict[str, Any]]) -> None:
    """Store one batch and bring the stage rows up to date, inside the caller's transaction.

    Lock order (E-009): every writer that changes a job's stages takes the job row
    first, as the supervisor, the watchdog and the job service do. So the rows of
    the jobs this batch touches are locked here, in ID order, before any stage row.
    ``FOR NO KEY UPDATE`` is the lock an ``UPDATE`` of the row takes anyway; it does
    not block inserts that only reference the job.
    """
    job_ids = sorted({e.job_id for e in events if e.job_id is not None and e.stage is not None})
    if job_ids:
        session.execute(
            select(Job.id)
            .where(Job.id.in_(job_ids))
            .order_by(Job.id)
            .with_for_update(key_share=True)
        ).all()
    insert_events(session, events)
    if samples:
        session.execute(insert(ProcessSample), samples)
    for event in events:
        apply_to_stage(session, event)


class Collector:
    def __init__(self, bus: Bus, engine: Engine, *, consumer: str = "collector-1") -> None:
        self._bus = bus
        self._engine = engine
        self._consumer = consumer
        self._recovering = True
        self._group_ready = False

    def run_once(self, *, block_ms: int = 1000, count: int = 200) -> int:
        """Store one batch. Returns how many stream entries were handled."""
        if not self._group_ready:
            self._bus.ensure_group()
            self._group_ready = True
        entries: list[StreamEntry] = []
        if self._recovering:
            # First, whatever a previous run read but never acknowledged.
            entries = self._bus.read_group(self._consumer, count=count, block_ms=0, pending=True)
            self._recovering = bool(entries)
        if not entries:
            entries = self._bus.read_group(self._consumer, count=count, block_ms=block_ms)
        if entries:
            self._store(entries)
        return len(entries)

    def _store(self, entries: list[StreamEntry]) -> None:
        events: list[Event] = []
        samples: list[dict[str, Any]] = []
        for entry in entries:
            try:
                if entry.kind == KIND_EVENT:
                    events.append(Event.model_validate_json(entry.data))
                elif entry.kind == KIND_PROCESS_SAMPLE:
                    raw: dict[str, Any] = json.loads(entry.data)
                    sample = {name: raw.get(name) for name in _PROCESS_FIELDS}
                    sample["ts"] = datetime.fromisoformat(str(raw["ts"]))
                    samples.append(sample)
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                # It can never become valid, so it is acknowledged and dropped, loudly.
                log.error("stream_entry_rejected", entry_id=entry.entry_id, reason=str(exc)[:300])

        with Session(self._engine) as session, session.begin():
            write_batch(session, events, samples)
        self._bus.ack([entry.entry_id for entry in entries])

        with contextlib.suppress(Exception):  # live viewers are best-effort (NFR-13)
            for job_id in sorted({e.job_id for e in events if e.job_id is not None}):
                for kind in ("event", "job"):
                    message = {"kind": kind, "job_id": job_id}
                    self._bus.publish(live_channel(job_id), message)
                    self._bus.publish(LIVE_GLOBAL, message)
            if any(e.job_id is None for e in events):
                self._bus.publish(LIVE_GLOBAL, {"kind": "event", "job_id": None})
            for sample in samples:
                payload = {**sample, "ts": sample["ts"].isoformat()}
                self._bus.publish(
                    live_channel(str(sample["job_id"])), {"kind": "sample", "process": payload}
                )
