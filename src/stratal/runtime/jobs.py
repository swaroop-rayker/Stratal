"""Creating, cancelling and retrying jobs, and the shape the API returns (API.md §3.3).

Functions here change rows inside the caller's transaction. Anything that must
happen after the commit (enqueueing a task, raising a control flag, notifying
live viewers) is returned to the caller or done through :func:`after_commit`.
"""

import contextlib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from stratal.core.bus import LIVE_GLOBAL, Bus, live_channel
from stratal.core.errors import FieldProblem, NotFound, SourceNotFound, ValidationFailed
from stratal.data import registry
from stratal.db.models import Job, JobStage
from stratal.mon.events import utc_now
from stratal.runtime import stages as stage_registry
from stratal.runtime.states import TERMINAL, check_transition, record_creation, transition

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class FolderSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["folder"] = "folder"
    path: str = Field(description="Folder as the workers see it, e.g. /sources/coco10k.")


class PipelineRequest(BaseModel):
    """``POST /jobs`` with ``kind: pipeline``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pipeline"]
    dataset: str = Field(description="Dataset slug; created if it does not exist.")
    source: FolderSource
    stages: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: dict[str, dict[str, Any]](),
        description="Per-stage overrides of the default config.",
    )
    until_stage: str | None = Field(default=None, description="Stop after this stage.")


@dataclass(frozen=True)
class Enqueue:
    """A task to send once the transaction that decided it has committed."""

    job_id: str
    idx: int
    queue: str


def config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def next_job_id(session: Session) -> str:
    number = session.execute(text("SELECT nextval('job_id_seq')")).scalar_one()
    return f"job_{int(number):04d}"


def create_job_from_plan(
    session: Session,
    *,
    kind: str,
    spec: dict[str, Any],
    plan: list[tuple[str, dict[str, Any]]],
    dataset_id: int | None = None,
) -> tuple[Job, Enqueue]:
    """Insert a queued job with one stage per ``(name, config)`` in ``plan``."""
    job = Job(
        id=next_job_id(session),
        kind=kind,
        state="queued",
        spec=spec,
        dataset_id=dataset_id,
        attempt=0,
        current_stage_idx=0,
    )
    session.add(job)
    session.flush()
    first_queue = "cpu"
    for idx, (name, config) in enumerate(plan):
        definition = stage_registry.get_stage(name)
        if definition is None:
            raise ValidationFailed(f"Unknown stage {name!r}.")
        if idx == 0:
            first_queue = definition.queue
        session.add(
            JobStage(
                job_id=job.id,
                idx=idx,
                name=name,
                queue=definition.queue,
                status="queued" if idx == 0 else "pending",
                config=config,
                config_hash=config_hash(config),
                cache_hit=False,
            )
        )
    session.flush()
    record_creation(session, job)
    return job, Enqueue(job.id, 0, first_queue)


def _field_problems(stage: str, exc: ValidationError) -> list[FieldProblem]:
    return [
        FieldProblem(
            loc=["stages", stage, *error["loc"]],
            msg=error["msg"],
            code=error["type"],
        )
        for error in exc.errors()
    ]


def create_pipeline_job(session: Session, request: PipelineRequest) -> tuple[Job, Enqueue]:
    """Validate a pipeline request, resolve every stage's config and queue the job."""
    if not _SLUG.match(request.dataset):
        raise ValidationFailed(
            f"Dataset name {request.dataset!r} is not a valid slug.",
            suggested_action="Use 2 to 63 lowercase letters, digits, '-' or '_'.",
        )
    available = stage_registry.registered()
    names = [name for name in stage_registry.PIPELINE_ORDER if name in available]
    unknown = sorted(set(request.stages) - set(names))
    if unknown:
        raise ValidationFailed(
            f"Unknown or not yet available stage(s): {', '.join(unknown)}.",
            suggested_action=f"Stages available now: {', '.join(names)}.",
        )
    if request.until_stage is not None:
        if request.until_stage not in names:
            raise ValidationFailed(
                f"until_stage {request.until_stage!r} is not an available stage.",
                suggested_action=f"Stages available now: {', '.join(names)}.",
            )
        names = names[: names.index(request.until_stage) + 1]
    if not Path(request.source.path).is_dir():
        raise SourceNotFound(
            f"Source folder {request.source.path} does not exist or is not a folder.",
            suggested_action="Put the folder under the mounted sources directory "
            "(/sources in the containers) and use that path.",
        )

    plan: list[tuple[str, dict[str, Any]]] = []
    problems: list[FieldProblem] = []
    for name in names:
        try:
            model = available[name].config_model.model_validate(request.stages.get(name, {}))
        except ValidationError as exc:
            problems.extend(_field_problems(name, exc))
            continue
        plan.append((name, model.model_dump(mode="json")))
    if problems:
        raise ValidationFailed(
            "A stage config did not pass validation.",
            suggested_action="Fix the fields listed in 'errors'.",
            errors=problems,
        )

    dataset = registry.ensure_dataset(
        session, slug=request.dataset, modality="image", source_uri=request.source.path
    )
    spec = request.model_dump(mode="json")
    spec["stages"] = dict(plan)
    return create_job_from_plan(
        session, kind="pipeline", spec=spec, plan=plan, dataset_id=dataset.id
    )


