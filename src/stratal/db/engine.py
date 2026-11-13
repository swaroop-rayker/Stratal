"""The SQLAlchemy engine for this process (ADR-0002)."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from stratal.core.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide engine. Connections are checked before use."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)
