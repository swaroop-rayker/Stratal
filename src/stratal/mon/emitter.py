"""Sending events out of a job (ADR-0005, MON-1.4, NFR-13).

Each event is written to a local spool file first, then handed to a background
thread that appends it to the Redis stream. Emitting never blocks the job and
never raises into it: if Redis is down the job carries on and the spool file
keeps every event for a later backfill.
"""

import contextlib
import queue
import threading
import time
from pathlib import Path
from typing import IO, Protocol

from stratal.core.bus import Bus
from stratal.core.logging import get_logger
from stratal.mon.events import Event

log = get_logger(__name__)

KIND_EVENT = "event"
KIND_PROCESS_SAMPLE = "process_sample"


class Emitter(Protocol):
    def emit(self, event: Event) -> None: ...

    def send(self, kind: str, data: str) -> None: ...

    def close(self, timeout_s: float = 5.0) -> None: ...


class ListEmitter:
    """Keeps events in memory. For tests and for code that only needs to look at them."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.sent: list[tuple[str, str]] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def send(self, kind: str, data: str) -> None:
        self.sent.append((kind, data))

    def close(self, timeout_s: float = 5.0) -> None:
        del timeout_s


class StreamEmitter:
    """Spool file plus Redis stream, with a bounded queue between job and network."""

    def __init__(
        self,
        *,
        redis_url: str,
        spool_path: Path | None,
        maxlen: int = 100_000,
        queue_size: int = 10_000,
        retry_s: float = 1.0,
    ) -> None:
        self._redis_url = redis_url
        self._maxlen = maxlen
        self._retry_s = retry_s
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=queue_size)
        self._spool: IO[str] | None = None
        if spool_path is not None:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            self._spool = spool_path.open("a", encoding="utf-8")
        self._spool_lock = threading.Lock()
        self.dropped = 0
        """Events that did not reach the stream because the queue was full. They are
        still in the spool file."""
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="event-emitter", daemon=True)
        self._thread.start()

    def emit(self, event: Event) -> None:
        data = event.to_json()
        if self._spool is not None:
            with self._spool_lock, contextlib.suppress(OSError, ValueError):
                self._spool.write(data + "\n")
                self._spool.flush()
        self.send(KIND_EVENT, data)

    def send(self, kind: str, data: str) -> None:
        try:
            self._queue.put_nowait((kind, data))
        except queue.Full:
            self.dropped += 1

    def close(self, timeout_s: float = 5.0) -> None:
        """Give the background thread ``timeout_s`` to send what is queued, then stop."""
        self._closing.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        self._thread.join(timeout_s)
        if self._spool is not None:
            with self._spool_lock, contextlib.suppress(OSError):
                self._spool.close()

    def _run(self) -> None:
        bus: Bus | None = None
        warned = False
        while True:
            item = self._queue.get()
            if item is None:
                break
            kind, data = item
            while True:
                try:
                    if bus is None:
                        bus = Bus(self._redis_url, timeout_s=2.0)
                    bus.append(kind, data, maxlen=self._maxlen)
                    warned = False
                    break
                except Exception as exc:
                    if not warned:
                        log.warning("event_stream_unreachable", reason=str(exc))
                        warned = True
                    bus = None
                    if self._closing.is_set():
                        return  # shutting down: the spool file has the events
                    time.sleep(self._retry_s)
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.close()
