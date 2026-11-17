"""Event backfill from a job's spool file (ADR-0005 decision 2, MON-1.4).

Every event a job emits is first appended to ``jobs/<job_id>/events.jsonl``. If
Redis was down, or the stream entry was lost, the event never reached the
collector; the spool still has it. Backfilling inserts every spooled event that
PostgreSQL does not have. The insert skips known ``event_uid`` values, so a
backfill can run any number of times, before or after the collector.

The supervisor backfills a job after each of its stages, when the child has
exited and its spool is complete. ``python -m stratal.cli events backfill``
does the same by hand, for a job whose last worker died.
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from stratal.core.logging import get_logger
from stratal.mon.events import Event
from stratal.mon.store import insert_events

log = get_logger(__name__)

SPOOL_NAME = "events.jsonl"
BATCH = 500


def spool_path(data_dir: Path, job_id: str) -> Path:
    return data_dir / "jobs" / job_id / SPOOL_NAME


@dataclass(frozen=True)
class Backfilled:
    read: int
    """Valid events in the spool."""
    inserted: int
    """Events PostgreSQL did not have."""
    skipped: int
    """Lines that are not a valid event, such as a last line cut off by a crash."""


def backfill_job(session: Session, data_dir: Path, job_id: str) -> Backfilled:
    """Insert the job's spooled events that are missing, inside the caller's transaction."""
    path = spool_path(data_dir, job_id)
    if not path.is_file():
        return Backfilled(0, 0, 0)
    read = inserted = skipped = 0
    batch: list[Event] = []
    with path.open(encoding="utf-8") as spool:
        for line in spool:
            if not line.strip():
                continue
            try:
                event = Event.model_validate_json(line)
            except ValidationError:
                skipped += 1
                continue
            if event.job_id != job_id:
                skipped += 1
                continue
            read += 1
            batch.append(event)
            if len(batch) >= BATCH:
                inserted += insert_events(session, batch)
                batch = []
    inserted += insert_events(session, batch)
    if inserted or skipped:
        log.info("events_backfilled", job_id=job_id, inserted=inserted, skipped=skipped, read=read)
    return Backfilled(read, inserted, skipped)
