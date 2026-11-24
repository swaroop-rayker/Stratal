"""The monitor service: ``python -m stratal.mon`` (ADR-0005).

One process, three threads: collector, sampler and watchdog. The sampler also
runs the resource alerts on each sample, and the watchdog the stall alert and the
bottleneck diagnostics (ADR-0020). Each thread runs in its own restart loop, so
an error in one is logged and retried without stopping the other two. Jobs never
depend on this process being up (NFR-13).
"""

import signal
import threading
import time
from collections.abc import Callable

from sqlalchemy.engine import Engine

from stratal.core.bus import Bus
from stratal.core.logging import configure_logging, get_logger
from stratal.core.settings import Settings, get_settings
from stratal.db.engine import get_engine
from stratal.mon.alerts import Alerts, Limits
from stratal.mon.collector import Collector
from stratal.mon.sampler import MachineProbe, Sampler
from stratal.mon.watchdog import Watchdog
from stratal.runtime.celery_app import enqueue_stage

log = get_logger("stratal.monitor")


def _forever(name: str, stop: threading.Event, body: Callable[[], None]) -> threading.Thread:
    def loop() -> None:
        while not stop.is_set():
            try:
                body()
            except Exception as exc:
                log.error("monitor_part_failed", part=name, reason=str(exc)[:500])
                stop.wait(2.0)

    thread = threading.Thread(target=loop, name=name, daemon=True)
    thread.start()
    return thread


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    engine = get_engine()

    collector = Collector(Bus(settings.redis_url), engine)
    registry_bus = Bus(settings.redis_url, timeout_s=2.0)
    probe = MachineProbe()
    limits = _AlertLimits(engine, settings)
    watchdog = Watchdog(
        engine,
        enqueue=enqueue_stage,
        stale_after_s=settings.heartbeat_stale_after_s,
        max_attempts=settings.max_attempts,
        bus=registry_bus,
        stall_after_s=settings.stall_timeout_s,
        thermal_pause_c=settings.thermal_pause_c,
        thermal_resume_c=settings.thermal_resume_c,
        limits=lambda: _training_limits(engine, settings),
        disk_threshold=limits.disk,
    )
    alerts = Alerts(engine, limits=limits, pause=watchdog.pause_for_heat)
    sampler = Sampler(
        engine,
        probe=probe,
        active_jobs=registry_bus.get_active,
        bus=Bus(settings.redis_url, timeout_s=2.0),
        active_interval_s=settings.sample_interval_active_s,
        idle_interval_s=settings.sample_interval_idle_s,
        on_sample=alerts.observe,
        intervals=limits.intervals,
    )

    def watch() -> None:
        watchdog.run_once()
        stop.wait(settings.watchdog_interval_s)

    log.info("monitor_started", gpu_available=probe.gpu.available, gpu_error=probe.gpu.error)
    publish_system(registry_bus, probe)
    threads = [
        _forever("collector", stop, lambda: void(collector.run_once())),
        _forever("sampler", stop, lambda: sampler.run(stop)),
        _forever("watchdog", stop, watch),
    ]
    while not stop.is_set():
        time.sleep(0.5)
    for thread in threads:
        thread.join(3.0)
    log.info("monitor_stopped")


def _training_limits(engine: Engine, settings: Settings) -> tuple[float, float, float]:
    """``(stall_timeout_s, thermal_pause_c, thermal_resume_c)`` as saved on SCR-14 (OC-9)."""
    from sqlalchemy.orm import Session

    from stratal.runtime import limits

    with Session(engine) as session:
        values = limits.read(session, settings)
    return (
        float(values.stall_timeout_s),
        float(values.thermal_pause_c),
        float(values.thermal_resume_c),
    )


class _AlertLimits:
    """The alert limits from SCR-14's training and monitoring sections, read at most every
    5 s: the sampler calls this on every sample, and a query per second would count
    against NFR-11."""

    EVERY_S = 5.0

    def __init__(self, engine: Engine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._at = 0.0
        self._limits = Limits(
            settings.vram_alert_percent,
            settings.ram_alert_percent,
            settings.thermal_pause_c,
            settings.thermal_resume_c,
        )
        self._disk = settings.disk_bottleneck_mb_s

    def __call__(self) -> Limits:
        if time.monotonic() - self._at >= self.EVERY_S:
            from sqlalchemy.orm import Session

            from stratal.runtime import limits

            with Session(self._engine) as session:
                training = limits.read(session, self._settings)
                monitoring = limits.read_monitoring(session, self._settings)
            self._limits = Limits(
                float(monitoring.vram_alert_percent),
                float(monitoring.ram_alert_percent),
                float(training.thermal_pause_c),
                float(training.thermal_resume_c),
            )
            self._disk = float(monitoring.disk_bottleneck_mb_s)
            self._at = time.monotonic()
        return self._limits

    def disk(self) -> float:
        """The diagnostics' disk threshold, MB/s (ADR-0020 rule 8)."""
        self()
        return self._disk

    def intervals(self) -> tuple[float, float]:
        from sqlalchemy.orm import Session

        from stratal.runtime import limits

        with Session(self._engine) as session:
            values = limits.read_monitoring(session, self._settings)
        return float(values.sample_interval_active_s), float(values.sample_interval_idle_s)


def publish_system(bus: Bus, probe: MachineProbe) -> None:
    """Describe the machine once for ``GET /system`` (SCR-01). The API has no NVML."""
    import os

    import psutil

    gpu = probe.gpu.read()
    try:
        bus.set_system(
            {
                "gpu_name": probe.gpu.name(),
                "gpu_error": probe.gpu.error,
                "gpu_slowdown_c": probe.gpu.slowdown_c(),
                "vram_total_mb": gpu.vram_total_mb if gpu is not None else None,
                "ram_total_mb": psutil.virtual_memory().total / 2**20,
                "cpu_count": os.cpu_count(),
            }
        )
    except Exception as exc:
        log.warning("system_info_not_published", reason=str(exc)[:200])


def void(_value: object) -> None:
    """Discard a result so a lambda can be used where nothing is returned."""


if __name__ == "__main__":
    main()
