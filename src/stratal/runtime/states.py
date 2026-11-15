"""The job state machine (RUN-1.1, ARCHITECTURE.md §4.2, INV-08).

Every change of ``jobs.state`` goes through :func:`transition`. It rejects any
move that is not in :data:`TRANSITIONS`, and writes the ``job_transitions`` row
and a ``job_state`` event in the caller's transaction, so a state can never
change without leaving both records.
"""

from typing import Literal

from sqlalchemy.orm import Session

from stratal.core.errors import InvalidStateTransition
from stratal.db.models import Job, JobTransition
from stratal.mon.events import Event, Level, utc_now
from stratal.mon.store import insert_events

Actor = Literal["api", "supervisor", "watchdog", "user"]

TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"completed", "failed", "cancelled", "paused", "stalled", "queued"}),
    "paused": frozenset({"queued", "cancelled"}),
    "stalled": frozenset({"running", "failed", "cancelled", "queued"}),
    "failed": frozenset({"queued"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
TERMINAL = frozenset({"completed", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "stalled"})


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, frozenset())


def check_transition(job_id: str, from_state: str, to_state: str) -> None:
    if not can_transition(from_state, to_state):
        allowed = ", ".join(sorted(TRANSITIONS.get(from_state, frozenset()))) or "nothing"
        raise InvalidStateTransition(
            f"Job {job_id} is {from_state} and cannot become {to_state}.",
            suggested_action="The job has finished. Start a new run instead."
            if from_state in ("completed", "cancelled")
            else f"From {from_state} a job can only become: {allowed}.",
        )


def _log_change(
    session: Session, job: Job, from_state: str | None, reason: str, actor: Actor
) -> None:
    session.add(
        JobTransition(
            job_id=job.id, from_state=from_state, to_state=job.state, reason=reason, actor=actor
        )
    )
    level: Level = "ERROR" if job.state == "failed" else "INFO"
    error = job.error or {}
    event = Event(
        job_id=job.id,
        type="job_state",
        level=level,
        attempt=job.attempt,
        message=f"Job {job.state}" if from_state is None else f"Job {from_state} → {job.state}",
        metrics={"from": from_state or "", "to": job.state, "actor": actor},
        reason=reason if level == "ERROR" else None,
        suggested_action=(
            str(error.get("suggested_action") or "Read the stage's error, then retry the job.")
            if level == "ERROR"
            else None
        ),
    )
    insert_events(session, [event])


def record_creation(session: Session, job: Job, *, actor: Actor = "api") -> None:
    """Log the creation of a job, which starts in ``queued``."""
    _log_change(session, job, None, "created", actor)


def transition(session: Session, job: Job, to_state: str, *, reason: str, actor: Actor) -> None:
    """Move ``job`` to ``to_state`` or raise :class:`InvalidStateTransition`."""
    from_state = job.state
    check_transition(job.id, from_state, to_state)
    job.state = to_state
    if to_state in TERMINAL:
        job.finished_at = utc_now()
        job.desired_state = None
    elif to_state == "queued":
        job.finished_at = None
    session.flush()
    _log_change(session, job, from_state, reason, actor)
