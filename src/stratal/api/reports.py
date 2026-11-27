"""Evaluation report endpoints (docs/API.md §3.6; EVAL-1.1)."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from stratal.api import schemas
from stratal.api.jobs import Db, Queue, Redis
from stratal.core.errors import NotFound
from stratal.data import registry
from stratal.db.models import Dataset, DatasetVersion, EvalReport
from stratal.runtime import jobs as job_service

router = APIRouter(tags=["evaluation"])
Json = dict[str, Any]


class CreateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["phase1_dataset", "retrieval"] = "phase1_dataset"
    question_set: str | None = Field(
        default=None, description="retrieval: the frozen question set (default: the current one)."
    )
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


@router.post("/versions/{version_id}/reports", status_code=202, response_model=schemas.Job)
def create_report(
    version_id: str, body: CreateReport, session: Db, enqueue: Queue, bus: Redis
) -> Json:
    """Build a report in a one-stage job; the job's ``evaluate`` stage stores it."""
    with session.begin():
        job, first = job_service.create_report_job(
            session, version_id, body.model_dump(mode="json", exclude_none=True)
        )
        view = job_service.job_view(session, job)
    enqueue(first)
    job_service.after_commit(bus, view["id"])
    return view


@router.get("/versions/{version_id}/reports/latest", response_model=schemas.Report)
def latest_report(
    version_id: str,
    session: Db,
    kind: Literal["phase1_dataset", "retrieval"] = "phase1_dataset",
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


class RetrievalSummary(BaseModel):
    report_id: int
    version_id: str
    question_set: str
    question_set_sha256: str
    chunk_tokens: int | None
    overlap_tokens: int | None
    chunks: int | None
    hits: dict[str, Any]
    created_at: str


class RetrievalSummaries(BaseModel):
    items: list[RetrievalSummary]


@router.get("/datasets/{slug}/reports", response_model=RetrievalSummaries)
def dataset_reports(slug: str, session: Db, kind: Literal["retrieval"] = "retrieval") -> Json:
    """The latest retrieval report of each index version of a corpus, newest first, so
    SCR-06 can compare chunk sizes on one question set (LLM-2.2; SCREENS.md v5)."""
    dataset = session.scalars(select(Dataset).where(Dataset.slug == slug)).first()
    if dataset is None:
        raise NotFound(f"Dataset {slug!r} does not exist.")
    rows = session.scalars(
        select(EvalReport)
        .join(DatasetVersion, DatasetVersion.version_id == EvalReport.version_id)
        .where(DatasetVersion.dataset_id == dataset.id, EvalReport.kind == kind)
        .order_by(EvalReport.created_at.desc())
    ).all()
    latest: dict[str, EvalReport] = {}
    for row in rows:
        latest.setdefault(row.version_id, row)
    items: list[Json] = []
    for row in latest.values():
        body: Json = row.report
        index: Json = body.get("index") or {}
        items.append(
            {
                "report_id": row.id,
                "version_id": row.version_id,
                "question_set": body["question_set"]["name"],
                "question_set_sha256": body["question_set"]["sha256"],
                "chunk_tokens": index.get("chunk_tokens"),
                "overlap_tokens": index.get("overlap_tokens"),
                "chunks": index.get("chunks"),
                "hits": body["hits"],
                "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
            }
        )
    return {"items": items}


@router.get("/reports/{report_id}", response_model=schemas.Report)
def get_report(report_id: int, session: Db) -> Json:
    row = session.get(EvalReport, report_id)
    if row is None:
        raise NotFound(f"No report {report_id}.")
    return report_view(row)