def get_job(session: Session, job_id: str, *, lock: bool = False) -> Job:
    job = session.get(Job, job_id, with_for_update=lock)
    if job is None:
        raise NotFound(f"No job {job_id!r}.")
    return job


def stages_of(session: Session, job_id: str) -> list[JobStage]:
    query = select(JobStage).where(JobStage.job_id == job_id).order_by(JobStage.idx)
    return list(session.scalars(query))


def cancel_unfinished_stages(session: Session, job_id: str) -> None:
    for stage in stages_of(session, job_id):
        if stage.status in ("pending", "queued", "running"):
            stage.status = "cancelled"
            stage.finished_at = stage.finished_at or utc_now()


def request_cancel(session: Session, job_id: str) -> tuple[Job, bool]:
    """Cancel a job. Returns the job and whether a running child must be told.

    A job that is not running is cancelled at once. A running one gets
    ``desired_state = cancelled`` and stops at its next safe point; the
    supervisor makes the transition (ARCHITECTURE.md §4.3).
    """
    job = get_job(session, job_id, lock=True)
    check_transition(job.id, job.state, "cancelled")
    if job.state == "running":
        job.desired_state = "cancelled"
        return job, True
    transition(session, job, "cancelled", reason="cancelled by the user", actor="user")
    cancel_unfinished_stages(session, job_id)
    return job, False


def request_retry(session: Session, job_id: str) -> tuple[Job, Enqueue]:
    """Requeue a failed job from its first unfinished stage; finished stages are kept."""
    job = get_job(session, job_id, lock=True)
    check_transition(job.id, job.state, "queued")
    stages = stages_of(session, job_id)
    resume = next((s for s in stages if s.status not in ("completed", "skipped")), None)
    if resume is None:
        raise ValidationFailed(f"Job {job_id} has no unfinished stage to retry.")
    for stage in stages:
        if stage.idx == resume.idx:
            stage.status = "queued"
            stage.error = None
            stage.finished_at = None
        elif stage.idx > resume.idx:
            stage.status = "pending"
    job.error = None
    job.current_stage_idx = resume.idx
    transition(session, job, "queued", reason="retry requested by the user", actor="user")
    return job, Enqueue(job.id, resume.idx, resume.queue)


def after_commit(bus: Bus, job_id: str) -> None:
    """Tell live viewers that a job or its events changed. Never raises (NFR-13)."""
    with contextlib.suppress(Exception):
        for kind in ("event", "job"):
            message = {"kind": kind, "job_id": job_id}
            bus.publish(live_channel(job_id), message)
            bus.publish(LIVE_GLOBAL, message)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def stage_view(stage: JobStage) -> dict[str, Any]:
    return {
        "idx": stage.idx,
        "name": stage.name,
        "queue": stage.queue,
        "status": stage.status,
        "config": stage.config,
        "input_version_id": stage.input_version_id,
        "output_version_id": stage.output_version_id,
        "cache_hit": stage.cache_hit,
        "step": stage.step,
        "total": stage.total,
        "throughput": stage.throughput,
        "eta_seconds": stage.eta_seconds,
        "activity": stage.activity,
        "metrics": stage.metrics,
        "peaks": stage.peaks,
        "started_at": _iso(stage.started_at),
        "finished_at": _iso(stage.finished_at),
        "error": stage.error,
    }


def job_view(session: Session, job: Job) -> dict[str, Any]:
    """A job with its stages, as the API returns it."""
    stages = stages_of(session, job.id)
    current = next((s.name for s in stages if s.idx == job.current_stage_idx), None)
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "desired_state": job.desired_state,
        "attempt": job.attempt,
        "dataset": job.spec.get("dataset"),
        "current_stage": current if job.state not in TERMINAL else None,
        "stages": [stage_view(stage) for stage in stages],
        "output_version_id": job.output_version_id,
        "error": job.error,
        "summary": job.summary,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }
