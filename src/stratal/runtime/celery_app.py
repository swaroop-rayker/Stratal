# pyright: basic
"""Celery wiring (ADR-0003). Celery ships without type hints, so this one module is
checked in pyright's basic mode.

Celery only delivers "run this stage" messages. Job state lives in PostgreSQL,
and the task body is :func:`stratal.runtime.supervisor.run_stage`.

    celery -A stratal.runtime.celery_app worker -Q cpu -c 2 -n cpu@%h
"""

from celery import Celery
from celery.signals import setup_logging

from stratal.core.logging import configure_logging
from stratal.core.settings import get_settings
from stratal.runtime.jobs import Enqueue

TASK_NAME = "stratal.run_stage"

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
