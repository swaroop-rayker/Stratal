"""The job runtime against the real PostgreSQL and Redis, with real child processes.

Covers MON-1.1, MON-1.2, MON-3.1 (ingestion, staging), the phase-1 slice of
RUN-1.1 and RUN-1.2, NFR-10, BUG2 and BUG4. Stages are supervised in the test's
own thread instead of by a Celery worker; everything below the task wrapper is
the production code.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.api.app import create_app
from stratal.api.jobs import get_enqueue
from stratal.core.errors import InvalidStateTransition
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.mon.watchdog import Watchdog
from stratal.runtime import jobs as job_service
from stratal.runtime.states import transition
from stratal.testing.harness import Runner, wait_until
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration

FAST = {"items": 10, "seconds_per_item": 0.01}
SLOW = {"items": 400, "seconds_per_item": 0.02}


def _transitions(job_id: str) -> list[tuple[str | None, str, str]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state, actor FROM job_transitions "
                "WHERE job_id = :job_id ORDER BY id"
            ),
            {"job_id": job_id},
        )
        return [(row[0], row[1], row[2]) for row in rows]


def _timeline_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild a job's stage timeline using nothing but its events (MON-1.1.AC1)."""
    stages: dict[str, dict[str, Any]] = {}
    for event in events:
        name = event["stage"]
        if name is None:
            continue
        entry = stages.setdefault(name, {"name": name, "status": "running", "last_step": None})
        if event["type"] == "stage_started":
            entry["started_at"] = event["ts"]
        elif event["type"] == "stage_progress":
            entry["last_step"] = event["step"]
        elif event["type"] in ("stage_completed", "stage_failed"):
            entry["status"] = event["type"].removeprefix("stage_")
            entry["finished_at"] = event["ts"]
    return list(stages.values())


def test_MON_1_1_full_timeline_is_rebuilt_from_the_event_log_alone(runner: Runner) -> None:
    job_id = runner.create([("synthetic", FAST), ("synthetic", {**FAST, "fail_at": 4})])

    assert runner.run_queued() == ["completed", "failed"]
    runner.collect()
    events = runner.events(job_id)

    for event in events:
        assert event["job_id"] == job_id and event["ts"] is not None and event["attempt"] in (0, 1)
    states = [e["metrics"]["to"] for e in events if e["type"] == "job_state"]
    assert states == ["queued", "running", "failed"]
    kinds = [e["type"] for e in events if e["stage"] == "synthetic"]
    assert kinds.count("stage_started") == 2
    assert kinds.count("stage_completed") == 1 and kinds.count("stage_failed") == 1
    progress = [e["step"] for e in events if e["type"] == "stage_progress"]
    assert progress == [*range(1, 11), 1, 2, 3]
    timeline = _timeline_from_events(events)
    assert timeline[0]["status"] == "failed" and timeline[0]["finished_at"] is not None
    failed = next(e for e in events if e["type"] == "stage_failed")
    assert failed["level"] == "ERROR" and "planted failure" in failed["reason"]
    assert "job.log" in failed["suggested_action"]


def test_MON_1_2_activity_messages_with_key_parameters_appear_live(runner: Runner) -> None:
    job_id = runner.create([("synthetic", {**SLOW, "label": "Florence-2-base"})])
    thread = runner.run_in_thread()

    def live_activity() -> str | None:
        runner.collector.run_once(block_ms=100)
        stage = runner.job(job_id)["stages"][0]
        return stage["activity"] if stage["status"] == "running" and stage["step"] else None

    wait_until(live_activity, what="an activity message while the stage is still running")
    stage = runner.job(job_id)["stages"][0]

    assert stage["status"] == "running", "the message must be visible before the stage ends"
    assert "Processing item" in stage["activity"]
    assert "Florence-2-base" in stage["activity"] and "delay=0.02s" in stage["activity"]
    assert stage["total"] == 400 and 0 < stage["step"] < 400

    with Session(get_engine()) as session, session.begin():
        job_service.request_cancel(session, job_id)
    runner.bus.set_control(job_id, "cancel")
    thread.join(15)
    assert runner.job(job_id)["state"] == "cancelled"


