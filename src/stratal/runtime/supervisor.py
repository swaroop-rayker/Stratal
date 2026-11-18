"""The supervisor: what a Celery task actually does (ADR-0003, ARCHITECTURE.md §4.1).

It does no real work. It claims a stage in PostgreSQL, starts the child process
that runs it, sends heartbeats, enforces cancel, turns the child's exit into an
outcome, and queues the next stage. Nothing here imports Celery, so the whole
thing can be driven directly from tests.
"""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.core.logging import get_logger
from stratal.core.settings import Settings, get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Job, JobStage, ProcessSample
from stratal.mon import probes
from stratal.mon.backfill import backfill_job
from stratal.mon.events import Event, utc_now
from stratal.mon.store import insert_events, refresh_summary
from stratal.runtime.child import EXIT_CANCELLED, EXIT_OK, EXIT_PAUSED
from stratal.runtime.jobs import Enqueue, after_commit, cancel_unfinished_stages
from stratal.runtime.states import transition

Outcome = Literal["completed", "failed", "cancelled", "paused", "not_claimed", "superseded"]
EnqueueFn = Callable[[Enqueue], None]

# A wall-clock jump this much larger than the monotonic clock's means the machine slept.
SLEEP_JUMP_S = 30.0

log = get_logger(__name__)


@dataclass(frozen=True)
class Claim:
    attempt: int
    stage: str
    queue: str


def _pause_reason(job: Job) -> str:
    if job.pause_reason == "thermal":
        return "paused at a safe point: the GPU is too hot (RUN-1.6)"
    return "paused at a safe point by the user"


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim(job_id: str, idx: int, worker: str) -> Claim | None:
    """Take the stage if it is still waiting for a worker. One conditional step, under a
    row lock, so a task delivered twice starts the stage once."""
    with Session(get_engine()) as session, session.begin():
        job = session.get(Job, job_id, with_for_update=True)
        stage = session.get(JobStage, (job_id, idx))
        if job is None or stage is None or stage.status != "queued":
            return None
        if job.desired_state == "cancelled" and job.state in ("queued", "running"):
            transition(
                session, job, "cancelled", reason="cancelled before it started", actor="supervisor"
            )
            cancel_unfinished_stages(session, job_id)
            return None
        now = utc_now()
        if job.desired_state == "paused" and job.state == "running":
            # Paused between stages: this stage waits for the resume (RUN-1.2).
            stage.status = "paused"
            job.desired_state = None
            transition(session, job, "paused", reason=_pause_reason(job), actor="supervisor")
            return None
        if job.state == "queued":
            job.attempt += 1
            job.started_at = job.started_at or now
            transition(session, job, "running", reason=f"claimed by {worker}", actor="supervisor")
        elif job.state != "running":
            return None
        stage.status = "running"
        stage.started_at = now
        stage.finished_at = None
        stage.error = None
        stage.step = stage.total = None
        stage.throughput = stage.eta_seconds = None
        stage.last_progress_at = now
        job.current_stage_idx = idx
        job.worker = worker
        job.heartbeat_at = now
        return Claim(job.attempt, stage.name, stage.queue)


def heartbeat(job_id: str, attempt: int) -> tuple[bool, str | None]:
    """Record that this supervisor is alive. Returns whether it still owns the job, and
    the job's ``desired_state``."""
    with Session(get_engine()) as session, session.begin():
        row = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.attempt == attempt, Job.state.in_(("running", "stalled")))
            .values(heartbeat_at=func.now())
            .returning(Job.desired_state)
        ).first()
    return (row is not None, row[0] if row is not None else None)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


def describe_exit(
    code: int, *, oom_kills: int, peak_rss_mb: float | None, limit_mb: float | None
) -> dict[str, Any]:
    """Turn the exit status of a child that did not report its own error into one
    (BUG2). ``code`` is negative when a signal killed it."""
    if code < 0:
        name = _signal_name(-code)
        if -code == signal.SIGKILL and oom_kills > 0:
            peak = f" Its memory peaked at {peak_rss_mb:.0f} MB." if peak_rss_mb else ""
            limit = f" of {limit_mb:.0f} MB" if limit_mb else ""
            return {
                "code": "host_oom_killed",
                "reason": f"The kernel killed the stage for exceeding the container's "
                f"memory limit{limit}.{peak}",
                "suggested_action": "Use fewer loader workers or a smaller batch, then retry.",
                "detail": {"signal": name, "peak_rss_mb": peak_rss_mb, "limit_mb": limit_mb},
            }
        return {
            "code": "stage_failed",
            "reason": f"The stage's process was killed by {name} before it could report.",
            "suggested_action": "Retry the job; finished stages are kept. If it happens "
            "again, read the job log for what the stage was doing.",
            "detail": {"signal": name},
        }
    return {
        "code": "stage_failed",
        "reason": f"The stage's process exited with code {code} without reporting an error.",
        "suggested_action": "Read the job log, fix the cause, then retry the job.",
        "detail": {"exit_code": code},
    }


