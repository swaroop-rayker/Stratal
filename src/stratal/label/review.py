"""Manual review sessions (LABEL-1.5, DATA-4.7). The agent's plumbing.

A review samples ``sample_size`` accepted rows of one version with a recorded
seed, stores each sampled row with its split, and records an accept or reject
per row. It measures label quality and changes nothing: no manifest, split or
status is touched (DATA-4.7; DECISIONS.md, M5). Closing a session summarizes
it; the acceptance rate and its Wilson interval come from the owner's
:func:`stratal.eval.report_stats.review_acceptance` (EVAL-1.1).
"""

import dataclasses
import random
import secrets
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict, NotFound, ValidationFailed
from stratal.data import registry
from stratal.db.models import REVIEW_DECISIONS, ReviewItem, ReviewSession
from stratal.eval import report_stats
from stratal.eval.report import ReviewInput
from stratal.eval.report_stats import Acceptance, ReviewedItem
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows

MAX_SAMPLE = 1000
"""The most rows one session may sample: a review is read by a person."""
SEED_LIMIT = 2**31
"""Seeds drawn by the server are below this; a given seed may be any non-negative 63-bit int."""

AcceptanceFn = Callable[[Sequence[ReviewedItem]], Acceptance]


def sample_rows(row_keys: Sequence[str], size: int, seed: int) -> list[str]:
    """``size`` distinct keys, in review order. The same keys and seed give the same sample.

    The keys are sorted first, so the order in which a manifest happens to be
    read cannot change the sample.
    """
    return random.Random(seed).sample(sorted(row_keys), size)


def _accepted(data_dir: Path, session: Session, version_id: str) -> dict[str, str | None]:
    """``{row_key: split}`` of the version's accepted rows."""
    _, manifest = registry.manifest_for(session, data_dir, version_id)
    return {
        str(row["row_key"]): row["split"]
        for row in iter_rows(manifest, columns=["row_key", "status", "split"])
        if row["status"] == STATUS_ACCEPTED
    }


def _next_id(session: Session) -> str:
    number = session.execute(text("SELECT nextval('review_id_seq')")).scalar_one()
    return f"rev_{int(number):04d}"


def create_review(
    session: Session, data_dir: Path, version_id: str, *, sample_size: int, seed: int | None
) -> ReviewSession:
    """Draw a sample of accepted rows and store it as an open session."""
    accepted = _accepted(data_dir, session, version_id)
    if not accepted:
        raise ValidationFailed(
            f"Version {version_id} has no accepted rows to review.",
            suggested_action="Review a version made by the filter or split stage.",
        )
    if not 1 <= sample_size <= min(MAX_SAMPLE, len(accepted)):
        raise ValidationFailed(
            f"sample_size must be between 1 and {min(MAX_SAMPLE, len(accepted))} for "
            f"{version_id}, not {sample_size}.",
            suggested_action=f"The version has {len(accepted):,} accepted rows; one session "
            f"reviews at most {MAX_SAMPLE}.",
        )
    if seed is None:
        seed = secrets.randbelow(SEED_LIMIT)
    review = ReviewSession(
        id=_next_id(session),
        version_id=version_id,
        sample_size=sample_size,
        seed=seed,
        status="open",
    )
    session.add(review)
    session.flush()
    for position, key in enumerate(sample_rows(list(accepted), sample_size, seed), start=1):
        session.add(
            ReviewItem(session_id=review.id, position=position, row_key=key, split=accepted[key])
        )
    session.flush()
    return review


def get_review(session: Session, review_id: str) -> ReviewSession:
    review = session.get(ReviewSession, review_id)
    if review is None:
        raise NotFound(f"No review session {review_id!r}.")
    return review


def items_of(session: Session, review_id: str) -> list[ReviewItem]:
    query = select(ReviewItem).where(ReviewItem.session_id == review_id)
    return list(session.scalars(query.order_by(ReviewItem.position)))


def decide(
    session: Session, review_id: str, position: int, *, decision: str, note: str | None
) -> ReviewItem:
    """Record or change one item's decision while the session is open."""
    review = get_review(session, review_id)
    if review.status != "open":
        raise Conflict(
            f"Review {review_id} is closed; its decisions can no longer change.",
            suggested_action="Start a new review session to review again.",
        )
    if decision not in REVIEW_DECISIONS:
        raise ValidationFailed(
            f"decision must be one of {list(REVIEW_DECISIONS)}, not {decision!r}."
        )
    item = session.get(ReviewItem, (review_id, position))
    if item is None:
        raise NotFound(f"Review {review_id} has no item at position {position}.")
    item.decision = decision
    item.note = note
    item.decided_at = datetime.now(UTC)
    session.flush()
    return item


def close_review(session: Session, review_id: str) -> ReviewSession:
    review = get_review(session, review_id)
    if review.status != "open":
        raise Conflict(f"Review {review_id} is already closed.")
    review.status = "closed"
    review.closed_at = datetime.now(UTC)
    session.flush()
    return review


def progress(items: Sequence[ReviewItem]) -> dict[str, Any]:
    """Plain counts, overall and per split. No statistics: those are the owner's."""
    decisions = Counter(item.decision for item in items)
    by_split: dict[str, dict[str, int]] = {}
    for item in items:
        if item.split is None:
            continue
        entry = by_split.setdefault(item.split, {"sampled": 0, "accept": 0, "reject": 0})
        entry["sampled"] += 1
        if item.decision is not None:
            entry[item.decision] += 1
    return {
        "sampled": len(items),
        "decided": len(items) - decisions[None],
        "accept": decisions["accept"],
        "reject": decisions["reject"],
        "undecided": decisions[None],
        "by_split": dict(sorted(by_split.items())),
    }


def acceptance_summary(
    items: Sequence[ReviewItem], *, acceptance: AcceptanceFn | None = None
) -> dict[str, Any]:
    """The acceptance rate with its interval, overall and per split (DATA-4.7.AC1).

    ``None`` with a reason while the owner's ``review_acceptance`` is not written.
    """
    compute = acceptance or report_stats.review_acceptance
    reviewed = [ReviewedItem(item.split, item.decision) for item in items]
    try:
        result = compute(reviewed)
    except NotImplementedError as exc:
        return {"acceptance": None, "acceptance_unavailable": f"owner unit missing: {exc}"}
    return {"acceptance": dataclasses.asdict(result), "acceptance_unavailable": None}


def review_for_report(
    session: Session, version_id: str, review_id: str | None
) -> ReviewInput | None:
    """The review a report on ``version_id`` uses (EVAL-1.1, DATA-4.7.AC1).

    The named review, which must belong to the version, or else the version's
    newest closed review, or ``None`` when it has none.
    """
    if review_id is not None:
        review = get_review(session, review_id)
        if review.version_id != version_id:
            raise ValidationFailed(
                f"Review {review_id} sampled {review.version_id}, not {version_id}.",
                suggested_action="Name a review of the version being reported on, or none.",
            )
    else:
        query = (
            select(ReviewSession)
            .where(ReviewSession.version_id == version_id, ReviewSession.status == "closed")
            .order_by(ReviewSession.closed_at.desc(), ReviewSession.id.desc())
        )
        review = session.scalars(query).first()
        if review is None:
            return None
    items = items_of(session, review.id)
    return ReviewInput(
        review_id=review.id,
        status=review.status,
        sample_size=review.sample_size,
        seed=review.seed,
        items=[ReviewedItem(item.split, item.decision) for item in items],
    )
