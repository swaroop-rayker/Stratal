"""Alembic environment. Applied migrations are never edited (docs/DATA_MODEL.md)."""

from alembic import context

from stratal.core.logging import configure_logging
from stratal.core.settings import get_settings
from stratal.db.base import Base
from stratal.db.engine import get_engine

configure_logging(get_settings().log_level)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
