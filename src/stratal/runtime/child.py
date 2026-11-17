"""The child process that runs one stage of one job, then exits (ADR-0003).

    python -m stratal.runtime.child --job job_0142 --stage 0 --attempt 1

Started fresh by the supervisor for every stage, so when it exits the operating
system takes back all of its RAM and VRAM. It reports through events, writes
the stage's result or error to ``job_stages``, and tells the supervisor the
rest with its exit code.
"""

import argparse
import contextlib
import json
import os
import sys
import threading
import time
from typing import IO, Any

from sqlalchemy.orm import Session

from stratal.core.bus import Bus
from stratal.core.errors import StratalError
from stratal.core.logging import bind_job_context, configure_logging, get_logger
from stratal.core.settings import Settings, get_settings
from stratal.db.engine import get_engine
from stratal.db.models import Job, JobStage
from stratal.mon import probes
from stratal.mon.emitter import KIND_PROCESS_SAMPLE, Emitter, NullEmitter, StreamEmitter
from stratal.mon.events import MetricValue, utc_now
from stratal.runtime.context import JobCancelled, JobContext
from stratal.runtime.stages import StageResult, StageRun, get_stage

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 3

log = get_logger(__name__)


class _Tee:
    """Writes log lines to the container's output and to the job's own log file."""

    def __init__(self, *streams: IO[str]) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            with contextlib.suppress(OSError, ValueError):
                stream.write(text)
                stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            with contextlib.suppress(OSError, ValueError):
                stream.flush()


class ProcessReporter(threading.Thread):
    """Reports this process's memory, and PyTorch's allocator once CUDA is in use.

    Only the owning process can read its allocator (SPEC CL-11). The stage code
    does not know this thread exists.
    """

    def __init__(self, emitter: Emitter, *, job_id: str, stage: str, interval_s: float) -> None:
        super().__init__(name="process-reporter", daemon=True)
        self._emitter = emitter
        self._job_id = job_id
        self._stage = stage
        self._interval_s = interval_s
        # Not "_stop": threading.Thread uses that name itself.
        self._halt = threading.Event()

    def sample(self) -> dict[str, Any]:
        torch = probes.torch_stats()
        return {
            "ts": utc_now().isoformat(),
            "job_id": self._job_id,
            "stage": self._stage,
            "rss_mb": round(probes.process_rss_mb(os.getpid()), 2),
            "torch_allocated_mb": round(torch[0], 2) if torch else None,
            "torch_reserved_mb": round(torch[1], 2) if torch else None,
            "torch_max_allocated_mb": round(torch[2], 2) if torch else None,
        }

    def report(self) -> None:
        with contextlib.suppress(Exception):
            self._emitter.send(KIND_PROCESS_SAMPLE, json.dumps(self.sample()))

    def run(self) -> None:
        while True:
            self.report()
            if self._halt.wait(self._interval_s):
                break
        self.report()

    def stop(self) -> None:
        self._halt.set()
        self.join(2.0)


class _StopFlag:
    """Asks Redis whether the job was told to stop, at most twice a second."""

    def __init__(self, redis_url: str, job_id: str) -> None:
        self._redis_url = redis_url
        self._job_id = job_id
        self._bus: Bus | None = None
        self._checked_at = 0.0
        self._stop = False

    def __call__(self) -> bool:
        now = time.monotonic()
        if self._stop or now - self._checked_at < 0.5:
            return self._stop
        self._checked_at = now
        try:
            if self._bus is None:
                self._bus = Bus(self._redis_url, timeout_s=1.0)
            self._stop = self._bus.get_control(self._job_id) == "cancel"
        except Exception:
            self._bus = None  # Redis is down: keep working, the supervisor can still kill us
        return self._stop


def _record(job_id: str, idx: int, **values: Any) -> None:
    with Session(get_engine()) as session, session.begin():
        stage = session.get(JobStage, (job_id, idx))
        if stage is not None:
            for name, value in values.items():
                setattr(stage, name, value)


