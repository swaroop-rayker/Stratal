"""Helpers for tests of the job runtime: drive jobs without Celery, and wait for things."""

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.db.engine import get_engine
from stratal.mon.collector import Collector
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue
from stratal.runtime.supervisor import run_stage

Plan = list[tuple[str, dict[str, Any]]]


class Runner:
    """Drives jobs without Celery: stages run in this process's thread, one after another."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.queue: list[Enqueue] = []
        self.collector = Collector(bus, get_engine())

    def create(self, plan: Plan, spec: dict[str, Any] | None = None) -> str:
        with Session(get_engine()) as session, session.begin():
            job, first = job_service.create_job_from_plan(
                session, kind="pipeline", spec=spec or {"dataset": "test"}, plan=plan
            )
            job_id = job.id
        self.queue.append(first)
        return job_id

    def run_queued(self) -> list[str]:
        """Run every queued stage, including the ones they queue. Returns the outcomes."""
        outcomes: list[str] = []
        while self.queue:
            item = self.queue.pop(0)
            outcomes.append(run_stage(item.job_id, item.idx, enqueue=self.queue.append))
        return outcomes

    def run_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_queued, daemon=True)
        thread.start()
        return thread

    def collect(self) -> int:
        """Store everything waiting in the event stream."""
        total = 0
        while (handled := self.collector.run_once(block_ms=200)) > 0:
            total += handled
        return total

    def job(self, job_id: str) -> dict[str, Any]:
        with Session(get_engine()) as session:
            return job_service.job_view(session, job_service.get_job(session, job_id))

    def events(self, job_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE job_id = :job_id" if job_id else ""
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(f"SELECT * FROM events {where} ORDER BY id"), {"job_id": job_id}
            )
            return [dict(row._mapping) for row in rows]  # pyright: ignore[reportPrivateUsage]


def wait_until(condition: Callable[[], object], *, timeout_s: float = 10.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {what or 'condition'}")
