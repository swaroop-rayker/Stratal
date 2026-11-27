"""Shared fixtures for tests that use the real PostgreSQL and Redis (SPEC §8.1: no mocking).

The tests get their own database (``stratal_test``), their own Redis database
(number 15) and their own data folder. Job tests commit rows and start real
processes, so they must not share the development stack's database or its
event stream, where a running monitor would consume their events.
"""

import importlib.util
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings

_HAS_TEST_CLIENT = importlib.util.find_spec("httpx2") is not None


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip modules that use the API's test client in an image without it.

    The worker-cpu image has no httpx2, so a module importing FastAPI's TestClient
    (directly or through ``stratal.testing.pipeline``) cannot even be imported there.
    None of those modules has ``ml``-marked tests, the only ones run in that image
    (check.ps1, step "integration (ml)"); everywhere else they are collected as usual.
    """
    if _HAS_TEST_CLIENT or collection_path.suffix != ".py":
        return None
    if not collection_path.name.startswith("test_"):
        return None
    source = collection_path.read_text(encoding="utf-8")
    if "TestClient" in source or "stratal.testing.pipeline" in source:
        return True
    return None


_main_url = make_url(get_settings().database_url)
_redis_base = get_settings().redis_url.rsplit("/", 1)[0]
os.environ.update(
    STRATAL_DATABASE_URL=_main_url.set(database="stratal_test").render_as_string(
        hide_password=False
    ),
    STRATAL_REDIS_URL=f"{_redis_base}/15",
    STRATAL_DATA_DIR=tempfile.mkdtemp(prefix="stratal-test-"),
    STRATAL_EXTRA_STAGES="stratal.testing.stages",
    STRATAL_HEARTBEAT_INTERVAL_S="0.3",
    STRATAL_CANCEL_GRACE_S="1.0",
    STRATAL_PROCESS_SAMPLE_INTERVAL_S="0.2",
)
get_settings.cache_clear()

from stratal.core.bus import Bus  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.mon.collector import Collector  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402

get_engine.cache_clear()

_TABLES = (
    "events, resource_samples, process_samples, resource_samples_1m, process_samples_1m, "
    "job_transitions, job_stages, "
    "review_items, review_sessions, dataset_versions, jobs, datasets, derivations, settings, "
    "chat_messages, inference_requests, chat_sessions"
)


@pytest.fixture(scope="session")
def migrated() -> None:
    """Create the test database if needed and bring it to the newest migration."""
    admin = create_engine(_main_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'stratal_test'")
        ).first()
        if exists is None:
            connection.execute(text("CREATE DATABASE stratal_test"))
    admin.dispose()
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def session(migrated: None) -> Iterator[Session]:
    """A session inside a transaction that is rolled back after the test."""
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            yield db
        transaction.rollback()


@pytest.fixture
def bus(migrated: None) -> Iterator[Bus]:
    """An empty database and an empty Redis, for tests that commit and start processes."""
    client = Bus(get_settings().redis_url)
    client.flush_all()
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    yield client
    client.flush_all()
    client.close()


@pytest.fixture
def runner(bus: Bus) -> Runner:
    return Runner(bus)


@pytest.fixture
def reset_state(bus: Bus, runner: Runner) -> Callable[[], None]:
    """Empty the database and Redis again mid-test; files in the data folder stay.

    The runner gets a new collector, since flushing Redis removes its consumer group.
    """

    def reset() -> None:
        bus.flush_all()
        with get_engine().begin() as connection:
            connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        runner.collector = Collector(bus, get_engine())

    return reset


@pytest.fixture
def fake_units(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the owner's units as fakes, here and in the stage processes (M5 on)."""
    from stratal.runtime import stages as stage_registry
    from stratal.testing.pipeline import FAKE_MODULES, restore_real_units

    monkeypatch.setenv("STRATAL_EXTRA_STAGES", ",".join(FAKE_MODULES))
    stage_registry.load_extra_stages()
    yield
    restore_real_units()
