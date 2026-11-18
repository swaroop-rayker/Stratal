"""The model registry (docs/API.md §3.9; REG-1.1, REG-1.2, ADR-0015).

Lineages and their versions, read-only in this step. Pins, retention and
``/predict`` arrive with REG-2 and REG-1.4.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from stratal.api import schemas
from stratal.api.jobs import Db, Limit
from stratal.registry import store

router = APIRouter(tags=["models"])
Json = dict[str, Any]

VersionState = Literal["base", "partial", "trained"]


def _page(items: list[Json], cursor: str | None, limit: int) -> Json:
    start = int(cursor) if cursor and cursor.isdigit() else 0
    end = start + limit
    return {"items": items[start:end], "next_cursor": str(end) if end < len(items) else None}


@router.get("/models", response_model=schemas.ModelLineageList)
def list_models(
    session: Db,
    state: Annotated[
        VersionState | None, Query(description="The state of the lineage's latest version.")
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
    state: VersionState | None = None,
    weights: Annotated[
        Literal["present", "deleted"] | None,
        Query(description="'deleted' lists versions whose weights retention removed."),
    ] = None,
) -> Json:
    """One lineage and its versions, newest first, filtered by state and weights."""
    return store.lineage_detail(session, lineage_id, state=state, weights=weights)


@router.get("/model-versions/{version_id}", response_model=schemas.ModelVersionDetail)
def get_model_version(version_id: str, session: Db) -> Json:
    """Everything that made a version: config, dataset version, job, parent, metrics and
    the per-epoch history (REG-1.1)."""
    return store.version_detail(session, version_id)
