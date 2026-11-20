"""How a deep-learning study and its trial jobs move each other (HPO-1.2, ADR-0018 rule 5).

Plain Python, no Optuna: the API, the supervisors and the watchdog all use it.

A deep study's ``study`` stage runs in short steps. A step ends by creating the next
trial's job and leaving the stage ``waiting``; the job itself stays ``running``, with no
process behind it. When the trial job ends, its supervisor queues the next step
(:func:`trial_ended`), keyed by the study and the trial's number, so a late or
duplicated end changes nothing. Pause and cancel on the study act on its current
trial (:func:`forward_pause`, :func:`study_job_ended`).
"""

import contextlib
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.db.models import Job, JobStage, Study, StudyTrial
from stratal.mon.events import utc_now
from stratal.runtime.states import TERMINAL, transition

TRIAL_FOLDER = "trial"
"""A trial's weights, under its job's folder: ``jobs/<trial job>/trial/{initial,trained}``."""

LIVE = ("queued", "running", "stalled", "paused")


def trial_folder(data_dir: Path, trial_job_id: str) -> Path:
    return data_dir / "jobs" / trial_job_id / TRIAL_FOLDER


def study_of_job(session: Session, study_job_id: str) -> Study | None:
    return session.scalar(select(Study).where(Study.job_id == study_job_id))


def current_trial(session: Session, study_id: str) -> tuple[StudyTrial, Job] | None:
    """The trial a waiting study waits for: its newest trial with a job. Its row may say
    ``complete`` or ``pruned`` already (the trial writes it), while its job still ends."""
    row = session.scalar(
        select(StudyTrial)
        .where(StudyTrial.study_id == study_id, StudyTrial.job_id.is_not(None))
        .order_by(StudyTrial.number.desc())
        .limit(1)
    )
    if row is None or row.job_id is None:
        return None
    job = session.get(Job, row.job_id)
    return (row, job) if job is not None else None


def study_stage(session: Session, study_job_id: str) -> JobStage | None:
    return session.get(JobStage, (study_job_id, 0))


def trial_to_enqueue(session: Session, study_job_id: str) -> Any:
    """After a step: the current trial's queued stage, to send once the step's exit commits."""
    from stratal.runtime.jobs import Enqueue

    study = study_of_job(session, study_job_id)
    found = current_trial(session, study.id) if study is not None else None
    if found is None:
        return None
    _, job = found
    stage = session.scalar(
        select(JobStage)
        .where(JobStage.job_id == job.id, JobStage.status == "queued")
        .order_by(JobStage.idx)
    )
    return Enqueue(job.id, stage.idx, stage.queue) if stage is not None else None


def _finish_row(row: StudyTrial, *, code: str) -> None:
    now = utc_now()
    row.state, row.code, row.finished_at = "failed", code, now
    row.duration_s = round((now - row.started_at).total_seconds(), 3)


def interrupt_queued_trial(session: Session, study_job_id: str, *, reason: str) -> bool:
    """A study paused while its trial waits for a worker: that trial never started, so it
    is cancelled and recorded as ``interrupted`` (uncounted); the resume asks for another."""
    study = study_of_job(session, study_job_id)
    found = current_trial(session, study.id) if study is not None else None
    if found is None or found[1].state != "queued":
        return False
    row, job = found
    transition(session, job, "cancelled", reason=reason, actor="supervisor")
    _cancel_stages(session, job.id)
    _finish_row(row, code="interrupted")
    return True


def _cancel_stages(session: Session, job_id: str) -> None:
    from stratal.runtime.jobs import cancel_unfinished_stages

    cancel_unfinished_stages(session, job_id)