def _peak_rss(session: Session, job_id: str, stage: str) -> float | None:
    return session.execute(
        select(func.max(ProcessSample.rss_mb)).where(
            ProcessSample.job_id == job_id, ProcessSample.stage == stage
        )
    ).scalar_one_or_none()


def finalize(
    job_id: str,
    idx: int,
    held: Claim,
    code: int,
    *,
    cancel_requested: bool,
    oom_kills: int,
) -> tuple[Outcome, Enqueue | None]:
    """Record what the child's exit means, in one transaction."""
    with Session(get_engine()) as session, session.begin():
        job = session.get(Job, job_id, with_for_update=True)
        stage = session.get(JobStage, (job_id, idx))
        if job is None or stage is None:
            return "superseded", None
        if job.attempt != held.attempt or job.state not in ("running", "stalled"):
            return "superseded", None  # the watchdog gave this job to someone else
        if job.state == "stalled":  # the stage ended, so it was not hung after all (RUN-1.7)
            transition(session, job, "running", reason="the stage finished", actor="supervisor")
        now = utc_now()
        stage.finished_at = now
        cancel_requested = cancel_requested or job.desired_state == "cancelled"
        pause_requested = job.desired_state == "paused" and not cancel_requested
        following = session.get(JobStage, (job_id, idx + 1))
        outcome: Outcome
        enqueue: Enqueue | None = None

        if code == EXIT_OK:
            stage.status = "completed"
            if following is not None and pause_requested:
                # Paused between stages: the next one waits for the resume (RUN-1.2).
                following.status = "paused"
                following.input_version_id = stage.output_version_id
                job.current_stage_idx = following.idx
                job.desired_state = None
                transition(session, job, "paused", reason=_pause_reason(job), actor="supervisor")
                outcome = "paused"
            elif following is not None and not cancel_requested:
                following.status = "queued"
                following.input_version_id = stage.output_version_id
                job.current_stage_idx = following.idx
                enqueue = Enqueue(job_id, following.idx, following.queue)
                outcome = "completed"
            elif following is not None:
                transition(
                    session, job, "cancelled", reason="cancelled by the user", actor="supervisor"
                )
                cancel_unfinished_stages(session, job_id)
                outcome = "cancelled"
            else:
                job.output_version_id = stage.output_version_id
                transition(
                    session, job, "completed", reason="all stages completed", actor="supervisor"
                )
                outcome = "completed"
        elif code == EXIT_PAUSED and not cancel_requested:
            stage.status = "paused"
            job.desired_state = None
            transition(session, job, "paused", reason=_pause_reason(job), actor="supervisor")
            outcome = "paused"
        elif cancel_requested or code == EXIT_CANCELLED:
            stage.status = "cancelled"
            transition(
                session, job, "cancelled", reason="cancelled by the user", actor="supervisor"
            )
            cancel_unfinished_stages(session, job_id)
            outcome = "cancelled"
        else:
            reported = stage.error is not None
            error = stage.error or describe_exit(
                code,
                oom_kills=oom_kills,
                peak_rss_mb=_peak_rss(session, job_id, stage.name),
                limit_mb=probes.memory_limit_mb(),
            )
            stage.status = "failed"
            stage.error = error
            job.error = {**error, "stage": stage.name}
            if not reported:  # the child died without a word: say it for it
                insert_events(
                    session,
                    [
                        Event(
                            job_id=job_id,
                            stage=stage.name,
                            attempt=held.attempt,
                            type="stage_failed",
                            level="ERROR",
                            message=f"Stage {stage.name} failed: {error['reason']}",
                            reason=str(error["reason"]),
                            suggested_action=str(error["suggested_action"]),
                            metrics={"code": str(error["code"])},
                        )
                    ],
                )
            transition(
                session,
                job,
                "failed",
                reason=f"stage {stage.name} failed: {error['reason']}",
                actor="supervisor",
            )
            outcome = "failed"
        session.flush()
        refresh_summary(session, job_id, now=now)
        return outcome, enqueue


