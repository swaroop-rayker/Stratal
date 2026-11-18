"""RUN-1.2: pause and resume any job (ADR-0014 rule 6, OC-8), against the real services.

A pipeline stage pauses at its next safe point and runs again on resume (its
caches hold the work done); a job between stages pauses before the next one
starts. Training's checkpoint on pause is tested in ``test_M8_training.py``.
"""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from stratal.api.app import create_app
from stratal.api.jobs import get_enqueue
from stratal.db.engine import get_engine
from stratal.runtime import jobs as job_service
from stratal.testing.harness import Runner, wait_until

pytestmark = pytest.mark.integration

SLOW = {"items": 200, "seconds_per_item": 0.05}
FAST = {"items": 3, "seconds_per_item": 0.01}


def _client() -> tuple[TestClient, list[job_service.Enqueue]]:
    queued: list[job_service.Enqueue] = []
    app = create_app()
    app.dependency_overrides[get_enqueue] = lambda: queued.append
    return TestClient(app), queued


def _transitions(job_id: str) -> list[tuple[str | None, str, str]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state, actor FROM job_transitions "
                "WHERE job_id = :j ORDER BY id"
            ),
            {"j": job_id},
        )
        return [(r[0], r[1], r[2]) for r in rows]


def test_RUN_1_2_a_running_stage_pauses_at_a_safe_point_and_resumes(runner: Runner) -> None:
    client, queued = _client()
    job_id = runner.create([("synthetic", SLOW)])
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(job_id)["state"] == "running", what="the job to start")
    time.sleep(0.5)

    response = client.post(f"/api/v1/jobs/{job_id}/pause")
    assert response.status_code == 202, response.text
    assert response.json()["desired_state"] == "paused", "shown as pausing until it stops"
    thread.join(15)

    job = runner.job(job_id)
    assert (job["state"], job["desired_state"]) == ("paused", None)
    assert job["stages"][0]["status"] == "paused"
    assert _transitions(job_id)[-1] == ("running", "paused", "supervisor")
    assert runner.bus.get_control(job_id) is None, "the flag is cleared, so a resume runs"
    shown = client.get(f"/api/v1/jobs/{job_id}")
    assert shown.status_code == 200, shown.text  # the response model knows the paused stage
    assert shown.json()["stages"][0]["status"] == "paused"

    resumed = client.post(f"/api/v1/jobs/{job_id}/resume")
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "queued"
    assert [(q.job_id, q.idx) for q in queued] == [(job_id, 0)]
    runner.queue.extend(queued)
    assert runner.run_queued() == ["completed"]
    job = runner.job(job_id)
    assert (job["state"], job["attempt"]) == ("completed", 2)
    assert ("paused", "queued", "user") in _transitions(job_id)


def test_RUN_1_2_a_job_paused_between_stages_waits_before_the_next(runner: Runner) -> None:
    client, queued = _client()
    # The first stage ignores stop requests, so the pause takes effect between stages.
    job_id = runner.create(
        [("synthetic", {**SLOW, "items": 30, "ignore_cancel": True}), ("synthetic2", FAST)]
    )
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(job_id)["state"] == "running", what="the job to start")

    assert client.post(f"/api/v1/jobs/{job_id}/pause").status_code == 202
    thread.join(15)

    job = runner.job(job_id)
    assert job["state"] == "paused"
    assert [s["status"] for s in job["stages"]] == ["completed", "paused"]
    assert client.post(f"/api/v1/jobs/{job_id}/resume").status_code == 202
    assert [(q.job_id, q.idx) for q in queued] == [(job_id, 1)], "stage 0 is not run again"
    runner.queue.extend(queued)
    assert runner.run_queued() == ["completed"]
    assert runner.job(job_id)["state"] == "completed"


def test_RUN_1_2_pause_and_resume_refuse_jobs_in_the_wrong_state(runner: Runner) -> None:
    client, _ = _client()
    waiting = runner.create([("synthetic", FAST)])

    pause_queued = client.post(f"/api/v1/jobs/{waiting}/pause")
    resume_queued = client.post(f"/api/v1/jobs/{waiting}/resume")
    runner.run_queued()
    pause_done = client.post(f"/api/v1/jobs/{waiting}/pause")

    for response in (pause_queued, resume_queued, pause_done):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "invalid_state_transition"


def test_RUN_1_2_a_paused_job_can_be_cancelled(runner: Runner) -> None:
    client, _ = _client()
    job_id = runner.create([("synthetic", SLOW)])
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(job_id)["state"] == "running", what="the job to start")
    client.post(f"/api/v1/jobs/{job_id}/pause")
    thread.join(15)

    response = client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert response.status_code == 202
    job = runner.job(job_id)
    assert job["state"] == "cancelled"
    assert job["stages"][0]["status"] == "cancelled"
    assert _transitions(job_id)[-1] == ("paused", "cancelled", "user")
