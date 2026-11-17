"""Manual review endpoints (docs/API.md §3.5; LABEL-1.5, DATA-4.7)."""

from collections.abc import Sequence
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from stratal.api import schemas
from stratal.api.jobs import Db
from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.models import ReviewItem, ReviewSession
from stratal.label import review as reviews
from stratal.storage.manifest import iter_rows

router = APIRouter(tags=["review"])
Json = dict[str, Any]

ROW_DETAILS = [
    "row_key",
    "sha256",
    "derived_sha256",
    "caption",
    "clip_score",
    "split_stratum",
    # M6: what SCR-05 shows beside the image (caption model, original and copy size).
    "caption_model",
    "caption_model_revision",
    "width",
    "height",
    "size_bytes",
    "derived_width",
    "derived_height",
]


class CreateReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_size: int = Field(ge=1, le=reviews.MAX_SAMPLE, description="Rows to sample.")
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2**63 - 1,
        description="Seed for the sample; drawn by the server and recorded when absent.",
    )


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
    note: str | None = Field(default=None, max_length=2000)


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _details(session: Session, version_id: str, keys: set[str]) -> dict[str, Json]:
    """Caption, score and image hashes of the sampled rows, read from the manifest."""
    _, manifest = registry.manifest_for(session, get_settings().data_dir, version_id)
    found: dict[str, Json] = {}
    for row in iter_rows(manifest, columns=ROW_DETAILS):
        if row["row_key"] in keys:
            found[str(row["row_key"])] = row
    return found


def _item(item: ReviewItem, details: Json | None = None) -> Json:
    view: Json = {
        "position": item.position,
        "row_key": item.row_key,
        "split": item.split,
        "decision": item.decision,
        "note": item.note,
        "decided_at": _iso(item.decided_at),
    }
    if details is not None:
        view["row"] = {k: v for k, v in details.items() if k != "row_key"}
    return view


def _session(review: ReviewSession) -> Json:
    return {
        "id": review.id,
        "version_id": review.version_id,
        "sample_size": review.sample_size,
        "seed": review.seed,
        "status": review.status,
        "created_at": _iso(review.created_at),
        "closed_at": _iso(review.closed_at),
    }


def _full(session: Session, review: ReviewSession, items: Sequence[ReviewItem]) -> Json:
    details = _details(session, review.version_id, {item.row_key for item in items})
    return {
        **_session(review),
        "progress": reviews.progress(items),
        "items": [_item(item, details.get(item.row_key)) for item in items],
    }


@router.post("/versions/{version_id}/reviews", status_code=201, response_model=schemas.Review)
def create_review(version_id: str, body: CreateReview, session: Db) -> Json:
    with session.begin():
        review = reviews.create_review(
            session,
            get_settings().data_dir,
            version_id,
            sample_size=body.sample_size,
            seed=body.seed,
        )
        review_id = review.id
    review = reviews.get_review(session, review_id)
    return _full(session, review, reviews.items_of(session, review_id))


@router.get("/reviews/{review_id}", response_model=schemas.Review)
def get_review(review_id: str, session: Db) -> Json:
    review = reviews.get_review(session, review_id)
    return _full(session, review, reviews.items_of(session, review_id))


@router.put("/reviews/{review_id}/items/{position}", response_model=schemas.ReviewItem)
def decide(review_id: str, position: int, body: Decision, session: Db) -> Json:
    with session.begin():
        item = reviews.decide(session, review_id, position, decision=body.decision, note=body.note)
        view = _item(item)
    return view


@router.post("/reviews/{review_id}/close", response_model=schemas.ReviewClosed)
def close(review_id: str, session: Db) -> Json:
    with session.begin():
        review = reviews.close_review(session, review_id)
        items = reviews.items_of(session, review_id)
        view = {
            **_session(review),
            "progress": reviews.progress(items),
            **reviews.acceptance_summary(items),
        }
    return view
