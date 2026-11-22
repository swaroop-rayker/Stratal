"""Watchdog v2 (ARCHITECTURE.md §4.3, ADR-0014 rule 7).

Three checks, every few seconds:

1. **Lost worker.** A supervisor writes ``jobs.heartbeat_at`` every few seconds.
   If it stops (the worker container died or froze), the job goes back in the
   queue and resumes in a fresh process from its last committed work; after too
   many attempts it fails instead.
2. **Stall (RUN-1.7).** A stage whose heartbeat is fresh but which has reported
   no progress or activity for ``stall_after_s`` becomes ``stalled``; when
   progress returns it is ``running`` again.
3. **Heat (RUN-1.6).** The ``gpu_hot`` alert (:mod:`stratal.mon.alerts`, run by the
   sampler) calls :meth:`Watchdog.pause_for_heat` once the GPU has been at
   ``thermal_pause_c`` for 5 s: the running GPU job is asked to pause, with the reason
   ``thermal``, its action recording the alert (ADR-0020). Below ``thermal_resume_c``
   the jobs paused for that reason, and only those, are resumed here.
5. **Diagnostics (MON-3.2, ADR-0020 part B).** Each running GPU stage's last 30 s
   against :func:`stratal.mon.diagnose.diagnose`; a diagnosis is a ``diagnostic``
   event, once per crossing and at most every 10 minutes per stage. It does not act
   on the job, so it is not in :meth:`Watchdog.run_once`'s result.
4. **Waiting studies (ADR-0018 rule 5).** A deep-learning study between its steps has
   no process and no heartbeat; it is checked through its current trial instead. A
   trial still queued, running or paused means all is well; a trial that ended with
   no step queued (a lost message) gets the step queued here. A queued step nobody
   took for ``stale_after_s`` is sent again (a duplicate is claimed once).
"""

import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.core.logging import get_logger
from stratal.db.models import Event as EventRow
from stratal.db.models import Job, JobStage, ResourceSample
from stratal.hpo import control as hpo_control
from stratal.mon import diagnose as diag
from stratal.mon.events import Event, MetricValue
from stratal.mon.store import insert_events
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue
from stratal.runtime.states import TERMINAL, transition

log = get_logger(__name__)

SLEEP_JUMP_S = 30.0
DIAGNOSIS_EVERY_S = 600.0
"""A stage gets the same diagnosis at most once in this long (ADR-0020 rule 8)."""


