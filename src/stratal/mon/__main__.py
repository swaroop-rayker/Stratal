"""The monitor service: ``python -m stratal.mon`` (ADR-0005).

One process, three threads: collector, sampler and watchdog. Each runs in its
own restart loop, so an error in one is logged and retried without stopping the
other two. Jobs never depend on this process being up (NFR-13).
"""

import signal
import threading
import time
from collections.abc import Callable

from stratal.core.bus import Bus
from stratal.core.logging import configure_logging, get_logger
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
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
    sampler = Sampler(
        engine,
        probe=probe,
        active_jobs=registry_bus.get_active,
        bus=Bus(settings.redis_url, timeout_s=2.0),
        active_interval_s=settings.sample_interval_active_s,
        idle_interval_s=settings.sample_interval_idle_s,
    )
    watchdog = Watchdog(
        engine,
        enqueue=enqueue_stage,
        stale_after_s=settings.heartbeat_stale_after_s,
        max_attempts=settings.max_attempts,
    )

    def watch() -> None:
        watchdog.run_once()
        stop.wait(settings.watchdog_interval_s)

    log.info("monitor_started", gpu_available=probe.gpu.available, gpu_error=probe.gpu.error)
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


def void(_value: object) -> None:
    """Discard a result so a lambda can be used where nothing is returned."""


if __name__ == "__main__":
    main()