def trial_ended(session: Session, trial_job: Job) -> Any:
    """A trial job ended (completed, failed or cancelled): queue the study's next step, if
    the study is still waiting for exactly this trial. Returns the step to send, or None."""
    from stratal.runtime.jobs import Enqueue

    study_job = session.get(Job, str(trial_job.spec.get("study_job")), with_for_update=True)
    if study_job is None or study_job.state in TERMINAL:
        if study_job is not None and study_job.state == "cancelled":
            from stratal.core.settings import get_settings

            with contextlib.suppress(OSError):  # what it wrote after the cancel
                remove_tree(trial_folder(get_settings().data_dir, trial_job.id))
        return None
    stage = study_stage(session, study_job.id)
    study = study_of_job(session, study_job.id)
    if stage is None or study is None or stage.status != "waiting":
        return None
    found = current_trial(session, study.id)
    if found is None or found[0].number != trial_job.spec.get("trial"):
        return None
    if study_job.state == "paused":
        return None  # its resume runs the step
    if study_job.desired_state == "paused":
        study_job.desired_state = None
        transition(
            session,
            study_job,
            "paused",
            reason=f"paused after trial {found[0].number} ended",
            actor="supervisor",
        )
        return None
    stage.status = "queued"
    study_job.heartbeat_at = utc_now()  # nobody beats for a queued step: start the clock
    return Enqueue(study_job.id, stage.idx, stage.queue)


def trial_paused(session: Session, trial_job: Job) -> None:
    """A trial paused: if its study was asked to pause, the study is paused now."""
    study_job = session.get(Job, str(trial_job.spec.get("study_job")), with_for_update=True)
    if study_job is None or study_job.state != "running" or study_job.desired_state != "paused":
        return
    study_job.desired_state = None
    transition(
        session,
        study_job,
        "paused",
        reason=f"its trial {trial_job.id} paused at a safe point",
        actor="supervisor",
    )


def forward_pause(session: Session, study_job: Job) -> list[str]:
    """``POST /jobs/{study}/pause`` while the study waits for a trial: the trial pauses (a
    checkpoint first) and the study follows; a trial not yet started is withdrawn.
    Returns the jobs whose running process must be told."""
    stage = study_stage(session, study_job.id)
    study = study_of_job(session, study_job.id)
    if stage is None or study is None or stage.status != "waiting":
        return []
    found = current_trial(session, study.id)
    trial = found[1] if found is not None else None
    if trial is not None and trial.state in ("running", "stalled"):
        if trial.desired_state is None:
            trial.desired_state = "paused"
            trial.pause_reason = study_job.pause_reason or "user"
        return [trial.id]
    if trial is not None and trial.state == "queued":
        interrupt_queued_trial(session, study_job.id, reason="its study was paused before it began")
    study_job.desired_state = None
    transition(session, study_job, "paused", reason="paused by the user", actor="user")
    return []


def trial_to_signal(session: Session, study_job: Job) -> list[str]:
    """The current trial of a study, if a process runs it (for the Redis fast path)."""
    if study_job.kind != "hpo_study":
        return []
    study = study_of_job(session, study_job.id)
    found = current_trial(session, study.id) if study is not None else None
    if found is None or found[1].state not in ("running", "stalled"):
        return []
    return [found[1].id]


def discard_trial_weights(data_dir: Path, session: Session, study_id: str) -> int:
    """Delete every trial's weights of a study; returns the bytes freed."""
    freed = 0
    jobs = session.scalars(
        select(StudyTrial.job_id).where(
            StudyTrial.study_id == study_id, StudyTrial.job_id.is_not(None)
        )
    ).all()
    for job_id in jobs:
        freed += remove_tree(trial_folder(data_dir, str(job_id)))
    return freed


def remove_tree(folder: Path) -> int:
    if not folder.exists():
        return 0
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    shutil.rmtree(folder, ignore_errors=True)
    return size


def study_job_ended(session: Session, job: Job, to_state: str) -> None:
    """A study job was cancelled or failed: its study row follows, its current trial is
    cancelled, and its trials' weights are deleted. Cancelling a study registers nothing
    and evaluates nothing (ADR-0018 rule 5)."""
    study = study_of_job(session, job.id)
    if study is None or study.state not in ("running", "closed"):
        return
    study.state = "cancelled" if to_state == "cancelled" else "failed"
    study.finished_at = utc_now()
    found = current_trial(session, study.id)
    if found is not None:
        row, trial = found
        if trial.state in ("running", "stalled"):
            trial.desired_state = "cancelled"  # its supervisor stops it at a safe point
        elif trial.state in ("queued", "paused"):
            transition(
                session, trial, "cancelled", reason=f"its study {job.id} ended", actor="supervisor"
            )
            _cancel_stages(session, trial.id)
        if row.state == "running":
            _finish_row(row, code="cancelled")
    if to_state == "cancelled":
        from stratal.core.settings import get_settings

        with contextlib.suppress(OSError):
            discard_trial_weights(get_settings().data_dir, session, study.id)
