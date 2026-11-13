"""Dependency checks used by the health endpoint.

Each check returns nothing on success and raises on failure.
"""

import uuid
from collections.abc import Callable
from pathlib import Path

import redis
from sqlalchemy import text

from stratal.core.settings import get_settings
from stratal.db.engine import get_engine

Checker = Callable[[], None]


def check_postgres() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def check_redis() -> None:
    client = redis.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()  # pyright: ignore[reportUnknownMemberType]
    finally:
        client.close()


def check_data_volume() -> None:
    data_dir: Path = get_settings().data_dir
    probe = data_dir / f".health-{uuid.uuid4().hex}"
    probe.write_bytes(b"ok")
    probe.unlink()


def default_checkers() -> dict[str, Checker]:
    return {
        "postgres": check_postgres,
        "redis": check_redis,
        "data_volume": check_data_volume,
    }
