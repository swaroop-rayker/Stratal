"""Evaluation report endpoints (docs/API.md §3.6; EVAL-1.1)."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from stratal.api.jobs import Db, Queue, Redis
from stratal.core.errors import NotFound
from stratal.data import registry
from stratal.db.models import EvalReport
from stratal.runtime import jobs as job_service

router = APIRouter(tags=["evaluation"])
Json = dict[str, Any]


class CreateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["phase1_dataset"] = "phase1_dataset"
    review_id: str | None = Field(
        default=None,
        description="The review to include; the version's newest closed review when absent.",
    )


def report_view(row: EvalReport) -> Json:
    return {
        "id": row.id,
        "version_id": row.version_id,
        "kind": row.kind,
        "review_session_id": row.review_session_id,
        "job_id": row.job_id,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
        "report": row.report,
    }


@router.post("/versions/{version_id}/reports", status_code=202)
def create_report(
    version_id: str, body: CreateReport, session: Db, enqueue: Queue, bus: Redis
) -> Json:
    """Build a report in a one-stage job; the job's ``evaluate`` stage stores it."""
    with session.begin():
        job, first = job_service.create_report_job(
            session, version_id, body.model_dump(mode="json")
        )
        view = job_service.job_view(session, job)
    enqueue(first)
    job_service.after_commit(bus, view["id"])
    return view


@router.get("/versions/{version_id}/reports/latest")
def latest_report(
    version_id: str, session: Db, kind: Literal["phase1_dataset"] = "phase1_dataset"
) -> Json:
    registry.get_version(session, version_id)
    query = (
        select(EvalReport)
        .where(EvalReport.version_id == version_id, EvalReport.kind == kind)
        .order_by(EvalReport.created_at.desc(), EvalReport.id.desc())
    )
    row = session.scalars(query).first()
    if row is None:
        raise NotFound(
            f"Version {version_id} has no {kind} report yet.",
            suggested_action=f"Build one with POST /versions/{version_id}/reports.",
        )
    return report_view(row)


@router.get("/reports/{report_id}")
def get_report(report_id: int, session: Db) -> Json:
    row = session.get(EvalReport, report_id)
    if row is None:
        raise NotFound(f"No report {report_id}.")
    return report_view(row)
