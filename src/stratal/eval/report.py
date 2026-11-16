"""Assembly of the phase-1 dataset report (EVAL-1.1). The agent's plumbing.

:func:`assemble_phase1_report` reads one version's manifest, feeds the owner's
statistics (:mod:`stratal.eval.report_stats`) and puts the four parts into one
JSON document:

1. rejection counts per stage and reason, with the rows per split;
2. the CLIP score distribution of every labelled row, of the accepted rows,
   and of the accepted rows of each split, all on the same fixed bins;
3. the filter rate;
4. the manual review's acceptance, overall and per split, with Wilson
   intervals (DATA-4.7.AC1), when a review is given.

The evaluate stage stores the result in ``eval_reports``. No database here.
"""

import dataclasses
import itertools
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratal.data.splitting import SPLITS
from stratal.eval import report_stats
from stratal.eval.report_stats import (
    Acceptance,
    RejectionCounts,
    ReviewedItem,
    RowStatus,
    ScoreDistribution,
)
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows

KIND = "phase1_dataset"
DEFAULT_EDGES: tuple[float, ...] = tuple(round(i * 0.025, 3) for i in range(21))
"""0.000 to 0.500 in steps of 0.025: where CLIP cosines of image–caption pairs fall."""


class EvaluateConfig(BaseModel):
    """Settings for the evaluate stage (EVAL-1.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["phase1_dataset"] = KIND
    review_id: str | None = Field(
        default=None,
        description="The review whose decisions the report uses. When absent, the version's "
        "newest closed review, if any.",
    )
    score_edges: tuple[float, ...] = Field(
        default=DEFAULT_EDGES,
        description="Bin edges of the CLIP score histograms; fixed, so reports compare bin by bin.",
    )
    confidence: float = Field(default=0.95, gt=0, lt=1, description="Of the Wilson intervals.")

    @field_validator("score_edges")
    @classmethod
    def _increasing(cls, edges: tuple[float, ...]) -> tuple[float, ...]:
        if len(edges) < 2 or any(b <= a for a, b in itertools.pairwise(edges)):
            raise ValueError("score_edges needs at least two strictly increasing values")
        return edges


@dataclass(frozen=True)
class ReportStats:
    """The owner's statistics, passed in so tests and early pipelines can use fakes."""

    rejections: Callable[[Iterable[RowStatus]], RejectionCounts]
    filter_rate: Callable[[RejectionCounts], float | None]
    distribution: Callable[..., ScoreDistribution]
    acceptance: Callable[..., Acceptance]


OWNER_STATS = ReportStats(
    rejections=report_stats.rejection_counts,
    filter_rate=report_stats.filter_rate,
    distribution=report_stats.score_distribution,
    acceptance=report_stats.review_acceptance,
)


@dataclass(frozen=True)
class ReviewInput:
    """The review a report uses: its identity, and each item's split and decision."""

    review_id: str
    status: str
    sample_size: int
    seed: int
    items: Sequence[ReviewedItem]


def _statuses(manifest: Path) -> Iterator[RowStatus]:
    for row in iter_rows(manifest, columns=["status", "decided_at_stage", "reason_code"]):
        yield RowStatus(row["status"], row["decided_at_stage"], row["reason_code"])


def _scores(
    manifest: Path,
) -> tuple[list[float], list[float], dict[str, list[float]], Counter[str]]:
    labelled: list[float] = []
    accepted: list[float] = []
    by_split: defaultdict[str, list[float]] = defaultdict(list)
    splits: Counter[str] = Counter()
    for row in iter_rows(manifest, columns=["status", "clip_score", "split"]):
        score = row["clip_score"]
        is_accepted = row["status"] == STATUS_ACCEPTED
        if is_accepted and row["split"] is not None:
            splits[str(row["split"])] += 1
        if score is None:
            continue
        labelled.append(float(score))
        if is_accepted:
            accepted.append(float(score))
            if row["split"] is not None:
                by_split[str(row["split"])].append(float(score))
    return labelled, accepted, dict(by_split), splits


def _review_part(review: ReviewInput, stats: ReportStats, confidence: float) -> dict[str, Any]:
    decided = [item for item in review.items if item.decision is not None]
    return {
        "review_id": review.review_id,
        "status": review.status,
        "sample_size": review.sample_size,
        "seed": review.seed,
        "decided": len(decided),
        "acceptance": dataclasses.asdict(stats.acceptance(review.items, confidence=confidence)),
    }


def assemble_phase1_report(
    *,
    manifest: Path,
    version_id: str,
    stage: str,
    config: EvaluateConfig,
    review: ReviewInput | None,
    stats: ReportStats = OWNER_STATS,
) -> dict[str, Any]:
    """The phase-1 report of one version, as a JSON-ready dict (EVAL-1.1.AC1)."""
    rejections = stats.rejections(_statuses(manifest))
    labelled, accepted, by_split, splits = _scores(manifest)
    edges = config.score_edges

    def distribution(scores: list[float]) -> dict[str, Any]:
        return dataclasses.asdict(stats.distribution(scores, edges=edges))

    return {
        "kind": KIND,
        "version_id": version_id,
        "stage": stage,
        "algorithm_version": report_stats.ALGORITHM_VERSION,
        "confidence": config.confidence,
        "rows": {
            "total": rejections.total,
            "accepted": rejections.accepted,
            "by_split": {split: splits[split] for split in SPLITS} if splits else None,
        },
        "rejections": dataclasses.asdict(rejections),
        "filter_rate": stats.filter_rate(rejections),
        "clip_scores": {
            "edges": list(edges),
            "labelled": distribution(labelled),
            "accepted": distribution(accepted),
            "by_split": {split: distribution(by_split.get(split, [])) for split in SPLITS}
            if splits
            else None,
        },
        "review": None if review is None else _review_part(review, stats, config.confidence),
    }