def run_stage(
    job_id: str,
    idx: int,
    *,
    enqueue: EnqueueFn,
    settings: Settings | None = None,
    poll_s: float = 0.25,
) -> Outcome:
    """Supervise one stage of one job from claim to outcome."""
    settings = settings or get_settings()
    worker = worker_name()
    held = claim(job_id, idx, worker)
    bus = Bus(settings.redis_url, timeout_s=2.0)
    if held is None:
        after_commit(bus, job_id)
        log.info("stage_not_claimed", job_id=job_id, stage_idx=idx)
        return "not_claimed"

    with contextlib.suppress(Exception):
        bus.set_active(job_id, stage=held.stage, queue=held.queue, since=utc_now().isoformat())
    after_commit(bus, job_id)

    oom_before = probes.oom_kill_count() or 0
    command = [sys.executable, "-m", "stratal.runtime.child"]
    command += ["--job", job_id, "--stage", str(idx), "--attempt", str(held.attempt)]
    # Its own session and process group: one signal reaches the stage and anything it started.
    process = subprocess.Popen(command, start_new_session=True)
    log.info("child_started", job_id=job_id, stage=held.stage, child_pid=process.pid)

    cancel_requested = False
    cancel_at: float | None = None
    last_beat = time.monotonic()
    last_wall, last_mono = time.time(), time.monotonic()
    try:
        while True:
            try:
                code = process.wait(timeout=poll_s)
                break
            except subprocess.TimeoutExpired:
                pass
            wall, mono = time.time(), time.monotonic()
            slept = (wall - last_wall) - (mono - last_mono)
            last_wall, last_mono = wall, mono
            if slept > SLEEP_JUMP_S:
                _report_sleep(job_id, held, slept)
                last_beat = 0.0  # prove we are alive before the watchdog decides otherwise

            if mono - last_beat >= settings.heartbeat_interval_s:
                last_beat = mono
                try:
                    owned, desired = heartbeat(job_id, held.attempt)
                except Exception as exc:  # PostgreSQL blip: keep the child running
                    log.warning("heartbeat_failed", job_id=job_id, reason=str(exc))
                else:
                    if not owned:
                        log.warning("job_superseded", job_id=job_id, attempt=held.attempt)
                        _kill_group(process)
                        process.wait()
                        return "superseded"
                    cancel_requested = cancel_requested or desired == "cancelled"
            if not cancel_requested:
                with contextlib.suppress(Exception):
                    cancel_requested = bus.get_control(job_id) == "cancel"
            if cancel_requested:
                cancel_at = cancel_at if cancel_at is not None else mono
                if mono - cancel_at > settings.cancel_grace_s:
                    log.warning("cancel_grace_expired", job_id=job_id, child_pid=process.pid)
                    _kill_group(process)
    except BaseException:
        _kill_group(process)  # never leave a child behind a dying supervisor
        raise
    finally:
        with contextlib.suppress(Exception):
            bus.clear_active(job_id)

    _kill_group(process)  # anything the stage left running in its group
    oom_kills = (probes.oom_kill_count() or 0) - oom_before
    outcome, following = finalize(
        job_id, idx, held, code, cancel_requested=cancel_requested, oom_kills=oom_kills
    )
    log.info("stage_finished", job_id=job_id, stage=held.stage, exit_code=code, outcome=outcome)
    _backfill(job_id, settings)
    if outcome != "superseded":
        with contextlib.suppress(Exception):
            if following is None or outcome == "paused":
                bus.clear_control(job_id)
        after_commit(bus, job_id)
        if following is not None:
            enqueue(following)
    with contextlib.suppress(Exception):
        bus.close()
    return outcome


def _backfill(job_id: str, settings: Settings) -> None:
    """Store any of the job's spooled events the collector never got (ADR-0005, MON-1.4).

    The child has exited, so its spool is complete. A failure here is logged and
    never changes the job's outcome; ``stratal.cli events backfill`` can redo it.
    """
    try:
        with Session(get_engine()) as session, session.begin():
            backfill_job(session, settings.data_dir, job_id)
    except Exception as exc:
        log.warning("events_backfill_failed", job_id=job_id, reason=str(exc))


def _report_sleep(job_id: str, held: Claim, seconds: float) -> None:
    """Put a detected suspend on the job's timeline (ADR-0008, BUG4)."""
    with contextlib.suppress(Exception), Session(get_engine()) as session, session.begin():
        insert_events(
            session,
            [
                Event(
                    job_id=job_id,
                    stage=held.stage,
                    attempt=held.attempt,
                    type="system",
                    level="WARN",
                    message=f"The machine was suspended for about {seconds:.0f} s",
                    reason="The wall clock jumped ahead of the monotonic clock, which "
                    "happens when the laptop sleeps or the container is frozen.",
                    suggested_action="None needed if the job continues. Keep the laptop "
                    "awake and on mains power during long jobs.",
                    metrics={"suspended_s": round(seconds, 1)},
                )
            ],
        )
