"""Watchdog v2 (ARCHITECTURE.md §4.3, ADR-0014 rule 7).

Three checks, every few seconds:

1. **Lost worker.** A supervisor writes ``jobs.heartbeat_at`` every few seconds.
   If it stops (the worker container died or froze), the job goes back in the
   queue and resumes in a fresh process from its last committed work; after too
   many attempts it fails instead.
2. **Stall (RUN-1.7).** A stage whose heartbeat is fresh but which has reported
   no progress or activity for ``stall_after_s`` becomes ``stalled``; when
   progress returns it is ``running`` again.
3. **Heat (RUN-1.6).** At ``thermal_pause_c`` the running GPU job is asked to
   pause, with the reason ``thermal``; below ``thermal_resume_c`` the jobs paused
   for that reason, and only those, are resumed.
"""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.core.logging import get_logger
from stratal.db.models import Job, JobStage, ResourceSample
from stratal.mon.events import Event, MetricValue
from stratal.mon.store import insert_events
from stratal.runtime import jobs as job_service
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
        bus: Bus | None = None,
        stall_after_s: float = 300.0,
        thermal_pause_c: float = 83.0,
        thermal_resume_c: float = 75.0,
        temperature: Callable[[], float | None] | None = None,
        limits: Callable[[], tuple[float, float, float]] | None = None,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._enqueue = enqueue
        self.stale_after_s = stale_after_s
        self.max_attempts = max_attempts
        self._bus = bus
        self.stall_after_s = stall_after_s
        self.thermal_pause_c = thermal_pause_c
        self.thermal_resume_c = thermal_resume_c
        self._temperature = temperature or self._latest_gpu_temperature
        self._limits = limits
        """``(stall, pause, resume)`` read before every check: the settings saved on SCR-14."""
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
        if self._limits is not None:
            try:
                self.stall_after_s, self.thermal_pause_c, self.thermal_resume_c = self._limits()
            except Exception as exc:  # keep the last limits read; a blip must not stop checks
                log.warning("limits_not_read", reason=str(exc)[:200])
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
                .where(Job.state.in_(("running", "stalled")), Job.heartbeat_at < cutoff)
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
        acted += self._check_stalls()
        acted += self._check_heat()
        return acted

    # ------------------------------------------------------------------ RUN-1.7
    def _check_stalls(self) -> list[str]:
        """Mark live but silent stages ``stalled``, and recover the ones that speak again."""
        acted: list[str] = []
        with Session(self._engine) as session, session.begin():
            fresh = func.now() - timedelta(seconds=self.stale_after_s)
            now = session.scalar(select(func.now()))
            assert now is not None
            quiet = now - timedelta(
                seconds=self.stall_after_s
            )  # the database's clock, as the events'
            rows = session.execute(
                select(Job, JobStage)
                .join(JobStage, (JobStage.job_id == Job.id) & (JobStage.status == "running"))
                .where(Job.state.in_(("running", "stalled")), Job.heartbeat_at >= fresh)
                .with_for_update(of=Job, skip_locked=True)
            ).all()
            for job, stage in rows:
                silent = stage.last_progress_at is None or stage.last_progress_at < quiet
                if job.state == "running" and silent:
                    reason = (
                        f"No progress or activity from stage {stage.name} for more than "
                        f"{self.stall_after_s:.0f} s, while its worker is alive."
                    )
                    transition(session, job, "stalled", reason=reason, actor="watchdog")
                    self._event(
                        session,
                        job,
                        stage.name,
                        "WARN",
                        f"No progress for {self.stall_after_s:.0f} s: job marked stalled",
                        code="stalled",
                        reason=reason,
                        action="Open the job's log to see what the stage was doing. It "
                        "becomes running again by itself if progress returns; otherwise "
                        "cancel and retry it.",
                    )
                    acted.append(job.id)
                elif job.state == "stalled" and not silent:
                    transition(session, job, "running", reason="progress resumed", actor="watchdog")
                    self._event(
                        session, job, stage.name, "INFO", "Progress resumed: job running again"
                    )
                    acted.append(job.id)
        return acted

    # ------------------------------------------------------------------ RUN-1.6
    def _latest_gpu_temperature(self) -> float | None:
        """The GPU temperature of the monitor's newest sample, if it is recent."""
        with Session(self._engine) as session:
            return session.scalar(
                select(ResourceSample.gpu_temp_c)
                .where(
                    ResourceSample.ts >= func.now() - timedelta(seconds=30),
                    ResourceSample.gpu_temp_c.is_not(None),
                )
                .order_by(ResourceSample.ts.desc())
                .limit(1)
            )

    def _check_heat(self) -> list[str]:
        """Pause the running GPU job when the card is too hot; resume thermal pauses when cool."""
        temperature = self._temperature()
        if temperature is None:
            return []
        if temperature >= self.thermal_pause_c:
            return self._pause_for_heat(temperature)
        if temperature < self.thermal_resume_c:
            return self._resume_after_heat(temperature)
        return []

    def _pause_for_heat(self, temperature: float) -> list[str]:
        acted: list[str] = []
        with Session(self._engine) as session, session.begin():
            rows = session.execute(
                select(Job.id, JobStage.name)
                .join(JobStage, (JobStage.job_id == Job.id) & (JobStage.status == "running"))
                .where(Job.state == "running", JobStage.queue == "gpu", Job.desired_state.is_(None))
            ).all()
            for job_id, stage_name in rows:
                job = job_service.request_pause(session, job_id, reason="thermal")
                self._event(
                    session,
                    job,
                    stage_name,
                    "WARN",
                    f"GPU at {temperature:.0f} °C: pausing {job_id} "
                    f"(limit {self.thermal_pause_c:.0f} °C)",
                    code="thermal_pause",
                    reason=f"The GPU reached {temperature:.0f} °C; the limit is "
                    f"{self.thermal_pause_c:.0f} °C.",
                    action=f"None needed: it resumes by itself below "
                    f"{self.thermal_resume_c:.0f} °C. Check the laptop's airflow.",
                    temperature=temperature,
                )
                acted.append(job_id)
        for job_id in acted:
            self._set_control(job_id, "pause")
        return acted

    def _resume_after_heat(self, temperature: float) -> list[str]:
        acted: list[str] = []
        to_enqueue: list[Enqueue] = []
        with Session(self._engine) as session, session.begin():
            paused = session.scalars(
                select(Job.id).where(Job.state == "paused", Job.pause_reason == "thermal")
            ).all()
            for job_id in paused:
                job, item = job_service.request_resume(
                    session,
                    job_id,
                    actor="watchdog",
                    reason=f"the GPU cooled to {temperature:.0f} °C (RUN-1.6)",
                )
                self._event(
                    session,
                    job,
                    None,
                    "INFO",
                    f"GPU cooled to {temperature:.0f} °C: resuming {job_id}",
                    code="thermal_resume",
                    temperature=temperature,
                )
                to_enqueue.append(item)
                acted.append(job_id)
        for item in to_enqueue:
            self._set_control(item.job_id, None)
            self._enqueue(item)
        return acted

    def _set_control(self, job_id: str, value: str | None) -> None:
        if self._bus is None:
            return
        try:
            if value is None:
                self._bus.clear_control(job_id)
            else:
                self._bus.set_control(job_id, value)
        except Exception as exc:  # jobs.desired_state stays the durable copy
            log.warning("control_flag_failed", job_id=job_id, reason=str(exc)[:300])

    def _event(
        self,
        session: Session,
        job: Job,
        stage: str | None,
        level: Literal["INFO", "WARN"],
        message: str,
        *,
        code: str | None = None,
        reason: str | None = None,
        action: str | None = None,
        temperature: float | None = None,
    ) -> None:
        metrics: dict[str, MetricValue] = {}
        if code is not None:
            metrics["code"] = code
        if temperature is not None:
            metrics["gpu_temp_c"] = round(temperature, 1)
        insert_events(
            session,
            [
                Event(
                    job_id=job.id,
                    stage=stage,
                    attempt=job.attempt,
                    type="action",
                    level=level,
                    message=message,
                    reason=reason,
                    suggested_action=action,
                    metrics=metrics or None,
                )
            ],
        )

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