def test_MON_3_1_ingestion_and_staging_emit_their_messages_and_metrics(
    runner: Runner, tmp_path: Path
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=12, corrupt=2)
    queued: list[job_service.Enqueue] = []
    app = create_app()
    app.dependency_overrides[get_enqueue] = lambda: queued.append
    client = TestClient(app)

    response = client.post(
        "/api/v1/jobs",
        json={
            "kind": "pipeline",
            "dataset": "mon31",
            "source": {"kind": "folder", "path": str(folder.root)},
            "stages": {"ingest": {"min_size": 32}},
            "until_stage": "ingest",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    runner.queue.extend(queued)
    assert runner.run_queued() == ["completed"]
    runner.collect()

    events = runner.events(job_id)
    messages = [e["message"] for e in events]
    total = folder.total_files
    # Ingestion row of the MON-3 table.
    assert f"Scanning folder… {total:,} files found" in messages
    assert f"Reading metadata {total:,}/{total:,}" in messages
    reading = next(e for e in events if e["message"].startswith("Reading metadata"))
    assert {"files_per_s", "accepted", "rejected"} <= reading["metrics"].keys()
    done = next(e for e in events if e["type"] == "stage_completed")
    assert done["metrics"]["accepted"] + done["metrics"]["rejected"] == total
    assert done["metrics"]["rejected_corrupt_image"] == 2
    # Staging row: the commit step of the stage (OC-1).
    staging = [e for e in events if e["substage"] == "staging"]
    assert [e["message"].split(" → ")[0] for e in staging] == [
        "Writing manifest",
        "Computing version hash",
        "Recording lineage",
    ]
    version_id = staging[-1]["metrics"]["version_id"]
    assert staging[-1]["metrics"]["rows"] == total and staging[-1]["metrics"]["bytes_written"] > 0
    assert staging[-1]["message"] == f"Recording lineage → {version_id}"

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["state"] == "completed" and job["output_version_id"] == version_id
    assert job["stages"][0]["output_version_id"] == version_id
    assert done["version_id"] == version_id, "events after the commit carry the version"
    with get_engine().connect() as connection:
        created_by = connection.execute(
            text("SELECT created_by_job_id FROM dataset_versions WHERE version_id = :v"),
            {"v": version_id},
        ).scalar_one()
    assert created_by == job_id
    page = client.get(f"/api/v1/jobs/{job_id}/events", params={"limit": 3}).json()
    assert len(page["items"]) == 3 and page["next_cursor"] == str(page["items"][-1]["id"])
    assert client.get("/api/v1/host/keep-awake").json() == {"keep_awake": False, "active_jobs": []}


def test_API_jobs_rejects_bad_requests_with_problem_details(runner: Runner, tmp_path: Path) -> None:
    client = TestClient(create_app())
    base = {"kind": "pipeline", "dataset": "bad", "source": {"path": str(tmp_path)}}

    missing = client.post("/api/v1/jobs", json={**base, "source": {"path": "/nowhere"}})
    config = client.post("/api/v1/jobs", json={**base, "stages": {"ingest": {"min_size": 0}}})
    unknown = client.post("/api/v1/jobs", json={**base, "stages": {"train": {}}})

    assert (missing.status_code, missing.json()["code"]) == (422, "source_not_found")
    assert (config.status_code, config.json()["code"]) == (422, "validation_error")
    assert config.json()["errors"][0]["loc"] == ["stages", "ingest", "min_size"]
    assert unknown.status_code == 422 and "train" in unknown.json()["detail"]
    assert client.get("/api/v1/jobs/job_9999").json()["code"] == "not_found"
    assert client.get("/api/v1/jobs").json() == {"items": [], "next_cursor": None}


def test_RUN_1_1_transitions_are_logged_and_invalid_ones_rejected(runner: Runner) -> None:
    job_id = runner.create([("synthetic", FAST)])
    assert runner.run_queued() == ["completed"]

    assert _transitions(job_id) == [
        (None, "queued", "api"),
        ("queued", "running", "supervisor"),
        ("running", "completed", "supervisor"),
    ]
    client = TestClient(create_app())
    for action in ("cancel", "retry"):
        response = client.post(f"/api/v1/jobs/{job_id}/{action}")
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "invalid_state_transition"
    with Session(get_engine()) as session, session.begin():
        job = job_service.get_job(session, job_id)
        with pytest.raises(InvalidStateTransition):
            transition(session, job, "running", reason="not allowed", actor="api")
    assert len(_transitions(job_id)) == 3, "a rejected transition leaves no record"
    with pytest.raises(Exception, match="ck_jobs_state"), get_engine().begin() as connection:
        connection.execute(text("UPDATE jobs SET state = 'bogus' WHERE id = :j"), {"j": job_id})


def test_RUN_1_1_a_task_delivered_twice_runs_the_stage_once(runner: Runner) -> None:
    job_id = runner.create([("synthetic", FAST)])
    runner.queue.append(runner.queue[0])  # the same message, delivered again

    assert runner.run_queued() == ["completed", "not_claimed"]
    runner.collect()
    assert [e["type"] for e in runner.events(job_id)].count("stage_started") == 1


def test_RUN_1_2_cancel_a_queued_job_and_retry_a_failed_one(runner: Runner) -> None:
    queued: list[job_service.Enqueue] = []
    app = create_app()
    app.dependency_overrides[get_enqueue] = lambda: queued.append
    client = TestClient(app)

    waiting = runner.create([("synthetic", FAST)])
    cancelled = client.post(f"/api/v1/jobs/{waiting}/cancel")
    assert cancelled.status_code == 202 and cancelled.json()["state"] == "cancelled"
    assert runner.run_queued() == ["not_claimed"], "a cancelled job is never started"

    failing = runner.create([("synthetic", FAST), ("synthetic", {**FAST, "fail_at": 2})])
    assert runner.run_queued() == ["completed", "failed"]
    job = runner.job(failing)
    assert job["state"] == "failed" and job["error"]["code"] == "stage_failed"
    with get_engine().begin() as connection:  # "fix the cause", then retry
        connection.execute(
            text("UPDATE job_stages SET config = :c WHERE job_id = :j AND idx = 1"),
            {"c": json.dumps(FAST), "j": failing},
        )
    retried = client.post(f"/api/v1/jobs/{failing}/retry")
    assert retried.status_code == 202 and retried.json()["state"] == "queued"
    assert [(item.job_id, item.idx) for item in queued] == [(failing, 1)], "stage 0 is reused"
    runner.queue.extend(queued)
    assert runner.run_queued() == ["completed"]
    job = runner.job(failing)
    assert (job["state"], job["attempt"], job["error"]) == ("completed", 2, None)


@pytest.mark.parametrize("ignore_cancel", [False, True], ids=["cooperative", "hard"])
def test_RUN_1_2_cancel_a_running_job(runner: Runner, ignore_cancel: bool) -> None:
    client = TestClient(create_app())
    job_id = runner.create([("synthetic", {**SLOW, "ignore_cancel": ignore_cancel})])
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(job_id)["state"] == "running", what="the job to start")
    time.sleep(0.5)

    response = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert response.status_code == 202
    assert response.json()["desired_state"] == "cancelled", "shown as cancelling until it stops"
    thread.join(15)

    job = runner.job(job_id)
    assert (job["state"], job["desired_state"]) == ("cancelled", None)
    assert job["stages"][0]["status"] == "cancelled"
    assert _transitions(job_id)[-1] == ("running", "cancelled", "supervisor")
    assert not runner.bus.get_active(), "the active-job registry is cleared"


def test_NFR_10_job_logs_carry_job_id_and_version_id_on_every_line(
    runner: Runner, tmp_path: Path
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=5, corrupt=1)
    spec = {"dataset": "nfr10", "source": {"kind": "folder", "path": str(folder.root)}}
    job_id = runner.create([("ingest", {"min_size": 32})], spec)
    assert runner.run_queued() == ["completed"]

    log_path = get_settings().data_dir / "jobs" / job_id / "job.log"
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    version_id = runner.job(job_id)["output_version_id"]

    assert len(lines) >= 5
    for line in lines:
        assert {"job_id", "version_id", "ts", "level", "event"} <= line.keys()
        assert line["job_id"] == job_id
    assert lines[0]["version_id"] is None, "not known until the stage commits"
    assert lines[-1]["version_id"] == version_id
    assert [line for line in lines if line["version_id"] == version_id], "filterable by version"
    spool = get_settings().data_dir / "jobs" / job_id / "events.jsonl"
    assert len(spool.read_text(encoding="utf-8").splitlines()) >= 5, "local event spool (ADR-0005)"


@pytest.mark.parametrize(
    ("sig", "name"), [(signal.SIGKILL, "SIGKILL"), (signal.SIGSEGV, "SIGSEGV")]
)
def test_BUG2_child_killed_by_a_signal_fails_the_job_and_the_worker_survives(
    runner: Runner, sig: signal.Signals, name: str
) -> None:
    job_id = runner.create([("synthetic", {**FAST, "kill_signal_at": 3, "kill_signal": int(sig)})])

    assert runner.run_queued() == ["failed"], "the supervisor returns normally"
    runner.collect()
    job = runner.job(job_id)

    assert job["state"] == "failed"
    assert job["error"]["code"] == "stage_failed" and job["error"]["stage"] == "synthetic"
    assert name in job["error"]["reason"] and job["error"]["suggested_action"]
    assert job["stages"][0]["status"] == "failed"
    errors = [e for e in runner.events(job_id) if e["level"] == "ERROR"]
    assert [e["type"] for e in errors] == ["stage_failed", "job_state"]
    assert all(e["reason"] and e["suggested_action"] for e in errors)
    # The same "worker" (this process) runs the next job without trouble.
    following = runner.create([("synthetic", FAST)])
    assert runner.run_queued() == ["completed"]
    assert runner.job(following)["state"] == "completed"


def test_BUG2_a_kernel_memory_kill_is_named_as_such() -> None:
    from stratal.runtime.supervisor import describe_exit

    error = describe_exit(-9, oom_kills=1, peak_rss_mb=4321.0, limit_mb=4500.0)

    assert error["code"] == "host_oom_killed"
    assert "4500 MB" in error["reason"] and "4321 MB" in error["reason"]
    assert "smaller batch" in error["suggested_action"]
    assert describe_exit(-9, oom_kills=0, peak_rss_mb=None, limit_mb=None)["code"] == "stage_failed"


def _signal_tree(pid: int, sig: signal.Signals) -> None:
    for process in [*psutil.Process(pid).children(recursive=True), psutil.Process(pid)]:
        process.send_signal(sig)


def test_BUG4_a_frozen_worker_is_recovered_and_the_job_completes(runner: Runner) -> None:
    job_id = runner.create([("synthetic", {"items": 150, "seconds_per_item": 0.02})])
    item = runner.queue.pop()
    requeued: list[job_service.Enqueue] = []
    watchdog = Watchdog(get_engine(), enqueue=requeued.append, stale_after_s=1.0, max_attempts=3)
    worker = subprocess.Popen(
        [sys.executable, "-m", "stratal.testing.run_supervisor", item.job_id, str(item.idx)],
        stdout=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )
    try:
        wait_until(lambda: runner.job(job_id)["state"] == "running", what="the job to start")
        wait_until(lambda: psutil.Process(worker.pid).children(), what="the child process")
        assert watchdog.run_once() == [], "a live job is left alone"

        _signal_tree(worker.pid, signal.SIGSTOP)  # freeze: what 'docker pause' does
        time.sleep(1.6)
        assert watchdog.run_once() == [job_id]
        job = runner.job(job_id)
        assert job["state"] == "queued" and job["stages"][0]["status"] == "queued"
        assert [(r.job_id, r.idx) for r in requeued] == [(job_id, 0)]

        _signal_tree(worker.pid, signal.SIGCONT)  # unfreeze
        stdout, _ = worker.communicate(timeout=20)
        assert stdout.strip() == "superseded", "the old supervisor stands down"
    finally:
        if worker.poll() is None:
            _signal_tree(worker.pid, signal.SIGKILL)

    runner.queue.extend(requeued)
    assert runner.run_queued() == ["completed"], "the job resumes in a fresh process"
    job = runner.job(job_id)
    assert (job["state"], job["attempt"]) == ("completed", 2)
    assert ("running", "queued", "watchdog") in _transitions(job_id)
    warning = next(e for e in runner.events(job_id) if e["level"] == "WARN")
    assert warning["metrics"]["code"] == "worker_lost" and warning["suggested_action"]


def test_BUG4_a_detected_suspend_is_not_mistaken_for_a_dead_worker(runner: Runner) -> None:
    clock = {"wall": 1000.0, "mono": 50.0}
    watchdog = Watchdog(
        get_engine(),
        enqueue=lambda _item: None,
        stale_after_s=30.0,
        wall=lambda: clock["wall"],
        mono=lambda: clock["mono"],
    )
    job_id = runner.create([("synthetic", FAST)])
    with get_engine().begin() as connection:  # running, with a heartbeat from before the sleep
        connection.execute(
            text(
                "UPDATE jobs SET state = 'running', attempt = 1, "
                "heartbeat_at = now() - interval '705 seconds' WHERE id = :j"
            ),
            {"j": job_id},
        )
        connection.execute(text("UPDATE job_stages SET status = 'running'"))

    clock["wall"] += 706.0  # the laptop slept 705 s (spike S-03): wall jumps, monotonic does not
    clock["mono"] += 1.0
    assert watchdog.run_once() == [], "grace after a suspend"
    assert runner.job(job_id)["state"] == "running"
    suspended = [e for e in runner.events() if e["type"] == "system"]
    assert len(suspended) == 1 and suspended[0]["metrics"]["suspended_s"] == 705.0

    clock["wall"] += 31.0
    clock["mono"] += 31.0
    assert watchdog.run_once() == [job_id], "still silent after the grace period: now it is lost"


def test_BUG4_host_power_events_are_put_on_the_timeline(runner: Runner) -> None:
    client = TestClient(create_app())

    for kind in ("suspend", "resume"):
        body = {"type": kind, "at": "2026-11-14T10:00:00Z"}
        assert client.post("/api/v1/host/power-events", json=body).status_code == 202

    events = client.get("/api/v1/events", params={"level": "WARN"}).json()["items"]
    assert [e["message"] for e in events] == ["Windows is going to sleep"]
    assert len(client.get("/api/v1/events").json()["items"]) == 2
