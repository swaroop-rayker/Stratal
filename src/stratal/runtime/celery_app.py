# pyright: basic
"""Celery wiring (ADR-0003). Celery ships without type hints, so this one module is
checked in pyright's basic mode.

Celery only delivers "run this stage" messages. Job state lives in PostgreSQL,
and the task body is :func:`stratal.runtime.supervisor.run_stage`.

    celery -A stratal.runtime.celery_app worker -Q cpu -c 2 -n cpu@%h

Scheduled maintenance (NFR-12) runs in the ``beat`` service: one worker on its own
``maintenance`` queue with the scheduler embedded, so it never waits behind a
pipeline stage on ``worker-cpu``:

    celery -A stratal.runtime.celery_app worker -Q maintenance -P solo -B -n beat@%h
"""

from celery import Celery
from celery.signals import setup_logging

from stratal.core.logging import configure_logging
from stratal.core.settings import get_settings
from stratal.runtime.jobs import Enqueue

TASK_NAME = "stratal.run_stage"
DOWNSAMPLE_TASK = "stratal.downsample_telemetry"

app = Celery("stratal", broker=get_settings().redis_url)
app.conf.update(
    task_ignore_result=True,
    # Acknowledge on receipt. A redelivery after a long run must not start a second
    # copy; a lost job is recovered by the watchdog from heartbeats, not by Celery.
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_default_queue="cpu",
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "downsample-telemetry": {
            "task": DOWNSAMPLE_TASK,
            "schedule": get_settings().downsample_every_s,
            "options": {"queue": "maintenance"},
        }
    },
    # The schedule is in code; beat's own state file would only record last runs.
    beat_schedule_filename="/tmp/celerybeat-schedule",
)


@setup_logging.connect
def _json_logging(**_kwargs) -> None:
    configure_logging(get_settings().log_level)


def enqueue_stage(item: Enqueue) -> None:
    """Ask a worker on ``item.queue`` to supervise this stage."""
    app.send_task(TASK_NAME, args=[item.job_id, item.idx], queue=item.queue)


@app.task(name=TASK_NAME)
def run_stage_task(job_id: str, idx: int) -> str:
    from stratal.runtime.supervisor import run_stage

    return run_stage(job_id, idx, enqueue=enqueue_stage)


@app.task(name=DOWNSAMPLE_TASK)
def downsample_telemetry_task() -> dict[str, int]:
    """NFR-12: average raw telemetry older than the retention into 1-minute rows."""
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    from stratal.db.engine import get_engine
    from stratal.mon.retention import downsample

    with Session(get_engine()) as session:
        result = downsample(
            session, now=datetime.now(UTC), keep_days=get_settings().telemetry_keep_days
        )
    return {
        "resource_rows": result.resource_rows,
        "process_rows": result.process_rows,
        "minutes": result.minutes,
    }
