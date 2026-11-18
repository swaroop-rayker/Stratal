"""The model registry and retention (docs/API.md §3.9; REG-1.1, REG-1.2, REG-2; ADR-0015).

Lineages and versions; pins; disk use measured on disk; re-train; the retention
dry run and run. ``/predict`` arrives with REG-1.4.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from stratal.api import schemas
from stratal.api.jobs import Db, Limit, Queue, Redis
from stratal.core.bus import Bus
from stratal.core.settings import get_settings
from stratal.registry import retention, store
from stratal.runtime import jobs as job_service

router = APIRouter(tags=["models"])
Json = dict[str, Any]

VersionState = Literal["base", "partial", "trained"]


def _page(items: list[Json], cursor: str | None, limit: int) -> Json:
    start = int(cursor) if cursor and cursor.isdigit() else 0
    end = start + limit
    return {"items": items[start:end], "next_cursor": str(end) if end < len(items) else None}


def _use(session: Db, bus: Bus) -> retention.InUse:
    loaded, ok = retention.loaded_from(bus.get_inference_loaded)
    return retention.in_use(session, loaded, redis_ok=ok)


def _with_decisions(rows: list[Json], found: dict[str, tuple[str, str]]) -> list[Json]:
    for row in rows:
        action, reason = found.get(row["id"], ("none", "weights already deleted"))
        row["retention_action"] = action
        row["retention_reason"] = reason
    return rows


def _retention_info(session: Db, lineage_id: str) -> Json:
    keep, source = retention.effective_keep(retention.read_values(session), lineage_id)
    return {
        "keep_intermediate": "all" if keep is None else keep,
        "source": source,
        "last_run": retention.last_run(session, lineage_id),
    }


@router.get("/models", response_model=schemas.ModelLineageList)
def list_models(
    session: Db,
    state: Annotated[
        VersionState | None, Query(description="Lineages with a version in this state.")
    ] = None,
    task: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Name substring.")] = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Json:
    """Model lineages, most recently updated first (REG-1.2)."""
    return _page(store.list_lineages(session, state=state, task=task, q=q), cursor, limit)


@router.get("/models/{lineage_id}", response_model=schemas.ModelLineageDetail)
def get_model(
    lineage_id: str,
    session: Db,
    bus: Redis,
    state: VersionState | None = None,
    weights: Annotated[
        Literal["present", "deleted"] | None,
        Query(description="'deleted' lists versions whose weights retention removed."),
    ] = None,
) -> Json:
    """One lineage and its versions, newest first, each with what retention does to it."""
    detail = store.lineage_detail(session, lineage_id, state=state, weights=weights)
    _with_decisions(
        detail["versions"], retention.decisions(session, lineage_id, _use(session, bus))
    )
    detail["retention"] = _retention_info(session, lineage_id)
    return detail


@router.get("/models/{lineage_id}/disk", response_model=schemas.DiskReport)
def lineage_disk(lineage_id: str, session: Db) -> Json:
    """Disk use measured on disk, by part, against the registry's records (REG-2.11)."""
    return retention.disk_report(session, get_settings().data_dir, lineage_id)


def _version(session: Db, bus: Bus, version_id: str) -> Json:
    detail = store.version_detail(session, version_id)
    found = retention.decisions(session, detail["lineage_id"], _use(session, bus))
    return _with_decisions([detail], found)[0]


@router.get("/model-versions/{version_id}", response_model=schemas.ModelVersionDetail)
def get_model_version(version_id: str, session: Db, bus: Redis) -> Json:
    """Everything that made a version: config, dataset version, job, parent, metrics and
    the per-epoch history (REG-1.1); and what retention does to it."""
    return _version(session, bus, version_id)


@router.post("/model-versions/{version_id}/pin", response_model=schemas.ModelVersionDetail)
def pin_version(version_id: str, session: Db, bus: Redis) -> Json:
    """Exempt a version from retention (REG-2.5). ``409`` unless it is trained with its
    weights on disk."""
    with session.begin():
        store.pin(session, version_id)
    return _version(session, bus, version_id)


@router.delete("/model-versions/{version_id}/pin", response_model=schemas.ModelVersionDetail)
def unpin_version(version_id: str, session: Db, bus: Redis) -> Json:
    """Unpin. Nothing is deleted now: ``retention_action`` says whether the next cleanup
    will delete it."""
    with session.begin():
        store.unpin(session, version_id)
    return _version(session, bus, version_id)


@router.post("/model-versions/{version_id}/retrain", status_code=202, response_model=schemas.Job)
def retrain_version(version_id: str, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Start a run with the version's exact config and dataset version (REG-2.7). Its
    metrics match within NFR-4's tolerance, not exactly."""
    with session.begin():
        job, first = job_service.create_retrain_job(session, version_id)
        view = job_service.job_view(session, job)
    enqueue(first)
    job_service.after_commit(bus, job.id)
    return view


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lineage_id: str | None = Field(default=None, description="One lineage; null for all.")
    values: retention.RetentionValues | None = Field(
        default=None, description="Settings to plan with instead of the saved ones."
    )


@router.post("/retention/plan", tags=["retention"], response_model=schemas.RetentionPlan)
def retention_plan(body: PlanRequest, session: Db, bus: Redis) -> Json:
    """The dry run (REG-2.9): what retention would delete, keep and refuse. Changes nothing."""
    if body.lineage_id is not None:
        store.get_lineage(session, body.lineage_id)
    return retention.build_plan(
        session,
        use=_use(session, bus),
        lineage_ids=[body.lineage_id] if body.lineage_id else None,
        values=body.values,
    )


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lineage_id: str | None = Field(default=None, description="One lineage; null for all.")
    dry_run: bool = Field(default=False, description="Log what would go; change nothing.")


@router.post("/retention/run", tags=["retention"], status_code=202, response_model=schemas.Job)
def retention_run(body: RunRequest, session: Db, enqueue: Queue, bus: Redis) -> Json:
    """Queue a retention job (REG-2.9). It applies the saved settings, as after a run."""
    with session.begin():
        if body.lineage_id is not None:
            store.get_lineage(session, body.lineage_id)
        job, first = job_service.create_retention_job(
            session,
            lineage_ids=[body.lineage_id] if body.lineage_id else None,
            dry_run=body.dry_run,
            reason="requested",
        )
        view = job_service.job_view(session, job)
    enqueue(first)
    job_service.after_commit(bus, job.id)
    return view
