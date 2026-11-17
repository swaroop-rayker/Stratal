"""Events, telemetry and independence against the real PostgreSQL and Redis.

Covers MON-1.4, MON-2.1 (system metrics), MON-2.2, MON-2.3, MON-2.5 and NFR-13.
GPU metrics (MON-2.1's GPU fields, MON-2.4) need the GPU and are tested there.
"""

import http.client
import itertools
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.api.app import create_app
from stratal.core.bus import Bus
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Job, JobStage
from stratal.mon.collector import Collector, write_batch
from stratal.mon.emitter import StreamEmitter
from stratal.mon.events import Event
from stratal.mon.sampler import ActiveJobs, MachineProbe, Reading, Sampler, primary_job
from stratal.testing.harness import Runner, wait_until

pytestmark = pytest.mark.integration

PORT = 8765


def _emit(messages: list[str], job_id: str = "job_0001") -> None:
    emitter = StreamEmitter(redis_url=get_settings().redis_url, spool_path=None)
    for message in messages:
        emitter.emit(Event(job_id=job_id, stage="synthetic", message=message))
    emitter.close()


def _count(table: str) -> int:
    with get_engine().connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


# -- MON-1.4: durable, and live within a second --------------------------------


def test_MON_1_4_events_wait_in_the_stream_while_the_monitor_is_down(bus: Bus) -> None:
    _emit([f"event {n}" for n in range(5)])  # no collector is running

    assert _count("events") == 0 and bus.stream_length() == 5
    restarted = Collector(bus, get_engine())
    assert restarted.run_once(block_ms=100) == 5
    assert _count("events") == 5
    assert restarted.run_once(block_ms=100) == 0, "nothing is delivered twice"


