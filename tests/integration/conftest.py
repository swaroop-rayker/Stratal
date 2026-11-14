"""Shared fixtures for tests that use the real PostgreSQL (SPEC §8.1: no mocking)."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine


@pytest.fixture(scope="session")
def migrated() -> None:
    """Bring the schema to the newest migration once per test run."""
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def session(migrated: None) -> Iterator[Session]:
    """A session inside a transaction that is rolled back after the test."""
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            yield db
        transaction.rollback()