class _Diagnosed:
    def __init__(self, uid: uuid.UUID, at: float) -> None:
        self.uid = uid
        self.at = at
        self.open = True


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
        disk_threshold: Callable[[], float] | None = None,
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
        self._disk_threshold = disk_threshold or (lambda: 50.0)
        self._diagnosed: dict[tuple[str, str, int], _Diagnosed] = {}

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
                if stage is None and job.kind == "hpo_study":
                    continue  # between steps: checked through its trial (check 4)
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
        acted += self._check_waiting()
        acted += self._check_stalls()
        acted += self._check_heat()
        try:
            self.check_diagnostics()
        except Exception as exc:  # a hint must never stop the checks that act
            log.warning("diagnostics_failed", reason=str(exc)[:300])
        return acted

    # ------------------------------------------------------------------ MON-3.2
    def check_diagnostics(self) -> list[str]:
        """Diagnose each running GPU stage; returns the codes written (tests)."""
        written: list[str] = []
        try:
            disk = float(self._disk_threshold())
        except Exception:
            disk = 50.0
        with Session(self._engine) as session, session.begin():
            now = session.scalar(select(func.now()))
            assert now is not None
            rows = session.execute(
                select(Job, JobStage)
                .join(JobStage, (JobStage.job_id == Job.id) & (JobStage.status == "running"))
                .where(Job.state == "running", JobStage.queue == "gpu")
            ).all()
            live = {(job.id, stage.name, job.attempt) for job, stage in rows}
            for key in [k for k in self._diagnosed if k not in live]:
                del self._diagnosed[key]
            for job, stage in rows:
                key = (job.id, stage.name, job.attempt)
                window = diag.read_window(
                    session, job_id=job.id, stage=stage.name, started_at=stage.started_at, now=now
                )
                seen = window.observed
                found = diag.diagnose(
                    seen,
                    queue=stage.queue,
                    running_for_s=window.running_for_s,
                    disk_threshold_mb_s=disk,
                )
                last = self._diagnosed.get(key)
                if last is not None and last.open:
                    busy = (
                        seen.samples >= diag.MIN_SAMPLES
                        and seen.gpu_percent is not None
                        and seen.gpu_percent >= diag.GPU_LOW
                    )
                    if busy:
                        last.open = False
                        self._diagnostic(
                            session,
                            job,
                            stage.name,
                            "INFO",
                            f"GPU busy again: {seen.gpu_percent:.0f}%",
                            seen,
                            state="cleared",
                            caused_by=last.uid,
                        )
                        written.append("cleared")
                    continue
                if found is None:
                    continue
                if last is not None and self._mono() - last.at < DIAGNOSIS_EVERY_S:
                    continue
                uid = uuid.uuid4()
                self._diagnosed[key] = _Diagnosed(uid, self._mono())
                self._diagnostic(
                    session,
                    job,
                    stage.name,
                    "WARN",
                    found.message,
                    seen,
                    state="fired",
                    code=found.code,
                    reason=found.reason,
                    action=found.action,
                    uid=uid,
                )
                written.append(found.code)
        return written

    def _diagnostic(
        self,
        session: Session,
        job: Job,
        stage: str,
        level: Literal["INFO", "WARN"],
        message: str,
        seen: diag.Observed,
        *,
        state: str,
        code: str | None = None,
        reason: str | None = None,
        action: str | None = None,
        uid: uuid.UUID | None = None,
        caused_by: uuid.UUID | None = None,
    ) -> None:
        metrics: dict[str, MetricValue] = {"state": state, "samples": seen.samples}
        if code is not None:
            metrics["code"] = code
        if seen.gpu_percent is not None:
            metrics["gpu_percent"] = round(seen.gpu_percent, 1)
        if seen.cpu_cores is not None:
            metrics["cpu_cores"] = round(seen.cpu_cores, 2)
        metrics["capacity_cores"] = seen.capacity_cores
        metrics["loader_workers"] = seen.loader_workers
        metrics["disk_read_mb_s"] = round(seen.disk_read_mb_s, 1)
        insert_events(
            session,
            [
                Event(
                    event_uid=uid or uuid.uuid4(),
                    job_id=job.id,
                    stage=stage,
                    attempt=job.attempt,
                    type="diagnostic",
                    level=level,
                    message=message,
                    reason=reason,
                    suggested_action=action,
                    metrics=metrics,
                    caused_by=caused_by,
                )
            ],
        )

    # ------------------------------------------------------------------ ADR-0018 rule 5
    def _check_waiting(self) -> list[str]:
        """Queue the step of a study whose trial ended without queueing it; resend a step
        that has sat in the queue unclaimed."""
        acted: list[str] = []
        to_enqueue: list[Enqueue] = []
        with Session(self._engine) as session, session.begin():
            cutoff = func.now() - timedelta(seconds=self.stale_after_s)
            rows = session.execute(
                select(Job, JobStage)
                .join(
                    JobStage, (JobStage.job_id == Job.id) & (JobStage.idx == Job.current_stage_idx)
                )
                .where(
                    Job.kind == "hpo_study",
                    Job.state == "running",
                    JobStage.status.in_(("waiting", "queued")),
                )
                .with_for_update(of=Job, skip_locked=True)
            ).all()
            for job, stage in rows:
                if stage.status == "queued":
                    fresh = session.scalar(
                        select(Job.id).where(Job.id == job.id, Job.heartbeat_at >= cutoff)
                    )
                    if fresh is not None:
                        continue
                    job.heartbeat_at = func.now()
                    to_enqueue.append(Enqueue(job.id, stage.idx, stage.queue))
                    acted.append(job.id)
                    continue
                study = hpo_control.study_of_job(session, job.id)
                found = hpo_control.current_trial(session, study.id) if study else None
                if found is not None and found[1].state not in TERMINAL:
                    continue  # the trial is queued, running or paused: all is well
                stage.status = "queued"
                job.heartbeat_at = func.now()
                self._event(
                    session,
                    job,
                    stage.name,
                    "WARN",
                    "The study's trial ended but its next step was never queued: queued it",
                    code="study_step_requeued",
                    reason="The message that queues the next step was lost, or its worker "
                    "stopped before sending it.",
                    action="None needed: the study continues.",
                )
                to_enqueue.append(Enqueue(job.id, stage.idx, stage.queue))
                acted.append(job.id)
        for item in to_enqueue:
            self._enqueue(item)
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
                    alert = uuid.uuid4()
                    self._event(
                        session,
                        job,
                        stage.name,
                        "WARN",
                        f"No progress from {stage.name} for {self.stall_after_s:.0f} s",
                        code="no_progress",
                        reason=reason,
                        action="Open the job's log to see what the stage was doing.",
                        event_type="alert",
                        uid=alert,
                        extra={
                            "state": "fired",
                            "value": round(self.stall_after_s),
                            "threshold": round(self.stall_after_s),
                        },
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
                        caused_by=alert,
                    )
                    acted.append(job.id)
                elif job.state == "stalled" and not silent:
                    alert = _latest_alert(session, "no_progress", job.id)
                    transition(session, job, "running", reason="progress resumed", actor="watchdog")
                    if alert is not None:
                        self._event(
                            session,
                            job,
                            stage.name,
                            "INFO",
                            f"Progress from {stage.name} again",
                            code="no_progress",
                            event_type="alert",
                            caused_by=alert,
                            extra={"state": "cleared"},
                        )
                    self._event(
                        session,
                        job,
                        stage.name,
                        "INFO",
                        "Progress resumed: job running again",
                        caused_by=alert,
                    )
                    acted.append(job.id)
            self._clear_stall_alerts(session)
        return acted

    def _clear_stall_alerts(self, session: Session) -> None:
        """Clear the ``no_progress`` alerts of jobs that left ``stalled`` some other way:
        the supervisor ends a stalled job's stage, or the job is cancelled."""
        open_alerts = session.execute(
            select(EventRow.event_uid, EventRow.job_id, EventRow.stage)
            .where(
                EventRow.level == "WARN",  # the partial index on warnings and errors
                EventRow.type == "alert",
                EventRow.metrics["code"].astext == "no_progress",
                EventRow.metrics["state"].astext == "fired",
                EventRow.ts >= func.now() - timedelta(days=1),
            )
            .order_by(EventRow.id)
        ).all()
        for uid, job_id, stage in open_alerts:
            done = session.scalar(
                select(EventRow.id)
                .where(EventRow.caused_by == uid, EventRow.metrics["state"].astext == "cleared")
                .limit(1)
            )
            job = session.get(Job, job_id) if job_id else None
            if done is not None or job is None or job.state == "stalled":
                continue
            self._event(
                session,
                job,
                stage,
                "INFO",
                f"No longer stalled: the job is {job.state}",
                code="no_progress",
                event_type="alert",
                caused_by=uid,
                extra={"state": "cleared"},
            )

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
        if temperature < self.thermal_resume_c:
            return self._resume_after_heat(temperature)
        return []

    def pause_for_heat(self, temperature: float, caused_by: uuid.UUID | None = None) -> list[str]:
        """Pause every running GPU job for heat (RUN-1.6). Called by the ``gpu_hot``
        alert, whose ``event_uid`` each pause records. Returns the paused jobs."""
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
                    caused_by=caused_by,
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
            alert = _latest_alert(session, "gpu_hot") if paused else None
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
                    caused_by=alert,
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
        caused_by: uuid.UUID | None = None,
        event_type: Literal["action", "alert"] = "action",
        uid: uuid.UUID | None = None,
        extra: dict[str, MetricValue] | None = None,
    ) -> None:
        metrics: dict[str, MetricValue] = {}
        if code is not None:
            metrics["code"] = code
        if temperature is not None:
            metrics["gpu_temp_c"] = round(temperature, 1)
        metrics.update(extra or {})
        insert_events(
            session,
            [
                Event(
                    event_uid=uid or uuid.uuid4(),
                    job_id=job.id,
                    stage=stage,
                    attempt=job.attempt,
                    type=event_type,
                    level=level,
                    message=message,
                    reason=reason,
                    suggested_action=action,
                    metrics=metrics or None,
                    caused_by=caused_by,
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


def _latest_alert(session: Session, code: str, job_id: str | None = None) -> uuid.UUID | None:
    """The ``event_uid`` of the latest firing of ``code`` (for that job, if given)."""
    query = select(EventRow.event_uid).where(
        EventRow.type == "alert",
        EventRow.metrics["code"].astext == code,
        EventRow.metrics["state"].astext == "fired",
    )
    if job_id is not None:
        query = query.where(EventRow.job_id == job_id)
    return session.scalar(query.order_by(EventRow.id.desc()).limit(1))
