"""The GPU lease: one holder of the GPU at a time, across processes (ADR-0006 rule 2, BUG6).

A PostgreSQL session-level advisory lock, taken on a connection of its own and
held for as long as the holder uses the GPU. If the holder dies, PostgreSQL
ends its session and the lock goes with it, so a crash cannot leave the GPU
locked. The connection's ``application_name`` names the holder, so whoever is
refused can be told who has it.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, text

from stratal.core.errors import GpuBusy

LEASE_KEY = 0x5354_5241_5441_4C01
"""The advisory-lock key ("STRATAL" and 1); below 2**63, so a valid bigint."""
_NAME_PREFIX = "stratal-gpu:"


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


@contextmanager
def gpu_lease(
    engine: Engine, *, holder: str, wait_s: float = 0.0, poll_s: float = 0.5
) -> Generator[None]:
    """Hold the GPU lease for the ``with`` block, or raise :class:`GpuBusy`.

    ``holder`` names the user, e.g. ``job_0042/label``. ``wait_s`` is how long to
    keep trying; 0 means try once.
    """
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        connection.execute(
            text("SELECT set_config('application_name', :name, false)"),
            {"name": f"{_NAME_PREFIX}{holder}"[:63]},
        )
        deadline = time.monotonic() + wait_s
        while True:
            taken = connection.execute(
                text("SELECT pg_try_advisory_lock(CAST(:key AS bigint))"), {"key": LEASE_KEY}
            ).scalar_one()
            if taken:
                break
            if time.monotonic() >= deadline:
                other = current_holder(engine)
                raise GpuBusy(
                    f"The GPU is in use by {other or 'another process'}.",
                    suggested_action="Wait for it to finish, or cancel it, then retry.",
                )
            time.sleep(poll_s)
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": LEASE_KEY}
            )
    finally:
        try:
            connection.execute(text("SELECT set_config('application_name', '', false)"))
        finally:
            connection.close()