def test_MON_1_4_a_crash_between_insert_and_acknowledge_stores_each_event_once(
    bus: Bus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _emit([f"event {n}" for n in range(5)])
    dying = Collector(bus, get_engine())
    with monkeypatch.context() as patch:

        def die(_ids: list[str]) -> None:
            raise RuntimeError("killed")

        patch.setattr(bus, "ack", die)
        with pytest.raises(RuntimeError):
            dying.run_once(block_ms=100)
    assert _count("events") == 5, "stored, but never acknowledged"

    restarted = Collector(bus, get_engine())
    assert restarted.run_once(block_ms=100) == 5, "the unacknowledged entries come back"
    assert _count("events") == 5, "and the unique event ID makes the second insert a no-op"
    assert restarted.run_once(block_ms=100) == 0


def test_MON_1_4_an_invalid_stream_entry_does_not_block_the_ones_behind_it(bus: Bus) -> None:
    bus.append("event", '{"level": "WARN", "message": "no reason given"}', maxlen=1000)
    bus.append("event", "not json", maxlen=1000)
    _emit(["a good event"])

    assert Collector(bus, get_engine()).run_once(block_ms=100) == 3
    assert _count("events") == 1


class _SseReader(threading.Thread):
    """Reads one SSE stream and queues ``(arrival time, event name, data)``."""

    def __init__(self, path: str, last_event_id: int | None = None) -> None:
        super().__init__(daemon=True)
        self.messages: queue.Queue[tuple[float, str, dict[str, Any]]] = queue.Queue()
        self._connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
        self._connection.request("GET", path, headers=headers)
        self._response = self._connection.getresponse()
        assert self._response.status == 200, self._response.read()
        assert self._response.getheader("content-type", "").startswith("text/event-stream")
        self.start()

    def run(self) -> None:
        name = ""
        try:
            for raw in self._response:
                line = raw.decode().rstrip("\n")
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    self.messages.put((time.monotonic(), name, json.loads(line[6:])))
        except (OSError, ValueError, AttributeError):
            return

    def next(self, name: str, timeout_s: float = 5.0) -> tuple[float, dict[str, Any]]:
        deadline = time.monotonic() + timeout_s
        while True:
            arrived, got, data = self.messages.get(timeout=max(deadline - time.monotonic(), 0.01))
            if got == name:
                return arrived, data

    def close(self) -> None:
        self._connection.close()


@pytest.fixture
def live_stack(bus: Bus) -> Iterator[Bus]:
    """The API served over real HTTP, plus a collector, as in the running stack."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_config=None)
    )
    serving = threading.Thread(target=server.run, daemon=True)
    serving.start()
    wait_until(lambda: server.started, what="the API to start")
    stop = threading.Event()
    collector = Collector(bus, get_engine())

    def collect() -> None:
        while not stop.is_set():
            collector.run_once(block_ms=100)

    collecting = threading.Thread(target=collect, daemon=True)
    collecting.start()
    yield bus
    stop.set()
    server.should_exit = True
    collecting.join(5)
    serving.join(5)


def test_MON_1_4_sse_delivers_within_one_second_and_replays_after_a_reconnect(
    live_stack: Bus, runner: Runner
) -> None:
    job_id = runner.create([("synthetic", {})])
    emitter = StreamEmitter(redis_url=get_settings().redis_url, spool_path=None)
    reader = _SseReader(f"/api/v1/jobs/{job_id}/stream")
    _, created = reader.next("event")
    assert created["type"] == "job_state", "history first: the job's creation"
    assert reader.next("job")[1]["id"] == job_id, "then a snapshot of the job"

    delays: list[float] = []
    last_id = 0
    for n in range(5):
        sent = time.monotonic()
        emitter.emit(Event(job_id=job_id, stage="synthetic", message=f"live {n}"))
        arrived, data = reader.next("event")
        assert data["message"] == f"live {n}"
        delays.append(arrived - sent)
        last_id = data["id"]
    assert max(delays) < 1.0, f"MON-1.4.AC1: within 1 s, got {delays}"
    reader.close()

    for n in range(3):  # emitted while no client is connected
        emitter.emit(Event(job_id=job_id, stage="synthetic", message=f"missed {n}"))
    emitter.emit(Event(job_id="job_other", stage="synthetic", message="another job's event"))
    wait_until(lambda: _count("events") == 10, what="the collector to store them")

    again = _SseReader(f"/api/v1/jobs/{job_id}/stream", last_event_id=last_id)
    replayed = [again.next("event")[1] for _ in range(3)]
    assert [e["message"] for e in replayed] == ["missed 0", "missed 1", "missed 2"]
    assert all(e["id"] > last_id for e in replayed), "nothing before the cursor is sent again"
    emitter.emit(Event(job_id=job_id, stage="synthetic", message="live again"))
    assert again.next("event")[1]["message"] == "live again", "then it switches to live"
    again.close()
    emitter.close()


# -- MON-2: telemetry ------------------------------------------------------------

_GPU_FIELDS = {
    "gpu_util_percent",
    "gpu_mem_util_percent",
    "vram_used_mb",
    "vram_total_mb",
    "gpu_temp_c",
    "gpu_power_w",
}


def _fake_reading() -> Reading:
    values: dict[str, Any] = {f.name: 1.0 for f in fields(Reading)}
    values["cpu_per_core"] = [1.0, 2.0]
    return Reading(**values)


def test_MON_2_1_every_system_metric_is_recorded(bus: Bus) -> None:
    probe = MachineProbe()
    time.sleep(0.3)
    reading = probe()

    for field in fields(Reading):
        if field.name not in _GPU_FIELDS:
            assert getattr(reading, field.name) is not None, field.name
    assert len(reading.cpu_per_core) == os.cpu_count()
    assert 0 < reading.ram_used_mb < reading.ram_total_mb
    assert reading.ram_available_mb > 0 and reading.disk_write_mb_s >= 0

    Sampler(get_engine(), probe=probe, active_jobs=dict, bus=bus).sample({}, 10.0)
    latest = TestClient(create_app()).get("/api/v1/telemetry/latest").json()
    assert {f.name for f in fields(Reading)} <= latest.keys()
    assert latest["ram_total_mb"] == pytest.approx(reading.ram_total_mb, rel=0.01)  # pyright: ignore[reportUnknownMemberType]
    assert latest["job_id"] is None and latest["interval_s"] == 10.0
    if not probe.gpu.available:  # no GPU in this container: unavailable, never zero
        assert all(latest[name] is None for name in _GPU_FIELDS)


def _sample_times() -> list[tuple[datetime, float, str | None]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text("SELECT ts, interval_s, job_id FROM resource_samples ORDER BY id")
        )
        return [(row[0], row[1], row[2]) for row in rows]


def test_MON_2_2_sample_timestamps_match_the_configured_interval(bus: Bus) -> None:
    active: ActiveJobs = {}
    sampler = Sampler(
        get_engine(),
        probe=_fake_reading,
        active_jobs=lambda: dict(active),
        active_interval_s=0.2,
        idle_interval_s=0.8,
    )
    stop = threading.Event()
    thread = threading.Thread(target=sampler.run, args=(stop,), daemon=True)
    thread.start()
    time.sleep(2.6)  # idle
    active["job_0001"] = {"stage": "label", "queue": "gpu", "since": "t"}
    time.sleep(2.1)  # a job is running
    stop.set()
    thread.join(5)

    rows = _sample_times()
    idle = [ts for ts, _, job in rows if job is None]
    busy = [ts for ts, _, job in rows if job == "job_0001"]
    assert all(interval == pytest.approx(0.8) for _, interval, job in rows if job is None)  # pyright: ignore[reportUnknownMemberType]
    assert all(interval == pytest.approx(0.2) for _, interval, job in rows if job)  # pyright: ignore[reportUnknownMemberType]
    assert 3 <= len(idle) <= 5 and 9 <= len(busy) <= 12

    def gaps(times: list[datetime]) -> list[float]:
        return [(b - a).total_seconds() for a, b in itertools.pairwise(times)]

    assert all(abs(gap - 0.8) < 0.1 for gap in gaps(idle)), gaps(idle)
    assert all(abs(gap - 0.2) < 0.1 for gap in gaps(busy)), gaps(busy)
    switch = (busy[0] - idle[-1]).total_seconds()
    assert switch < 0.8, "per-second sampling starts without waiting out the idle interval"


def test_MON_2_3_samples_are_tagged_with_job_and_stage(bus: Bus, runner: Runner) -> None:
    job_id = runner.create([("synthetic", {})])
    sampler = Sampler(get_engine(), probe=_fake_reading, active_jobs=bus.get_active, bus=bus)

    sampler.sample(bus.get_active(), 10.0)
    bus.set_active(job_id, stage="ingest", queue="cpu", since="2026-11-14T10:00:00+00:00")
    sampler.sample(bus.get_active(), 1.0)
    sampler.sample(bus.get_active(), 1.0)
    bus.set_active("job_gpu", stage="label", queue="gpu", since="2026-11-14T10:05:00+00:00")
    sampler.sample(bus.get_active(), 1.0)

    assert [job for _, _, job in _sample_times()] == [None, job_id, job_id, "job_gpu"]
    assert primary_job(bus.get_active()) == ("job_gpu", "label"), "the GPU job wins"
    client = TestClient(create_app())
    by_stage = client.get(f"/api/v1/jobs/{job_id}/telemetry", params={"stage": "ingest"}).json()
    assert len(by_stage["resource"]) == 2, "MON-2.3.AC1: charts can be filtered by stage"
    assert {s["stage"] for s in by_stage["resource"]} == {"ingest"}
    other = client.get(f"/api/v1/jobs/{job_id}/telemetry", params={"stage": "label"}).json()
    assert other == {"resource": [], "process": []}


def test_MON_2_5_job_summary_lists_peaks_for_every_stage(runner: Runner) -> None:
    sampler = Sampler(
        get_engine(),
        probe=MachineProbe(),
        active_jobs=runner.bus.get_active,
        active_interval_s=0.1,
        idle_interval_s=0.1,
    )
    stop = threading.Event()
    sampling = threading.Thread(target=sampler.run, args=(stop,), daemon=True)
    sampling.start()
    work = {"items": 40, "seconds_per_item": 0.02}
    job_id = runner.create([("synthetic", work), ("synthetic2", work)])

    assert runner.run_queued() == ["completed", "completed"]
    stop.set()
    sampling.join(5)
    runner.collect()
    job = runner.job(job_id)

    summary = job["summary"]
    assert [s["name"] for s in summary["stages"]] == ["synthetic", "synthetic2"]
    for stage in summary["stages"]:
        peaks = stage["peaks"]
        assert {"ram_used_mb", "vram_used_mb", "gpu_temp_c", "rss_mb"} <= peaks.keys()
        assert peaks["ram_used_mb"] > 0, "peak RAM, from the sampler"
        assert peaks["rss_mb"] > 0, "the job's own memory, from its reporter thread"
        assert stage["duration_s"] > 0.5 and stage["status"] == "completed"
    assert summary["duration_s"] >= sum(s["duration_s"] for s in summary["stages"]) * 0.9
    assert job["stages"][1]["peaks"] == summary["stages"][1]["peaks"]
    assert _count("process_samples") >= 4


# -- NFR-13: the monitor and the jobs do not depend on each other -----------------


@pytest.fixture
def monitor(bus: Bus) -> Iterator[subprocess.Popen[bytes]]:
    """The real monitor service, as its own process."""
    process = subprocess.Popen([sys.executable, "-m", "stratal.mon"], env=dict(os.environ))
    yield process
    if process.poll() is None:
        process.kill()
    process.wait(5)


def _progress_steps(runner: Runner, job_id: str) -> list[int]:
    return [e["step"] for e in runner.events(job_id) if e["type"] == "stage_progress"]


def test_NFR_13_kill_the_monitor_mid_job_and_the_job_continues(
    runner: Runner, monitor: subprocess.Popen[bytes]
) -> None:
    job_id = runner.create([("synthetic", {"items": 150, "seconds_per_item": 0.02})])
    thread = runner.run_in_thread()
    wait_until(lambda: len(_progress_steps(runner, job_id)) > 10, what="the monitor to collect")

    monitor.send_signal(signal.SIGKILL)
    monitor.wait(5)
    stored = len(_progress_steps(runner, job_id))
    time.sleep(0.5)
    assert thread.is_alive() and len(_progress_steps(runner, job_id)) == stored < 150, (
        "events stopped arriving when it died, while the job ran on"
    )
    thread.join(30)

    assert runner.job(job_id)["state"] == "completed", "the job never noticed"
    # M7: the supervisor backfills the spool when the stage ends (ADR-0005), so nothing
    # waits for the monitor; its late stream entries are then stored once, not twice.
    assert _progress_steps(runner, job_id) == list(range(1, 151)), "nothing lost"
    runner.collect()  # the monitor comes back
    assert _progress_steps(runner, job_id) == list(range(1, 151)), "nothing twice"


def test_NFR_13_kill_the_job_and_the_monitor_continues(
    runner: Runner, monitor: subprocess.Popen[bytes]
) -> None:
    doomed = runner.create([("synthetic", {"items": 50, "kill_signal_at": 5})])
    assert runner.run_queued() == ["failed"]
    assert runner.job(doomed)["state"] == "failed"

    following = runner.create([("synthetic", {"items": 20})])
    assert runner.run_queued() == ["completed"]

    wait_until(
        lambda: _progress_steps(runner, following) == list(range(1, 21)),
        what="the monitor to keep collecting after a job was killed",
    )
    assert monitor.poll() is None, "the monitor is still running"
    assert _count("resource_samples") >= 1, "and still sampling"


def _sessions_waiting_for_a_lock() -> int:
    with get_engine().connect() as connection:
        query = (
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE wait_event_type = 'Lock' AND datname = current_database()"
        )
        return int(connection.execute(text(query)).scalar_one())


def test_NFR_13_collector_and_supervisor_lock_job_rows_in_one_order(runner: Runner) -> None:
    """Regression for E-009 (first CI run, 2026-11-15): a deadlock between the two.

    The supervisor's ``finalize`` locks the job row and then updates the stage row.
    The collector used to update the stage row from an event and then, for
    ``stage_failed``, the job's summary: the opposite order. Here the interleaving
    is forced instead of left to timing.
    """
    job_id = runner.create([("synthetic", {"items": 1})])
    event = Event(
        job_id=job_id,
        stage="synthetic",
        type="stage_failed",
        level="ERROR",
        message="Stage synthetic failed",
        reason="planted for the test",
        suggested_action="none",
        metrics={"code": "stage_failed"},  # as the child sends it; this updates the stage row
    )
    errors: list[BaseException] = []

    def collect() -> None:
        try:
            with Session(get_engine()) as session, session.begin():
                write_batch(session, [event], [])
        except BaseException as exc:
            errors.append(exc)

    with Session(get_engine()) as supervisor, supervisor.begin():
        assert supervisor.get(Job, job_id, with_for_update=True) is not None
        collecting = threading.Thread(target=collect)
        collecting.start()
        wait_until(
            lambda: _sessions_waiting_for_a_lock() >= 1, what="the collector to wait for a lock"
        )
        stage = supervisor.get(JobStage, (job_id, 0))
        assert stage is not None
        stage.finished_at = datetime.now(UTC)
        supervisor.flush()  # with the old order: "deadlock detected" here or in the collector
    collecting.join(10)

    assert not collecting.is_alive()
    assert errors == []
    assert runner.job(job_id)["summary"] is not None, "the collector finished its work after"
