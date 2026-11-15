"""MON-1.3 (levels, reason and action), MON-1.5 (ETA) and the emitter's promise
never to block or raise into a job (NFR-13). No services needed."""

import json
import random
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from stratal.core.logging import clear_job_context
from stratal.mon.emitter import ListEmitter, StreamEmitter
from stratal.mon.eta import EtaEstimator
from stratal.mon.events import Event, oom_event
from stratal.runtime.context import JobCancelled, JobContext


@pytest.mark.parametrize("level", ["WARN", "ERROR"])
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"reason": "why"},
        {"suggested_action": "do this"},
        {"reason": "", "suggested_action": ""},
    ],
)
def test_MON_1_3_warn_or_error_without_reason_and_action_is_rejected(
    level: str, extra: dict[str, str]
) -> None:
    with pytest.raises(ValidationError, match=r"MON-1\.3"):
        Event.model_validate({"message": "something is wrong", "level": level, **extra})


def test_MON_1_3_every_level_is_accepted_when_complete() -> None:
    for level in ("DEBUG", "INFO"):
        assert Event.model_validate({"message": "fine", "level": level}).level == level
    for level in ("WARN", "ERROR"):
        event = Event.model_validate(
            {"message": "bad", "level": level, "reason": "why", "suggested_action": "do this"}
        )
        assert (event.reason, event.suggested_action) == ("why", "do this")
    with pytest.raises(ValidationError):
        Event.model_validate({"message": "x", "level": "FATAL"})


def test_MON_1_3_oom_event_carries_its_suggested_action() -> None:
    event = oom_event(job_id="job_0142", stage="label", batch_size=32)

    assert event.level == "WARN"
    assert event.suggested_action == "reduce batch size from 32 → 16"
    assert event.reason
    assert event.metrics == {"batch_size": 32, "suggested_batch_size": 16}
    assert oom_event(job_id="job_0142", stage="label", batch_size=1).level == "ERROR"


def test_MON_1_3_job_context_rejects_an_unexplained_warning() -> None:
    ctx = JobContext(job_id="job_0001", attempt=1, stage="label", emitter=ListEmitter())

    with pytest.raises(ValidationError):
        ctx.emit(stage="label", level="WARN", message="slow batch")


def _steady_run(total: int, seconds_per_item: float, jitter: float, seed: int) -> list[float]:
    """Times at which each step of a steady stage finishes, with per-step noise."""
    rng = random.Random(seed)
    now, times = 0.0, [0.0]
    for _ in range(total):
        now += seconds_per_item * (1 + rng.uniform(-jitter, jitter))
        times.append(now)
    return times


@pytest.mark.parametrize("seed", range(5))
def test_MON_1_5_eta_within_20_percent_once_10_percent_complete(seed: int) -> None:
    total = 400
    times = _steady_run(total, seconds_per_item=0.5, jitter=0.4, seed=seed)
    estimator = EtaEstimator()

    for step in range(total + 1):
        estimate = estimator.update(times[step], step, total)
        if step < total * 0.10 or step == total:
            continue
        assert estimate is not None and estimate.eta_seconds is not None
        truth = times[total] - times[step]
        # ±20% of the true remaining time; one item's worth of slack near the end.
        assert abs(estimate.eta_seconds - truth) <= 0.20 * truth + 0.5, (step, estimate, truth)


def test_MON_1_5_eta_follows_a_change_of_pace() -> None:
    estimator = EtaEstimator(window=10)
    now = 0.0
    estimate = None
    for step in range(1, 51):  # 1 item/s
        now += 1.0
        estimator.update(now, step, 200)
    for step in range(51, 101):  # then 4 items/s
        now += 0.25
        estimate = estimator.update(now, step, 200)
    assert estimate is not None
    assert estimate.throughput == pytest.approx(4.0)  # pyright: ignore[reportUnknownMemberType]
    assert estimate.eta_seconds == pytest.approx(25.0)  # pyright: ignore[reportUnknownMemberType]


def test_MON_1_5_progress_events_carry_throughput_and_eta() -> None:
    clock = iter(float(n) for n in range(100))
    emitter = ListEmitter()
    ctx = JobContext(
        job_id="job_0001", attempt=1, stage="label", emitter=emitter, clock=lambda: next(clock)
    )

    for step in range(1, 6):
        ctx.emit(stage="label", message=f"batch {step}/10", step=step, total=10)

    last = emitter.events[-1]
    assert last.type == "stage_progress"
    assert last.metrics == {"items_per_s": 1.0, "eta_s": 5.0}
    assert emitter.events[0].metrics is None  # no rate from a single reading


def test_MON_1_1_events_carry_job_stage_version_step_and_timestamp() -> None:
    emitter = ListEmitter()
    ctx = JobContext(job_id="job_0142", attempt=2, stage="label", emitter=emitter)
    try:
        ctx.set_version("ds_7f3a9c21b04e")
        ctx.event("stage_started", "Stage label started")
        ctx.emit(
            stage="label", substage="caption_batch", message="batch 42/313", step=42, total=313
        )
    finally:
        clear_job_context()  # set_version binds the ID to the logs; do not leak it

    started, progress = emitter.events
    assert (started.type, started.job_id, started.stage) == ("stage_started", "job_0142", "label")
    assert progress.version_id == "ds_7f3a9c21b04e"
    assert (progress.step, progress.total, progress.attempt) == (42, 313, 2)
    assert progress.ts.tzinfo is not None
    assert started.event_uid != progress.event_uid


def test_RUN_1_2_job_context_stops_at_a_safe_point_when_asked() -> None:
    stop = False
    ctx = JobContext(
        job_id="job_0001", attempt=1, stage="s", emitter=ListEmitter(), should_stop=lambda: stop
    )
    ctx.raise_if_cancelled()
    stop = True
    with pytest.raises(JobCancelled):
        ctx.raise_if_cancelled()


def test_NFR_13_emitting_never_blocks_or_raises_when_redis_is_down(tmp_path: Path) -> None:
    spool = tmp_path / "jobs" / "job_0001" / "events.jsonl"
    emitter = StreamEmitter(
        redis_url="redis://127.0.0.1:1/0", spool_path=spool, queue_size=5, retry_s=0.05
    )

    started = time.monotonic()
    for n in range(50):
        emitter.emit(Event(job_id="job_0001", stage="s", message=f"event {n}"))
    emit_seconds = time.monotonic() - started
    emitter.close(timeout_s=1.0)

    assert emit_seconds < 1.0, "emitting must not wait for Redis"
    lines = [json.loads(line) for line in spool.read_text(encoding="utf-8").splitlines()]
    assert [line["message"] for line in lines] == [f"event {n}" for n in range(50)]
    assert emitter.dropped > 0, "the queue is bounded; the spool file keeps what it drops"
