"""RUN-1.6 and RUN-1.7: the watchdog's heat and stall actions (ADR-0014 rule 7).

Real PostgreSQL and Redis, real supervisor and child processes running the
synthetic stage. The temperature is simulated (RUN-1.6.AC1) and the stall
timeout shortened to a second (RUN-1.7.AC1).
"""

import time
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine
from stratal.mon.watchdog import Watchdog
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue
from stratal.testing.harness import Runner, wait_until

pytestmark = pytest.mark.integration

SLOW = {"items": 200, "seconds_per_item": 0.05}


def _transitions(job_id: str) -> list[tuple[str | None, str, str, str]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state, actor, reason FROM job_transitions "
                "WHERE job_id = :j ORDER BY id"
            ),
            {"j": job_id},
        )
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def _create(runner: Runner, config: dict[str, Any], *, queue: str) -> str:
    """A one-stage synthetic job on ``queue`` (the GPU queue for the heat tests)."""
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_job_from_plan(
            session,
            kind="pipeline",
            spec={"dataset": "test"},
            plan=[("synthetic", config)],
            queues={"synthetic": queue},
        )
        job_id = job.id
    runner.queue.append(first)
    return job_id


def test_RUN_1_7_a_silent_stage_becomes_stalled_and_running_again_when_it_speaks(
    runner: Runner,
) -> None:
    # Three items, 2.5 s apart: the stage is alive (heartbeats) but silent between them.
    job_id = _create(runner, {"items": 3, "seconds_per_item": 2.5}, queue="cpu")
    watchdog = Watchdog(
        get_engine(), enqueue=lambda _item: None, stall_after_s=1.0, temperature=lambda: None
    )
    thread = runner.run_in_thread()

    def stalled_then_running() -> bool:
        runner.collect()
        watchdog.run_once()
        states = [to for _f, to, _a, _r in _transitions(job_id)]
        return "stalled" in states and states[states.index("stalled") + 1 :].count("running") > 0

    wait_until(stalled_then_running, timeout_s=20, what="stalled, then running")
    thread.join(20)

    assert runner.job(job_id)["state"] == "completed"
    moves = _transitions(job_id)
    stall = next(m for m in moves if m[1] == "stalled")
    assert stall[2] == "watchdog" and "no progress" in stall[3].lower()
    events = runner.events(job_id)
    assert any(e["metrics"] and e["metrics"].get("code") == "stalled" for e in events)


def test_RUN_1_6_heat_pauses_the_gpu_job_and_cooling_resumes_it(runner: Runner) -> None:
    gpu_job = _create(runner, SLOW, queue="gpu")
    temperatures = [60.0]
    requeued: list[Enqueue] = []
    watchdog = Watchdog(
        get_engine(),
        enqueue=requeued.append,
        bus=runner.bus,
        temperature=lambda: temperatures[-1],
    )
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(gpu_job)["state"] == "running", what="the job to start")

    temperatures.append(84.0)
    assert watchdog.run_once() == [], "the watchdog only resumes; the gpu_hot alert pauses"
    assert watchdog.pause_for_heat(84.0) == [gpu_job]  # what the alert calls (ADR-0020)
    thread.join(15)
    job = runner.job(gpu_job)
    assert (job["state"], job["stages"][0]["status"]) == ("paused", "paused")
    assert "too hot" in _transitions(gpu_job)[-1][3]

    temperatures.append(78.0)  # between the limits: stays paused (hysteresis)
    assert watchdog.run_once() == []
    temperatures.append(72.0)
    assert watchdog.run_once() == [gpu_job]
    assert [(q.job_id, q.idx, q.queue) for q in requeued] == [(gpu_job, 0, "gpu")]
    assert runner.bus.get_control(gpu_job) is None
    runner.queue.extend(requeued)
    assert runner.run_queued() == ["completed"]
    moves = _transitions(gpu_job)
    assert moves[-3][:3] == ("paused", "queued", "watchdog")
    codes = {e["metrics"].get("code") for e in runner.events(gpu_job) if e["metrics"]}
    assert {"thermal_pause", "thermal_resume"} <= codes


def test_RUN_1_6_heat_leaves_cpu_jobs_and_user_pauses_alone(runner: Runner) -> None:
    cpu_job = _create(runner, {**SLOW, "items": 40}, queue="cpu")
    temperatures = [90.0]
    watchdog = Watchdog(
        get_engine(),
        enqueue=lambda _item: None,
        bus=runner.bus,
        temperature=lambda: temperatures[-1],
    )
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(cpu_job)["state"] == "running", what="the job to start")

    assert watchdog.pause_for_heat(90.0) == [], "a CPU job is not paused for the GPU's heat"
    with Session(get_engine()) as session, session.begin():
        job_service.request_pause(session, cpu_job)  # the user pauses it
    runner.bus.set_control(cpu_job, "pause")
    thread.join(15)
    assert runner.job(cpu_job)["state"] == "paused"

    temperatures.append(60.0)
    time.sleep(0.1)
    assert watchdog.run_once() == [], "cooling resumes only thermal pauses"
    assert runner.job(cpu_job)["state"] == "paused"
