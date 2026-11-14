"""Watchdog v1: find running jobs whose supervisor has gone quiet (ARCHITECTURE.md §4.3).

A supervisor writes ``jobs.heartbeat_at`` every few seconds. If it stops (the
worker container died or froze), the watchdog puts the job back in the queue,
where it resumes in a fresh process from its last committed work. After too
many attempts it fails the job instead. Stall detection (RUN-1.7) is phase 2.
"""

import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from stratal.core.logging import get_logger
from stratal.db.models import Job, JobStage
from stratal.mon.events import Event
from stratal.mon.store import insert_events
from stratal.runtime.jobs import Enqueue
from stratal.runtime.states import transition

log = get_logger(__name__)

SLEEP_JUMP_S = 30.0


class Watchdog:
    def __init__(
        self,
        engine: Engine,
        *,
        enqueue: Callable[[Enqueue], None],
        stale_after_s: float = 30.0,
        max_attempts: int = 3,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._enqueue = enqueue
        self.stale_after_s = stale_after_s
        self.max_attempts = max_attempts
        self._wall = wall
        self._mono = mono
        self._last = (wall(), mono())
        self._grace_until = 0.0

    def _slept(self) -> float:
        """Seconds the machine was suspended since the last check, if any (ADR-0008)."""
        wall, mono = self._wall(), self._mono()
        jump = (wall - self._last[0]) - (mono - self._last[1])
        self._last = (wall, mono)
        return jump if jump > SLEEP_JUMP_S else 0.0

    def run_once(self) -> list[str]:
        """Check every running job once. Returns the IDs of jobs it acted on."""
        slept = self._slept()
        if slept:
            # Every heartbeat is stale by the length of the sleep. That is not a dead
            # worker: give the supervisors time to write a fresh one.
            self._grace_until = self._mono() + self.stale_after_s
            log.warning("suspend_detected", suspended_s=round(slept, 1))
            self._record_suspend(slept)
        if self._mono() < self._grace_until:
            return []

        acted: list[str] = []
        to_enqueue: list[Enqueue] = []
        with Session(self._engine) as session, session.begin():
            cutoff = func.now() - timedelta(seconds=self.stale_after_s)
            stale = session.scalars(
                select(Job)
                .where(Job.state == "running", Job.heartbeat_at < cutoff)
                .with_for_update(skip_locked=True)
            ).all()
            for job in stale:
                stage = session.scalars(
                    select(JobStage).where(JobStage.job_id == job.id, JobStage.status == "running")
                ).first()
                acted.append(job.id)
                reason = (
                    f"No heartbeat from worker {job.worker} for more than "
                    f"{self.stale_after_s:.0f} s; the worker was lost or frozen."
                )
                if stage is not None and job.attempt < self.max_attempts:
                    stage.status = "queued"
                    transition(session, job, "queued", reason=reason, actor="watchdog")
                    insert_events(
                        session,
                        [
                            Event(
                                job_id=job.id,
                                stage=stage.name,
                                attempt=job.attempt,
                                type="action",
                                level="WARN",
                                message=f"Worker lost; job requeued (attempt {job.attempt} "
                                f"of {self.max_attempts})",
                                reason=reason,
                                suggested_action="None needed: the job resumes from its last "
                                "committed work when a worker is free.",
                                metrics={"code": "worker_lost"},
                            )
                        ],
                    )
                    to_enqueue.append(Enqueue(job.id, stage.idx, stage.queue))
                else:
                    error = {
                        "code": "worker_lost",
                        "reason": reason,
                        "suggested_action": "Check the worker containers with "
                        "'docker compose ps', then retry the job; finished work is kept.",
                        "detail": {"attempt": job.attempt},
                    }
                    if stage is not None:
                        stage.status = "failed"
                        stage.error = error
                    job.error = error
                    transition(session, job, "failed", reason=reason, actor="watchdog")
        for item in to_enqueue:
            self._enqueue(item)
        return acted

    def _record_suspend(self, seconds: float) -> None:
        try:
            with Session(self._engine) as session, session.begin():
                insert_events(
                    session,
                    [
                        Event(
                            type="system",
                            level="WARN",
                            message=f"The machine was suspended for about {seconds:.0f} s",
                            reason="The wall clock jumped ahead of the monotonic clock.",
                            suggested_action="None needed. Running jobs are given "
                            f"{self.stale_after_s:.0f} s to show they survived.",
                            metrics={"suspended_s": round(seconds, 1)},
                        )
                    ],
                )
        except Exception as exc:
            log.warning("suspend_event_failed", reason=str(exc)[:300])
