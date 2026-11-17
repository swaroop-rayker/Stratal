"""MON-1.4 (ADR-0005 decision 2): events the collector never got are backfilled from
the job's spool file, once each, by the supervisor after every stage."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.cli.__main__ import main as cli
from stratal.core.bus import Bus
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Event as EventRow
from stratal.mon.backfill import backfill_job, spool_path
from stratal.mon.events import Event
from stratal.mon.store import insert_events
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration


def _stored(job_id: str) -> int:
    with Session(get_engine()) as session:
        query = select(func.count()).select_from(EventRow).where(EventRow.job_id == job_id)
        return session.scalar(query) or 0


@pytest.mark.usefixtures("fake_units")
def test_MON_1_4_a_job_whose_events_never_reached_the_collector_still_has_them(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    source = make_image_folder(tmp_path / "src", valid=4).root
    response = api.post(
        {"dataset": "backfill", "source": {"path": str(source)}, "until_stage": "clean"}
    )
    job_id = response.json()["id"]
    runner.queue.extend(api.queued)
    api.queued.clear()

    runner.run_queued()  # no collector runs: the stream is never read

    spooled = spool_path(get_settings().data_dir, job_id).read_text().splitlines()
    stage_events = [json.loads(line) for line in spooled]
    assert {e["type"] for e in stage_events} >= {"stage_started", "stage_completed"}
    with Session(get_engine()) as session:
        uids = set(session.scalars(select(EventRow.event_uid).where(EventRow.job_id == job_id)))
    assert {e["event_uid"] for e in stage_events} <= {str(u) for u in uids}, "every one is stored"

    before = _stored(job_id)
    runner.collect()  # the stream copies arrive late: nothing is stored twice
    assert _stored(job_id) == before


def test_MON_1_4_backfill_inserts_only_missing_events_and_skips_a_cut_off_line(
    bus: Bus, tmp_path: Path
) -> None:
    events = [Event(job_id="job_0042", stage="ingest", message=f"step {i}") for i in range(5)]
    spool = spool_path(tmp_path, "job_0042")
    spool.parent.mkdir(parents=True)
    other = Event(job_id="job_0043", stage="ingest", message="not this job's")
    lines = [e.to_json() for e in events] + [other.to_json(), events[0].to_json()[:40]]
    spool.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with Session(get_engine()) as session, session.begin():
        insert_events(session, events[:2])  # the collector got the first two

    with Session(get_engine()) as session, session.begin():
        first = backfill_job(session, tmp_path, "job_0042")
    with Session(get_engine()) as session, session.begin():
        second = backfill_job(session, tmp_path, "job_0042")

    assert (first.read, first.inserted, first.skipped) == (5, 3, 2)
    assert second.inserted == 0
    assert _stored("job_0042") == 5 and _stored("job_0043") == 0
    with Session(get_engine()) as session, session.begin():
        missing = backfill_job(session, tmp_path, "job_9999")
    assert (missing.read, missing.inserted) == (0, 0), "no spool file is not an error"


def test_MON_1_4_backfill_from_the_command_line(
    bus: Bus, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = get_settings().data_dir
    spool = spool_path(data_dir, "job_0077")
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_text(Event(job_id="job_0077", stage="split", message="m").to_json() + "\n")

    assert cli(["events", "backfill", "--job", "job_0077"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == [{"job_id": "job_0077", "read": 1, "inserted": 1, "skipped": 0}]
    assert cli(["events", "backfill"]) == 1, "a job or --all is required"
