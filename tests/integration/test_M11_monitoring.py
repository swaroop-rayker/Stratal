"""MON-2.6, MON-2.7, MON-3.2, MON-4.4 (M11, ADR-0020): alerts, their actions,
diagnostics and two jobs overlaid, against the real PostgreSQL and Redis.

Samples are simulated (MON-2.6.AC1, MON-3.2.AC1): rows are fed to the alerts as the
sampler would, or planted in the tables the diagnostics and the overlay read. The heat
test runs a real job on the GPU queue with the synthetic stage.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.db.engine import get_engine
from stratal.mon.alerts import Alerts, Limits
from stratal.mon.events import Event
from stratal.mon.store import insert_events
from stratal.mon.watchdog import Watchdog
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import Enqueue
from stratal.testing.harness import Runner, wait_until
from stratal.testing.pipeline import PipelineApi

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
LIMITS = Limits(vram_percent=90, ram_percent=85, pause_c=83, resume_c=75)


def _sample(at: float, *, job: str | None = None, **figures: float) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": T0 + timedelta(seconds=at),
        "job_id": job,
        "stage": "train" if job else None,
        "vram_used_mb": 1000.0,
        "vram_total_mb": 6000.0,
        "ram_total_mb": 10000.0,
        "ram_available_mb": 7000.0,
        "gpu_temp_c": 60.0,
    }
    row.update(figures)
    return row


def _alerts(code: str | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT event_uid, job_id, level, metrics, caused_by, message FROM events "
        "WHERE type = 'alert'"
    )
    with get_engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(text(query + " ORDER BY id"))]  # pyright: ignore[reportPrivateUsage]
    return [r for r in rows if code is None or r["metrics"]["code"] == code]


def _create(
    runner: Runner | None, config: dict[str, Any], *, queue: str, kind: str = "pipeline"
) -> str:
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_job_from_plan(
            session,
            kind=kind,
            spec={"dataset": "test"},
            plan=[("synthetic", config)],
            queues={"synthetic": queue},
        )
        job_id = job.id
    if runner is not None:
        runner.queue.append(first)
    return job_id


def test_MON_2_6_each_alert_fires_once_and_is_stored_with_its_job_or_without_one(
    bus: Bus,
) -> None:
    alerts = Alerts(get_engine(), limits=lambda: LIMITS)
    hot = {"vram_used_mb": 5700.0, "ram_available_mb": 1000.0, "gpu_temp_c": 84.0}
    for at in (0, 1, 3, 5, 6):
        alerts.observe(_sample(at, job="job_0001", **hot))
    assert sorted(a["metrics"]["code"] for a in _alerts()) == ["gpu_hot", "ram_high", "vram_high"]
    assert all(a["job_id"] == "job_0001" and a["level"] == "WARN" for a in _alerts())
    vram = _alerts("vram_high")[0]
    assert vram["metrics"]["state"] == "fired" and vram["metrics"]["threshold"] == 90
    assert "VRAM at 95% of the card" in vram["message"]

    restarted = Alerts(get_engine(), limits=lambda: LIMITS)  # a monitor restart
    for at in (10, 16):
        restarted.observe(_sample(at, job="job_0001", **hot))
    assert len(_alerts()) == 3, "still the same crossing: nothing fires twice"

    for at in (20, 21):  # all three clear, then RAM crosses again with no job running
        restarted.observe(_sample(at))
    cleared = [a for a in _alerts() if a["metrics"]["state"] == "cleared"]
    assert len(cleared) == 3 and {c["caused_by"] for c in cleared} == {
        a["event_uid"] for a in _alerts() if a["metrics"]["state"] == "fired"
    }
    for at in (30, 36):
        restarted.observe(_sample(at, ram_available_mb=500.0))
    ram = [a for a in _alerts("ram_high") if a["metrics"]["state"] == "fired"]
    assert len(ram) == 2 and ram[-1]["job_id"] is None, "stored without a job (K4)"


def test_MON_2_7_heat_pauses_the_gpu_job_and_the_alert_shows_its_actions(
    runner: Runner,
) -> None:
    gpu_job = _create(runner, {"items": 200, "seconds_per_item": 0.05}, queue="gpu")
    temperatures = [60.0]
    requeued: list[Enqueue] = []
    watchdog = Watchdog(
        get_engine(), enqueue=requeued.append, bus=runner.bus, temperature=lambda: temperatures[-1]
    )
    alerts = Alerts(get_engine(), limits=lambda: LIMITS, pause=watchdog.pause_for_heat)
    thread = runner.run_in_thread()
    wait_until(lambda: runner.job(gpu_job)["state"] == "running", what="the job to start")

    now = datetime.now(UTC) - timedelta(seconds=30)  # the readings are in the past
    alerts.observe({**_sample(0, job=gpu_job, gpu_temp_c=84.0), "ts": now})
    assert runner.job(gpu_job)["state"] == "running", "one reading does not pause (point 2)"
    alerts.observe({**_sample(0, job=gpu_job, gpu_temp_c=84.0), "ts": now + timedelta(seconds=5)})
    thread.join(15)
    assert runner.job(gpu_job)["state"] == "paused"

    alert = _alerts("gpu_hot")[0]
    assert alert["message"].endswith(f"paused {gpu_job}")
    temperatures.append(72.0)
    assert watchdog.run_once() == [gpu_job]
    alerts.observe({**_sample(0, job=None, gpu_temp_c=72.0), "ts": now + timedelta(seconds=9)})

    caused = {
        e["metrics"].get("code"): str(e["caused_by"])
        for e in runner.events(gpu_job)
        if e["metrics"] and e.get("caused_by")
    }
    assert caused["thermal_pause"] == str(alert["event_uid"])
    assert caused["thermal_resume"] == str(alert["event_uid"])

    api = PipelineApi(runner)
    body = api.client.get(
        "/api/v1/system/alerts", params={"from": (now - timedelta(minutes=1)).isoformat()}
    ).json()
    (item,) = body["items"]
    assert item["code"] == "gpu_hot" and not item["firing"]
    assert [a["metrics"]["code"] for a in item["actions"]] == ["thermal_pause", "thermal_resume"]
    assert item["cleared"]["metrics"]["state"] == "cleared" and item["lasted_s"] == 4.0, (
        "fired at +5 s, cleared at +9 s"
    )
    assert body["thresholds"]["pause_c"] == 83
    runner.queue.extend(requeued)
    assert runner.run_queued() == ["completed"]


def test_MON_2_7_an_out_of_memory_failure_links_to_a_recent_vram_alert(bus: Bus) -> None:
    alert = uuid.uuid4()

    def failure(at: float, job: str = "job_0007") -> Event:
        return Event(
            ts=T0 + timedelta(seconds=at),
            job_id=job,
            stage="train",
            type="stage_failed",
            level="ERROR",
            message="Out of GPU memory at batch size 256",
            reason="PyTorch ran out of VRAM",
            suggested_action="reduce batch size from 256 → 128",
            metrics={"code": "gpu_oom"},
        )

    with Session(get_engine()) as session, session.begin():
        insert_events(
            session,
            [
                Event(
                    event_uid=alert,
                    ts=T0,
                    job_id="job_0007",
                    stage="train",
                    type="alert",
                    level="WARN",
                    message="VRAM at 93% of the card",
                    reason="Above the 90% threshold (SCR-14).",
                    suggested_action="None needed yet.",
                    metrics={"code": "vram_high", "state": "fired"},
                ),
            ],
        )
    with Session(get_engine()) as session, session.begin():  # the failure comes later
        insert_events(
            session,
            [
                failure(20),
                failure(25, job="job_0008"),
            ],
        )
    with get_engine().connect() as c:
        linked = dict(
            c.execute(
                text("SELECT job_id, caused_by FROM events WHERE type = 'stage_failed'")
            ).all()
        )
    assert linked == {"job_0007": alert, "job_0008": None}, "only the alert's own job"

    with Session(get_engine()) as session, session.begin():
        insert_events(
            session,
            [
                Event(
                    ts=T0 + timedelta(seconds=30),
                    job_id="job_0007",
                    type="alert",
                    message="VRAM back below 85%",
                    metrics={"code": "vram_high", "state": "cleared"},
                    caused_by=alert,
                ),
            ],
        )
    with Session(get_engine()) as session, session.begin():
        insert_events(
            session,
            [
                failure(200),
            ],
        )
    with get_engine().connect() as c:
        late = c.execute(
            text("SELECT caused_by FROM events WHERE type = 'stage_failed' AND ts > :t"),
            {"t": T0 + timedelta(seconds=100)},
        ).scalar_one()
    assert late is None, "cleared more than a minute before: not caused by it"


def test_MON_2_7_no_progress_is_an_alert_and_stalled_records_it(runner: Runner) -> None:
    job_id = _create(runner, {"items": 3, "seconds_per_item": 2.5}, queue="cpu")
    watchdog = Watchdog(
        get_engine(), enqueue=lambda _i: None, stall_after_s=1.0, temperature=lambda: None
    )
    thread = runner.run_in_thread()

    def recovered() -> bool:
        runner.collect()
        watchdog.run_once()
        states = [e["metrics"].get("state") for e in runner.events(job_id) if e["type"] == "alert"]
        return "cleared" in states

    wait_until(recovered, timeout_s=20, what="no progress, then progress")
    thread.join(20)
    events = runner.events(job_id)
    fired = next(e for e in events if e["type"] == "alert" and e["metrics"]["state"] == "fired")
    assert fired["metrics"]["code"] == "no_progress" and fired["level"] == "WARN"
    stalled = next(e for e in events if e["metrics"] and e["metrics"].get("code") == "stalled")
    assert stalled["caused_by"] == fired["event_uid"]
    cleared = next(e for e in events if e["type"] == "alert" and e["metrics"]["state"] == "cleared")
    assert cleared["caused_by"] == fired["event_uid"]


def _running_gpu_job(*, minutes: float = 2.0) -> str:
    job_id = _create(None, {"items": 1}, queue="gpu", kind="train")
    with get_engine().begin() as c:
        c.execute(
            text(
                "UPDATE jobs SET state = 'running', "
                "started_at = now() - make_interval(secs => :m * 60), "
                "heartbeat_at = now() WHERE id = :j"
            ),
            {"j": job_id, "m": minutes},
        )
        c.execute(
            text(
                "UPDATE job_stages SET status = 'running', "
                "started_at = now() - make_interval(secs => :m * 60) WHERE job_id = :j"
            ),
            {"j": job_id, "m": minutes},
        )
    return job_id


def _plant(job_id: str, *, gpu: float, cores: float, seconds: int = 30) -> None:
    with get_engine().begin() as c:
        for i in range(seconds):
            c.execute(
                text(
                    "INSERT INTO resource_samples (ts, job_id, stage, interval_s, cpu_percent, "
                    "cpu_per_core, ram_used_mb, ram_available_mb, ram_total_mb, swap_used_mb, "
                    "gpu_util_percent, disk_read_mb_s, disk_write_mb_s) VALUES (now() - "
                    "make_interval(secs => :s), :j, 'synthetic', 1, 8, '{}', 3000, 7000, 10000, "
                    "0, :g, 3, 0)"
                ),
                {"s": i + 0.5, "j": job_id, "g": gpu},
            )
            c.execute(
                text(
                    "INSERT INTO process_samples (ts, job_id, stage, rss_mb, cpu_cores) VALUES "
                    "(now() - make_interval(secs => :s), :j, 'synthetic', 4000, :c)"
                ),
                {"s": i + 0.5, "j": job_id, "c": cores},
            )


def test_MON_3_2_the_diagnostic_fires_in_a_simulated_slow_loader_test(
    bus: Bus, runner: Runner
) -> None:
    job_id = _running_gpu_job()
    with Session(get_engine()) as session, session.begin():
        insert_events(
            session,
            [
                Event(
                    job_id=job_id,
                    stage="synthetic",
                    message="Training on the GPU",
                    metrics={"loader_workers": 0},
                )
            ],
        )
    _plant(job_id, gpu=11.0, cores=0.97)
    watchdog = Watchdog(get_engine(), enqueue=lambda _i: None, temperature=lambda: None)
    assert watchdog.check_diagnostics() == ["diag_data_loading"]
    assert watchdog.check_diagnostics() == [], "once per crossing"
    event = next(e for e in runner.events(job_id) if e["type"] == "diagnostic")
    assert event["level"] == "WARN" and "Data loading is the bottleneck" in event["message"]
    assert event["suggested_action"] == "Increase DataLoader workers (now 0)."
    assert event["metrics"]["cpu_cores"] == pytest.approx(0.97)

    live = PipelineApi(runner).client.get("/api/v1/system/diagnostics").json()
    (now,) = live["live"]
    assert now["expected"] == ["gpu"] and now["diagnosis"]["code"] == "diag_data_loading"
    assert now["observed"]["capacity_cores"] == 1 and now["observed"]["samples"] >= 20
    assert [i["fired"]["event_uid"] for i in live["items"]] == [str(event["event_uid"])]

    with get_engine().begin() as c:
        c.execute(text("DELETE FROM resource_samples WHERE job_id = :j"), {"j": job_id})
    _plant(job_id, gpu=70.0, cores=0.97)
    assert watchdog.check_diagnostics() == ["cleared"], "the GPU is busy again"


def _plant_run(
    job_id: str, *, start: datetime, epoch_s: float, epochs: int, pause_s: float
) -> None:
    """A training run: running, paused for ``pause_s`` after its first epoch, running,
    completed; one sample per second while running, and the epoch events."""
    with get_engine().begin() as c:
        c.execute(text("UPDATE jobs SET kind = 'train' WHERE id = :j"), {"j": job_id})
        c.execute(text("DELETE FROM job_transitions WHERE job_id = :j"), {"j": job_id})
        resume = start + timedelta(seconds=epoch_s + pause_s)
        end = resume + timedelta(seconds=epoch_s * (epochs - 1))
        for to_state, at in (
            ("running", start),
            ("paused", start + timedelta(seconds=epoch_s)),
            ("running", resume),
            ("completed", end),
        ):
            c.execute(
                text(
                    "INSERT INTO job_transitions (job_id, to_state, actor, at) "
                    "VALUES (:j, :s, 'test', :a)"
                ),
                {"j": job_id, "s": to_state, "a": at},
            )
        c.execute(
            text(
                "UPDATE jobs SET state = 'completed', started_at = :s, finished_at = :e "
                "WHERE id = :j"
            ),
            {"j": job_id, "s": start, "e": end},
        )
    ends = [start + timedelta(seconds=epoch_s)] + [
        resume + timedelta(seconds=epoch_s * k) for k in range(1, epochs)
    ]
    events = [
        Event(
            ts=start,
            job_id=job_id,
            stage="train",
            substage="train",
            message="Epoch 1 step 1",
            metrics={"epoch": 1},
        )
    ]
    events += [
        Event(
            ts=t,
            job_id=job_id,
            stage="train",
            substage="validate",
            message=f"Epoch {k}",
            metrics={"epoch": k},
        )
        for k, t in enumerate(ends, start=1)
    ]
    with Session(get_engine()) as session, session.begin():
        insert_events(session, events)
    with get_engine().begin() as c:
        t = start
        while t <= end:
            paused = ends[0] < t < resume
            if not paused:
                c.execute(
                    text(
                        "INSERT INTO resource_samples (ts, job_id, stage, interval_s, cpu_percent, "
                        "cpu_per_core, ram_used_mb, ram_available_mb, ram_total_mb, swap_used_mb, "
                        "gpu_util_percent, vram_used_mb, vram_total_mb, gpu_temp_c, "
                        "disk_read_mb_s, disk_write_mb_s) VALUES (:t, :j, 'train', 1, 20, '{}', "
                        "3000, 7000, 10000, 0, 60, 750, 6000, 62, 1, 0)"
                    ),
                    {"t": t, "j": job_id},
                )
            t += timedelta(seconds=1)


def test_MON_4_4_two_runs_overlay_by_time_running_and_by_epoch(bus: Bus, runner: Runner) -> None:
    a = _create(None, {"items": 1}, queue="gpu")
    b = _create(None, {"items": 1}, queue="gpu")
    _plant_run(a, start=T0, epoch_s=5, epochs=10, pause_s=30)
    _plant_run(b, start=T0 + timedelta(hours=1), epoch_s=9, epochs=4, pause_s=0)
    client = PipelineApi(runner).client

    by_time = client.get("/api/v1/system/compare", params={"a": a, "b": b}).json()
    assert by_time["align"] == "time" and by_time["epoch_unavailable"] is None
    xs = [p["x"] for p in by_time["a"]["machine"]]
    assert max(xs) <= 50.0 + 1e-6, "the 30 s pause is removed: 10 epochs of 5 s"
    assert by_time["a"]["running_s"] == 50.0

    by_epoch = client.get(
        "/api/v1/system/compare", params={"a": a, "b": b, "align": "epoch"}
    ).json()
    assert by_epoch["common_epochs"] == 4
    for side in ("a", "b"):
        assert max(p["x"] for p in by_epoch[side]["machine"]) <= 4.0 + 1e-6
    assert by_epoch["a"]["summary"]["seconds_per_epoch"] == pytest.approx((5 + 30 + 15) / 4)
    assert by_epoch["b"]["summary"]["seconds_per_epoch"] == pytest.approx(9.0)
    assert by_epoch["a"]["summary"]["gpu_mean_percent"] == 60

    pipeline = _create(None, {"items": 1}, queue="cpu")
    refused = client.get("/api/v1/system/compare", params={"a": a, "b": pipeline, "align": "epoch"})
    assert refused.status_code == 422
    assert "By epoch needs two training runs" in refused.json()["detail"]


def test_MON_2_1_telemetry_over_a_range_is_bucketed_with_its_jobs(bus: Bus, runner: Runner) -> None:
    job = _create(None, {"items": 1}, queue="gpu")
    _plant_run(job, start=T0, epoch_s=5, epochs=4, pause_s=0)
    body = (
        PipelineApi(runner)
        .client.get(
            "/api/v1/telemetry",
            params={
                "from": T0.isoformat(),
                "to": (T0 + timedelta(seconds=20)).isoformat(),
                "max_points": 10,
            },
        )
        .json()
    )
    assert body["bucket_s"] == 2.0 and len(body["points"]) <= 11
    assert body["points"][0]["gpu_util_percent"] == 60 and not body["points"][0]["minute_average"]
    assert [j["job_id"] for j in body["jobs"]] == [job]


def test_MON_2_6_the_thresholds_are_a_settings_section(bus: Bus, runner: Runner) -> None:
    client = PipelineApi(runner).client
    section = next(
        s for s in client.get("/api/v1/settings").json()["sections"] if s["name"] == "monitoring"
    )
    assert (
        section["values"]["vram_alert_percent"] == 90
        and section["values"]["ram_alert_percent"] == 85
    )
    body = {
        "section": "monitoring",
        "values": {**section["values"], "sample_interval_idle_s": 0.5},
        "version": section["version"],
    }
    refused = client.put("/api/v1/settings", json=body)
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["loc"][:2] == ["values", "sample_interval_idle_s"]
    body["values"] = {**section["values"], "vram_alert_percent": 80}
    saved = client.put("/api/v1/settings", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["section"]["values"]["vram_alert_percent"] == 80