def _fail(ctx: JobContext, job_id: str, idx: int, error: dict[str, Any]) -> int:
    with contextlib.suppress(Exception):
        _record(job_id, idx, error=error)
    ctx.event(
        "stage_failed",
        f"Stage {ctx.stage} failed: {error['reason']}",
        level="ERROR",
        reason=str(error["reason"]),
        suggested_action=str(error["suggested_action"]),
        metrics={"code": str(error["code"])},
    )
    return EXIT_FAILED


def run(job_id: str, idx: int, attempt: int, settings: Settings) -> int:
    job_dir = settings.data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_file = (job_dir / "job.log").open("a", encoding="utf-8")
    configure_logging(settings.log_level, _Tee(sys.stdout, log_file))  # pyright: ignore[reportArgumentType]
    bind_job_context(job_id=job_id)

    with Session(get_engine()) as session:
        job = session.get(Job, job_id)
        stage = session.get(JobStage, (job_id, idx))
        if job is None or stage is None:
            log.error("stage_not_found", stage_idx=idx)
            return EXIT_FAILED
        spec, name = dict(job.spec), stage.name
        config, input_version_id = dict(stage.config), stage.input_version_id

    emitter: Emitter = (
        StreamEmitter(
            redis_url=settings.redis_url,
            spool_path=job_dir / "events.jsonl",
            maxlen=settings.event_stream_maxlen,
        )
        if settings.monitoring_enabled
        else NullEmitter()
    )
    ctx = JobContext(
        job_id=job_id,
        attempt=attempt,
        stage=name,
        emitter=emitter,
        should_stop=_StopFlag(settings.redis_url, job_id),
    )
    reporter = ProcessReporter(
        emitter, job_id=job_id, stage=name, interval_s=settings.process_sample_interval_s
    )
    if settings.monitoring_enabled:
        reporter.start()
    started = time.monotonic()
    try:
        ctx.event("stage_started", f"Stage {name} started", metrics={"idx": idx})
        definition = get_stage(name)
        if definition is None:
            raise StratalError(
                f"No stage named {name!r} is registered in this worker.",
                suggested_action="Rebuild the worker image so it matches the API's code.",
            )
        result: StageResult = definition.run(
            StageRun(
                ctx=ctx,
                spec=spec,
                config=config,
                input_version_id=input_version_id,
                data_dir=settings.data_dir,
                engine=get_engine(),
            )
        )
        if result.output_version_id is not None:
            ctx.set_version(result.output_version_id)
        _record(
            job_id,
            idx,
            output_version_id=result.output_version_id,
            cache_hit=result.cache_hit,
            metrics=dict(result.metrics) or None,
        )
        metrics: dict[str, MetricValue] = dict(result.metrics)
        metrics["seconds"] = round(time.monotonic() - started, 3)
        ctx.event("stage_completed", f"Stage {name} completed", metrics=metrics)
        return EXIT_OK
    except JobCancelled:
        ctx.event("activity", f"Stage {name} stopped at a safe point on request")
        return EXIT_CANCELLED
    except StratalError as exc:
        return _fail(
            ctx,
            job_id,
            idx,
            {
                "code": exc.code if exc.code != "internal_error" else "stage_failed",
                "reason": exc.detail,
                "suggested_action": exc.suggested_action
                or "Fix the cause described in the reason, then retry the job.",
                "detail": None,
            },
        )
    except Exception as exc:
        log.exception("stage_raised", stage=name)
        return _fail(
            ctx,
            job_id,
            idx,
            {
                "code": "stage_failed",
                "reason": f"{type(exc).__name__}: {exc}"[:1000],
                "suggested_action": f"Read the stack trace in jobs/{job_id}/job.log, "
                "fix the cause, then retry the job.",
                "detail": None,
            },
        )
    finally:
        if settings.monitoring_enabled:
            reporter.stop()
        emitter.close()
        with contextlib.suppress(OSError):
            log_file.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratal.runtime.child")
    parser.add_argument("--job", required=True)
    parser.add_argument("--stage", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args(argv)
    return run(args.job, args.stage, args.attempt, get_settings())


if __name__ == "__main__":
    sys.exit(main())
