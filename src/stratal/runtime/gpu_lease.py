"""The GPU lease: one holder of the GPU at a time, across processes (ADR-0006 rule 2, BUG6).

A PostgreSQL session-level advisory lock, taken on a connection of its own and
held for as long as the holder uses the GPU. If the holder dies, PostgreSQL
ends its session and the lock goes with it, so a crash cannot leave the GPU
locked. The connection's ``application_name`` names the holder, so whoever is
refused can be told who has it.

A job that waits for the lease says so in Redis (``stratal:gpu:wanted``, a few
seconds' life, renewed while it waits). The inference service, the one
long-lived holder, gives the lease back when it sees that (ADR-0016 rule 5).
"""

import contextlib
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

from stratal.core.errors import GpuBusy

LEASE_KEY = 0x5354_5241_5441_4C01
"""The advisory-lock key ("STRATAL" and 1); below 2**63, so a valid bigint."""
_NAME_PREFIX = "stratal-gpu:"
WANTED_TTL_S = 5.0
"""How long a waiting job's "I want the GPU" lasts unless renewed."""


def _say_wanted(holder: str) -> None:
    """Tell the inference service a job is waiting. Best effort: Redis may be down."""
    with contextlib.suppress(Exception):
        from stratal.core.bus import Bus
        from stratal.core.settings import get_settings

        bus = Bus(get_settings().redis_url, timeout_s=1.0)
        try:
            bus.set_gpu_wanted(holder, ttl_s=WANTED_TTL_S)
        finally:
            bus.close()


def current_holder(engine: Engine) -> str | None:
    """Who holds the lease now, or ``None`` if nobody does."""
    query = text(
        "SELECT a.application_name FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
        " WHERE l.locktype = 'advisory' AND l.granted"
        " AND l.classid::bigint = :high AND l.objid::bigint = :low AND l.objsubid = 1"
    )
    with engine.connect() as connection:
        name = connection.execute(
            query, {"high": LEASE_KEY >> 32, "low": LEASE_KEY & 0xFFFF_FFFF}
        ).scalar()
    if name is None:
        return None
    return str(name).removeprefix(_NAME_PREFIX)


def _try_lock(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(CAST(:key AS bigint))"), {"key": LEASE_KEY}
        ).scalar_one()
    )


def _named(engine: Engine, holder: str) -> Connection:
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    connection.execute(
        text("SELECT set_config('application_name', :name, false)"),
        {"name": f"{_NAME_PREFIX}{holder}"[:63]},
    )
    return connection


def _close(connection: Connection) -> None:
    try:
        connection.execute(text("SELECT set_config('application_name', '', false)"))
    finally:
        connection.close()


@contextmanager
def gpu_lease(
    engine: Engine,
    *,
    holder: str,
    wait_s: float = 0.0,
    poll_s: float = 0.5,
    on_wait: Callable[[str], None] = _say_wanted,
) -> Generator[None]:
    """Hold the GPU lease for the ``with`` block, or raise :class:`GpuBusy`.

    ``holder`` names the user, e.g. ``job_0042/label``. ``wait_s`` is how long to
    keep trying; 0 means try once. While it waits, ``on_wait`` is called each poll.
    """
    connection = _named(engine, holder)
    try:
        deadline = time.monotonic() + wait_s
        while not _try_lock(connection):
            if time.monotonic() >= deadline:
                other = current_holder(engine)
                raise GpuBusy(
                    f"The GPU is in use by {other or 'another process'}.",
                    suggested_action="Wait for it to finish, or cancel it, then retry.",
                )
            on_wait(holder)
            time.sleep(poll_s)
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": LEASE_KEY}
            )
    finally:
        _close(connection)


class HeldLease:
    """The lease for a long-lived holder that keeps it across calls (the inference
    service, ADR-0016 rule 4). ``try_acquire`` never waits."""

    def __init__(self, engine: Engine, *, holder: str) -> None:
        self._engine = engine
        self._holder = holder
        self._connection: Connection | None = None

    @property
    def held(self) -> bool:
        return self._connection is not None

    def try_acquire(self) -> bool:
        if self._connection is not None:
            return True
        connection = _named(self._engine, self._holder)
        if _try_lock(connection):
            self._connection = connection
            return True
        _close(connection)
        return False

    def release(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": LEASE_KEY}
            )
        finally:
            _close(connection)
