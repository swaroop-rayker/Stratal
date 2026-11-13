"""NFR-10: structured logs (JSON) with job ID and version ID on every line."""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from stratal.core.logging import (
    bind_job_context,
    clear_job_context,
    configure_logging,
    get_logger,
)


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    stream = io.StringIO()
    configure_logging("INFO", stream)
    try:
        yield stream
    finally:
        clear_job_context()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_NFR_10_every_line_is_json_with_job_and_version_id(log_stream: io.StringIO) -> None:
    get_logger("stratal.test").info("before_any_job")
    bind_job_context(job_id="job_0142", version_id="ds_7f3a9c21b04e")
    get_logger("stratal.test").info("captioning_batch", batch=42, total=313)
    logging.getLogger("uvicorn.error").warning("a line from a standard-library logger")

    records = _records(log_stream)

    assert len(records) == 3
    for record in records:
        assert {"job_id", "version_id", "ts", "level", "event"} <= record.keys()
    assert records[0]["job_id"] is None
    assert records[0]["version_id"] is None
    for record in records[1:]:
        assert record["job_id"] == "job_0142"
        assert record["version_id"] == "ds_7f3a9c21b04e"
    assert records[1]["batch"] == 42
    assert records[2]["level"] == "warning"


def test_NFR_10_logs_can_be_filtered_by_id(log_stream: io.StringIO) -> None:
    bind_job_context(job_id="job_0001")
    get_logger("stratal.test").info("first_job_line")
    clear_job_context()
    bind_job_context(job_id="job_0002")
    get_logger("stratal.test").info("second_job_line")

    by_job = [r["event"] for r in _records(log_stream) if r["job_id"] == "job_0002"]

    assert by_job == ["second_job_line"]


def test_NFR_10_exceptions_stay_on_one_json_line(log_stream: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("stratal.test").exception("stage_failed")

    records = _records(log_stream)

    assert len(records) == 1
    assert "ValueError: boom" in str(records[0]["exception"])
