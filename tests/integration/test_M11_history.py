"""MON-4.5 through the API: SCR-13's History tab reads the last N runs of one stage."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine
from stratal.runtime import jobs as job_service
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
CNN = {"model": {"name": "small_cnn"}, "batch_size": 128, "epochs": 10}


def _run(
    start: datetime,
    *,
    config: dict[str, Any],
    seconds: float,
    lost_after: float | None = None,
    stage: str = "train",
    queue: str = "gpu",
    kind: str = "train",
    metrics: dict[str, Any] | None = None,
    throughput: float | None = 80.0,
    cache_hit: bool = False,
) -> str:
    """A completed one-stage job that ran ``seconds``; with ``lost_after``, its worker was lost
    then and it ran again, so it has two attempts."""
    with Session(get_engine()) as session, session.begin():
        job, _ = job_service.create_job_from_plan(
            session,
            kind="pipeline",
            spec={"dataset": "test"},
            plan=[("synthetic", {"items": 1})],
            queues={"synthetic": queue},
        )
        job_id = job.id
    end = start + timedelta(seconds=seconds)
    moves = [("running", start, "supervisor")]
    if lost_after is not None:
        moves += [
            ("queued", start + timedelta(seconds=lost_after), "watchdog"),
            ("running", start + timedelta(seconds=lost_after + 5), "supervisor"),
        ]
        end += timedelta(seconds=5)
    moves.append(("completed", end, "supervisor"))
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM job_transitions WHERE job_id = :j"), {"j": job_id})
        for state, at, actor in moves:
            c.execute(
                text(
                    "INSERT INTO job_transitions (job_id, to_state, actor, at) "
                    "VALUES (:j, :s, :a, :t)"
                ),
                {"j": job_id, "s": state, "a": actor, "t": at},
            )
        c.execute(
            text(
                "UPDATE jobs SET kind = :k, state = 'completed', started_at = :s, "
                "finished_at = :e WHERE id = :j"
            ),
            {"j": job_id, "k": kind, "s": start, "e": end},
        )
        last_start = moves[-2][1]
        c.execute(
            text(
                "UPDATE job_stages SET name = :n, queue = :q, status = 'completed', "
                "config = CAST(:c AS jsonb), metrics = CAST(:m AS jsonb), "
                "peaks = CAST(:p AS jsonb), throughput = :t, cache_hit = :h, "
                "started_at = :s, finished_at = :e WHERE job_id = :j"
            ),
            {
                "j": job_id,
                "n": stage,
                "q": queue,
                "c": _json(config),
                "m": _json(metrics or {}),
                "p": _json(
                    {
                        "vram_used_mb": 758.0,
                        "torch_max_allocated_mb": 149.0,
                        "ram_used_mb": 4200.0,
                        "gpu_temp_c": 66.0,
                        "rss_mb": 7400.0,
                    }
                ),
                "t": throughput,
                "h": cache_hit,
                "s": last_start,
                "e": end,
            },
        )
    return job_id


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


def test_MON_4_5_trends_are_shown_for_the_last_n_runs_of_a_stage(runner: Runner) -> None:
    jobs = [_run(T0 + timedelta(minutes=i), config=CNN, seconds=60) for i in range(5)]
    lost = _run(T0 + timedelta(minutes=10), config=CNN, seconds=60, lost_after=40)
    other = _run(T0 + timedelta(minutes=20), config={**CNN, "batch_size": 256}, seconds=30)
    client = PipelineApi(runner).client

    body = client.get("/api/v1/system/history", params={"n": 3}).json()
    assert (body["stage"], body["queue"], body["rate_unit"], body["time_unit"]) == (
        "train",
        "gpu",
        "samples/s",
        "s/epoch",
    )
    assert body["stages"][0] == {
        "stage": "train",
        "queue": "gpu",
        "runs": 7,
        "reused": 0,
        "last_at": body["stages"][0]["last_at"],
    }
    assert [r["job_id"] for r in body["runs"]] == [other], "the latest run's group by default"
    labels = {g["label"]: g["runs"] for g in body["groups"]}
    assert labels == {
        "small_cnn (reference) · ? · batch 128": 6,
        "small_cnn (reference) · ? · batch 256": 1,
    }

    group = next(g["key"] for g in body["groups"] if g["runs"] == 6)
    body = client.get(
        "/api/v1/system/history", params={"stage": "train", "group": group, "n": 3}
    ).json()
    runs = body["runs"]
    assert [r["job_id"] for r in runs] == [lost, jobs[4], jobs[3]], "newest first, at most n"
    first = runs[0]
    assert first["attempts"] == 2 and first["notes"] == ["resumed"]
    assert first["running_s"] == 60.0, "running time across both attempts, not the last one's 20 s"
    assert first["time"] == 6.0 and first["epochs"] == 10
    assert first["rate"] == 80.0 * 128
    assert first["peaks"]["vram_used_mb"] == 758.0 and first["peaks"]["torch_allocated_mb"] == 149.0

    every = client.get("/api/v1/system/history", params={"group": "all", "n": 100}).json()
    assert every["group"] is None and len(every["runs"]) == 7


def test_MON_4_5_reused_stages_are_left_out_unless_asked(runner: Runner) -> None:
    label = {"captioner": "florence-2-base", "caption_batch_size": 16}
    did = _run(
        T0,
        config=label,
        seconds=100,
        stage="label",
        kind="pipeline",
        metrics={"images": 900, "captions_computed": 900},
        throughput=None,
    )
    reused = _run(
        T0 + timedelta(minutes=5),
        config=label,
        seconds=2,
        stage="label",
        kind="pipeline",
        metrics={},
        throughput=None,
        cache_hit=True,
    )
    client = PipelineApi(runner).client

    body = client.get("/api/v1/system/history", params={"stage": "label"}).json()
    assert [r["job_id"] for r in body["runs"]] == [did]
    assert body["runs"][0]["rate"] == 9.0 and body["rate_unit"] == "captions/s"
    assert body["stages"][0]["reused"] == 1
    both = client.get("/api/v1/system/history", params={"stage": "label", "reused": True}).json()
    assert [r["job_id"] for r in both["runs"]] == [reused, did]
    assert both["runs"][0]["reused"] is True and both["runs"][0]["rate"] is None

    missing = client.get("/api/v1/system/history", params={"stage": "nonesuch"})
    assert missing.status_code == 404
