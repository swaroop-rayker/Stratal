"""NFR-11: monitoring uses under 2% CPU and adds under 1% to job runtime (CL-15 AC:
a benchmark with monitoring on and off).

Monitoring on is the shipped setup: the ``monitor`` process (collector, sampler,
watchdog) and, in the job, the event emitter and the memory reporter. Off is no
monitor and ``STRATAL_MONITORING_ENABLED=false`` in the job. The workload is the
synthetic stage doing CPU work with one progress event per item, about 50 events
a second: far more than a real stage sends, so this measures a worst case.

Marked ``benchmark``: timing needs a quiet machine, so it runs with
``check.ps1 -Bench`` and not in the normal integration run.
"""

import os
import statistics
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime

import psutil
import pytest

from stratal.core.bus import Bus
from stratal.testing.harness import Runner

pytestmark = [pytest.mark.integration, pytest.mark.benchmark]

ITEMS = 1000
CPU_PER_ITEM_S = 0.02
ROUNDS = 3
PRODUCTION = {  # the shipped intervals, not the integration tests' shortened ones
    "STRATAL_SAMPLE_INTERVAL_ACTIVE_S": "1.0",
    "STRATAL_SAMPLE_INTERVAL_IDLE_S": "10.0",
    "STRATAL_PROCESS_SAMPLE_INTERVAL_S": "1.0",
    "STRATAL_HEARTBEAT_INTERVAL_S": "5.0",
    "STRATAL_WATCHDOG_INTERVAL_S": "5.0",
}


@pytest.fixture
def production_intervals(monkeypatch: pytest.MonkeyPatch, bus: Bus) -> Iterator[None]:
    for name, value in PRODUCTION.items():
        monkeypatch.setenv(name, value)
    yield


def _stage_seconds(runner: Runner, job_id: str) -> float:
    stage = runner.job(job_id)["stages"][0]
    assert stage["status"] == "completed"
    started = datetime.fromisoformat(stage["started_at"])
    finished = datetime.fromisoformat(stage["finished_at"])
    return (finished - started).total_seconds()


def _run_job(runner: Runner) -> float:
    job_id = runner.create(
        [
            (
                "synthetic",
                {"items": ITEMS, "seconds_per_item": 0.0, "cpu_seconds_per_item": CPU_PER_ITEM_S},
            )
        ]
    )
    runner.run_queued()
    return _stage_seconds(runner, job_id)


def _cpu_seconds(process: psutil.Process) -> float:
    times = process.cpu_times()
    return times.user + times.system


@pytest.mark.usefixtures("production_intervals")
def test_NFR_11_monitoring_costs_under_2_percent_cpu_and_1_percent_runtime(
    runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on: list[float] = []
    off: list[float] = []
    monitor_cpu_s = 0.0
    monitor_wall_s = 0.0
    _run_job(runner)  # warm-up: imports and caches
    for _ in range(ROUNDS):
        monkeypatch.setenv("STRATAL_MONITORING_ENABLED", "false")
        off.append(_run_job(runner))

        monkeypatch.setenv("STRATAL_MONITORING_ENABLED", "true")
        monitor = subprocess.Popen([sys.executable, "-m", "stratal.mon"], env=dict(os.environ))
        try:
            time.sleep(3.0)  # let it start, so its import cost is not counted
            probe = psutil.Process(monitor.pid)
            cpu_before, wall_before = _cpu_seconds(probe), time.monotonic()
            on.append(_run_job(runner))
            monitor_cpu_s += _cpu_seconds(probe) - cpu_before
            monitor_wall_s += time.monotonic() - wall_before
        finally:
            monitor.kill()
            monitor.wait(5)

    added = statistics.median(on) / statistics.median(off) - 1
    monitor_share = monitor_cpu_s / monitor_wall_s / (psutil.cpu_count() or 1)
    print(
        f"NFR-11: runtime off {sorted(off)} s, on {sorted(on)} s, median added {added:+.2%}; "
        f"monitor {monitor_cpu_s / monitor_wall_s:.2%} of one core, "
        f"{monitor_share:.3%} of {psutil.cpu_count()} logical CPUs"
    )
    assert added < 0.01, f"monitoring added {added:.2%} to the job's runtime"
    assert monitor_share < 0.02, f"monitoring used {monitor_share:.2%} of the CPU"
