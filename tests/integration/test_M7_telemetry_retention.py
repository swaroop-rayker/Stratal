"""NFR-12: per-second telemetry is kept 7 days, then downsampled to 1-minute averages.

Runs the downsampling job against the real PostgreSQL with samples written as the
sampler and a job's reporter write them.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.db.engine import get_engine
from stratal.db.models import ProcessSample, ProcessSample1m, ResourceSample, ResourceSample1m
from stratal.mon.retention import cutoff, downsample
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 0, 30, tzinfo=UTC)
OLD = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)  # eight days before NOW


def _resource(ts: datetime, cpu: float, job_id: str | None = "job_0001") -> ResourceSample:
    return ResourceSample(
        ts=ts, job_id=job_id, stage="label" if job_id else None, active_jobs=None, interval_s=1.0,
        cpu_percent=cpu, cpu_per_core=[cpu, cpu], ram_used_mb=4000 + cpu, ram_available_mb=6000,
        ram_total_mb=10000, swap_used_mb=0, gpu_util_percent=None, gpu_mem_util_percent=None,
        vram_used_mb=3000.0, vram_total_mb=6141.0, gpu_temp_c=60.0, gpu_power_w=40.0,
        disk_read_mb_s=1.0, disk_write_mb_s=2.0,
    )  # fmt: skip


def _process(ts: datetime, rss: float, peak: float, job_id: str = "job_0001") -> ProcessSample:
    return ProcessSample(
        ts=ts, job_id=job_id, stage="label", rss_mb=rss, torch_allocated_mb=rss / 2,
        torch_reserved_mb=rss, torch_max_allocated_mb=peak,
    )  # fmt: skip


def _seed(session: Session) -> None:
    for minute in range(3):  # three whole minutes, 60 one-second samples each
        for second in range(60):
            ts = OLD + timedelta(minutes=minute, seconds=second)
            session.add(_resource(ts, cpu=10.0 * (minute + 1) + (second % 2)))
            session.add(_process(ts, rss=1000.0 + minute, peak=2000.0 + second))
    session.add(_resource(OLD + timedelta(seconds=5), cpu=99.0, job_id=None))  # idle sampler
    for second in range(10):  # recent: kept raw
        session.add(_resource(NOW - timedelta(hours=1, seconds=second), cpu=50.0))
        session.add(_process(NOW - timedelta(hours=1, seconds=second), rss=3000.0, peak=3500.0))
    session.commit()


def _count(session: Session, model: object) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0  # type: ignore[arg-type]


def test_NFR_12_samples_older_than_7_days_become_1_minute_averages(bus: Bus) -> None:
    with Session(get_engine()) as session:
        _seed(session)

        result = downsample(session, now=NOW, keep_days=7)

        assert (result.resource_rows, result.process_rows) == (181, 180)
        averaged = session.scalars(
            select(ResourceSample1m).order_by(ResourceSample1m.ts, ResourceSample1m.job_id)
        ).all()
        job_rows = [row for row in averaged if row.job_id == "job_0001"]
        assert [row.ts for row in job_rows] == [OLD + timedelta(minutes=m) for m in range(3)]
        assert [row.samples for row in job_rows] == [60, 60, 60]
        assert [row.cpu_percent for row in job_rows] == pytest.approx([10.5, 20.5, 30.5])
        assert job_rows[0].ram_used_mb == pytest.approx(4010.5)
        assert job_rows[0].gpu_util_percent is None, "unreadable stays null, never zero"
        idle = [row for row in averaged if row.job_id is None]
        assert len(idle) == 1 and idle[0].cpu_percent == pytest.approx(99.0)

        process = session.scalars(select(ProcessSample1m).order_by(ProcessSample1m.ts)).all()
        assert [row.rss_mb for row in process] == pytest.approx([1000.0, 1001.0, 1002.0])
        assert [row.torch_max_allocated_mb for row in process] == [2059.0, 2059.0, 2059.0]
        assert result.minutes == len(averaged) + len(process) == 7

        # The raw rows it averaged are gone; the last week's are untouched.
        assert _count(session, ResourceSample) == 10 and _count(session, ProcessSample) == 10
        oldest = session.scalar(select(func.min(ResourceSample.ts)))
        assert oldest is not None and oldest > NOW - timedelta(days=7)


def test_NFR_12_a_second_run_moves_nothing_and_a_partial_minute_stays_raw(bus: Bus) -> None:
    edge = cutoff(NOW, 7)
    assert edge == datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    with Session(get_engine()) as session:
        session.add(_resource(edge - timedelta(seconds=1), cpu=1.0))  # last whole minute: moved
        session.add(_resource(edge + timedelta(seconds=10), cpu=2.0))  # minute of the edge: kept
        session.commit()

        first = downsample(session, now=NOW, keep_days=7)
        second = downsample(session, now=NOW, keep_days=7)

        assert (first.resource_rows, first.minutes) == (1, 1)
        assert (second.resource_rows, second.process_rows, second.minutes) == (0, 0, 0)
        assert _count(session, ResourceSample1m) == 1
        assert session.scalars(select(ResourceSample.cpu_percent)).all() == [2.0]


@pytest.mark.usefixtures("fake_units")
def test_NFR_12_an_old_job_still_has_its_charts_from_the_averages(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    source = make_image_folder(tmp_path / "src", valid=4).root
    job = api.run({"dataset": "nfr12", "source": {"path": str(source)}, "until_stage": "ingest"})
    with Session(get_engine()) as session:
        for second in range(120):
            ts = OLD + timedelta(seconds=second)
            session.add(_resource(ts, cpu=float(second // 60), job_id=job["id"]))
            session.add(_process(ts, rss=500.0, peak=600.0, job_id=job["id"]))
        session.commit()
        downsample(session, now=NOW, keep_days=7)

    charts = api.client.get(f"/api/v1/jobs/{job['id']}/telemetry").json()

    old = [p for p in charts["resource"] if p["ts"].startswith("2026-11-12")]
    assert [p["cpu_percent"] for p in old] == [0.0, 1.0]
    assert old[0]["cpu_per_core"] == [] and old[0]["active_jobs"] is None
    assert [p["rss_mb"] for p in charts["process"] if p["ts"].startswith("2026-11-12")] == [
        500.0,
        500.0,
    ]
