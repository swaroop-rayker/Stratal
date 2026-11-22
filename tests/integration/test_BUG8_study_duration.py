"""BUG8 (M10's open point 3): a study job's stage duration on SCR-04 covered only its last
step, because each step's claim reset the stage's start. Against the real PostgreSQL."""

import time

import pytest
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.db.engine import get_engine
from stratal.db.models import Job, JobStage
from stratal.runtime import jobs as job_service
from stratal.runtime.supervisor import claim

pytestmark = pytest.mark.integration


def _stage(job_id: str) -> JobStage:
    with Session(get_engine()) as session:
        stage = session.get(JobStage, (job_id, 0))
        assert stage is not None
        session.expunge(stage)
        return stage


def _between_steps(job_id: str) -> None:
    """What the supervisor and the trial do between two steps: the stage waits with the job
    running, then the trial queues its next step (ADR-0018 rule 5)."""
    with Session(get_engine()) as session, session.begin():
        stage = session.get(JobStage, (job_id, 0))
        assert stage is not None
        stage.status = "waiting"
        stage.finished_at = None
    with Session(get_engine()) as session, session.begin():
        stage = session.get(JobStage, (job_id, 0))
        assert stage is not None
        stage.status = "queued"


def test_BUG8_a_study_stage_keeps_its_start_across_steps(bus: Bus) -> None:
    with Session(get_engine()) as session, session.begin():
        job, _first = job_service.create_job_from_plan(
            session, kind="hpo_study", spec={"dataset": "test"}, plan=[("study", {})]
        )
        job_id = job.id

    assert claim(job_id, 0, "w1") is not None
    started = _stage(job_id).started_at
    assert started is not None
    for _step in range(2):
        _between_steps(job_id)
        time.sleep(0.05)
        assert claim(job_id, 0, "w1") is not None
        assert _stage(job_id).started_at == started, "every step keeps the first start"
    with Session(get_engine()) as session:
        running = session.get(Job, job_id)
        assert running is not None and running.state == "running" and running.attempt == 1


def test_BUG8_a_retried_job_starts_its_stage_again(bus: Bus) -> None:
    with Session(get_engine()) as session, session.begin():
        job, _first = job_service.create_job_from_plan(
            session, kind="pipeline", spec={"dataset": "test"}, plan=[("synthetic", {})]
        )
        job_id = job.id
    assert claim(job_id, 0, "w1") is not None
    first = _stage(job_id).started_at
    with Session(get_engine()) as session, session.begin():  # the job queued again (a retry)
        again = session.get(Job, job_id)
        stage = session.get(JobStage, (job_id, 0))
        assert again is not None and stage is not None
        again.state, stage.status = "queued", "queued"
    time.sleep(0.05)
    assert claim(job_id, 0, "w1") is not None
    assert _stage(job_id).started_at != first, "a new attempt is timed from its own start"
