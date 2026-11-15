"""The sampler: system and GPU readings on a fixed interval (MON-2.1 to MON-2.3).

Runs in the monitor, independent of any job. It samples every second while a
job is active and every ten seconds when idle (both configurable), and tags
each sample with the active job and stage, which it reads from the registry the
supervisors keep in Redis.
"""

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from stratal.core.bus import LIVE_GLOBAL, Bus, live_channel
from stratal.core.logging import get_logger
from stratal.db.models import ResourceSample
from stratal.mon import probes
from stratal.mon.events import utc_now

log = get_logger(__name__)

ActiveJobs = dict[str, dict[str, str]]


@dataclass(frozen=True)
class Reading:
    """Everything one sample measures. GPU fields are ``None`` when unavailable."""

    cpu_percent: float
    cpu_per_core: list[float]
    ram_used_mb: float
    ram_available_mb: float
    ram_total_mb: float
    swap_used_mb: float
    gpu_util_percent: float | None
    gpu_mem_util_percent: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    gpu_temp_c: float | None
    gpu_power_w: float | None
    disk_read_mb_s: float
    disk_write_mb_s: float


class MachineProbe:
    """Reads psutil and NVML, and turns the cumulative disk counters into rates."""

    def __init__(self) -> None:
        self.gpu = probes.GpuProbe()
        self._last: tuple[float, int, int] | None = None
        probes.read_system()  # the first CPU reading only sets the baseline

    def __call__(self) -> Reading:
        system = probes.read_system()
        gpu = self.gpu.read()
        now = time.monotonic()
        read_rate = write_rate = 0.0
        if self._last is not None:
            elapsed = max(now - self._last[0], 1e-6)
            read_rate = max(system.disk_read_bytes - self._last[1], 0) / probes.MB / elapsed
            write_rate = max(system.disk_write_bytes - self._last[2], 0) / probes.MB / elapsed
        self._last = (now, system.disk_read_bytes, system.disk_write_bytes)
        return Reading(
            cpu_percent=system.cpu_percent,
            cpu_per_core=system.cpu_per_core,
            ram_used_mb=round(system.ram_used_mb, 1),
            ram_available_mb=round(system.ram_available_mb, 1),
            ram_total_mb=round(system.ram_total_mb, 1),
            swap_used_mb=round(system.swap_used_mb, 1),
            gpu_util_percent=gpu.util_percent if gpu else None,
            gpu_mem_util_percent=gpu.mem_util_percent if gpu else None,
            vram_used_mb=round(gpu.vram_used_mb, 1) if gpu and gpu.vram_used_mb else None,
            vram_total_mb=round(gpu.vram_total_mb, 1) if gpu and gpu.vram_total_mb else None,
            gpu_temp_c=gpu.temp_c if gpu else None,
            gpu_power_w=gpu.power_w if gpu else None,
            disk_read_mb_s=round(read_rate, 3),
            disk_write_mb_s=round(write_rate, 3),
        )


def primary_job(active: ActiveJobs) -> tuple[str | None, str | None]:
    """The job a sample is tagged with. The GPU job wins when two are active; otherwise
    the one that started first (DATA_MODEL.md §4.7)."""
    if not active:
        return None, None
    ordered = sorted(
        active.items(), key=lambda item: (item[1].get("queue") != "gpu", item[1].get("since", ""))
    )
    job_id, info = ordered[0]
    return job_id, info.get("stage")


class Sampler:
    def __init__(
        self,
        engine: Engine,
        *,
        probe: Callable[[], Reading],
        active_jobs: Callable[[], ActiveJobs],
        bus: Bus | None = None,
        active_interval_s: float = 1.0,
        idle_interval_s: float = 10.0,
    ) -> None:
        self._engine = engine
        self._probe = probe
        self._active_jobs = active_jobs
        self._bus = bus
        self.active_interval_s = active_interval_s
        self.idle_interval_s = idle_interval_s

    def sample(self, active: ActiveJobs, interval_s: float, ts: datetime | None = None) -> None:
        """Take one reading, store it, and send it to live viewers."""
        reading = self._probe()
        job_id, stage = primary_job(active)
        values: dict[str, Any] = {
            "ts": ts or utc_now(),
            "job_id": job_id,
            "stage": stage,
            "active_jobs": [{"job_id": j, **info} for j, info in sorted(active.items())] or None,
            "interval_s": interval_s,
            **asdict(reading),
        }
        with Session(self._engine) as session, session.begin():
            session.add(ResourceSample(**values))
        if self._bus is not None:
            with contextlib.suppress(Exception):
                payload = {**values, "ts": values["ts"].isoformat()}
                message = {"kind": "sample", "resource": payload}
                self._bus.publish(LIVE_GLOBAL, message)
                for active_job in active:
                    self._bus.publish(live_channel(active_job), message)

    def run(self, stop: threading.Event) -> None:
        """Sample until ``stop`` is set.

        The registry is checked once per active interval even while idle, so the
        switch to per-second sampling happens within a second of a job starting.
        Deadlines come from the monotonic clock and are advanced by the interval,
        not by "now", so the spacing does not drift (MON-2.2.AC1).
        """
        due = time.monotonic()
        interval = self.idle_interval_s
        while not stop.is_set():
            try:
                active = self._active_jobs()
            except Exception:
                active = {}
            wanted = self.active_interval_s if active else self.idle_interval_s
            if wanted != interval:
                interval = wanted
                due = min(due, time.monotonic())  # switching interval takes effect now
            now = time.monotonic()
            if now >= due:
                try:
                    self.sample(active, interval)
                except Exception as exc:
                    log.warning("sample_failed", reason=str(exc)[:300])
                due += interval
                if due < now:  # we fell behind (a sleep, a long stall): start again from now
                    due = now + interval
            stop.wait(max(0.0, min(due - time.monotonic(), self.active_interval_s)))
