"""Stage 4: mark pairs the owner's filter rule rejects (LABEL-1.4).

The shell around :func:`~stratal.label.filtering.filter_decisions` (ADR-0012):
it gives the rule every accepted row's caption and CLIP score, checks what comes
back, and marks those rows ``filtered`` with the reason, keeping every row
(CL-17, LABEL-1.4.AC1). Changing the threshold reruns only this stage and what
follows; nothing is captioned again (ARCHITECTURE.md §3).
"""

import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.versioning import VersionDeriver
from stratal.label import filtering
from stratal.label.filtering import (
    REASON_BELOW_THRESHOLD,
    REASON_MISSING_CAPTION,
    ScoredItem,
)
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_FILTERED, Row, iter_rows

STAGE = "filter"
REASONS = frozenset({REASON_BELOW_THRESHOLD, REASON_MISSING_CAPTION})


class FilterConfig(BaseModel):
    """Settings for the filter stage. Every field is recorded in the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clip_threshold: float = Field(
        default=0.2,
        allow_inf_nan=False,
        description="Pairs whose CLIP cosine is below this are filtered (LABEL-1.4). 0.2, "
        "chosen by the owner from the measured score distribution (OC-5, 2026-11-15).",
    )
    algorithm_version: int = filtering.ALGORITHM_VERSION


class FilterRule(Protocol):
    def __call__(self, items: Sequence[ScoredItem], *, threshold: float) -> dict[str, str]: ...


@dataclass(frozen=True)
class FilterResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]
    seconds: float


@dataclass
class _Decisions:
    marks: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    scores: dict[str, float | None] = field(default_factory=lambda: dict[str, float | None]())


def _decide(
    parent_manifest: Path, config: FilterConfig, rule: FilterRule, sink: EventSink
) -> _Decisions:
    items = [
        ScoredItem(str(row["row_key"]), row["caption"], row["clip_score"])
        for row in iter_rows(
            parent_manifest, columns=["row_key", "status", "caption", "clip_score"]
        )
        if row["status"] == STATUS_ACCEPTED
    ]
    sink.emit(
        stage=STAGE,
        message=f"Applying the CLIP filter to {len(items):,} pairs"
        f" · threshold {config.clip_threshold}",
        metrics={"input_accepted": len(items), "clip_threshold": config.clip_threshold},
    )
    marks = rule(items, threshold=config.clip_threshold)
    given = {item.row_key for item in items}
    unknown = sorted(set(marks) - given)
    if unknown:
        raise StratalError(
            f"filter_decisions marked rows it was not given, e.g. {unknown[0]!r}.",
            suggested_action="Check the owner's filter_decisions (LABEL-1.4, rule 4).",
        )
    odd = sorted({reason for reason in marks.values() if reason not in REASONS})
    if odd:
        raise StratalError(
            f"filter_decisions returned unknown reason codes: {odd}.",
            suggested_action=f"Use one of {sorted(REASONS)} (DATA_MODEL.md reason codes).",
        )
    return _Decisions(marks, {item.row_key: item.clip_score for item in items})


def _detail(reason: str, score: float | None, threshold: float) -> str:
    if reason == REASON_BELOW_THRESHOLD and score is not None and math.isfinite(score):
        return f"CLIP score {score:.4f} is below the threshold {threshold}"
    return "no caption" if reason == REASON_MISSING_CAPTION else reason


def _apply(rows: Iterator[Row], decisions: _Decisions, threshold: float) -> Iterator[Row]:
    for row in rows:
        reason = decisions.marks.get(str(row["row_key"]))
        if row["status"] == STATUS_ACCEPTED and reason is not None:
            row.update(
                status=STATUS_FILTERED,
                decided_at_stage=STAGE,
                reason_code=reason,
                reason_detail=_detail(reason, decisions.scores.get(str(row["row_key"])), threshold),
            )
        yield row


def run_filter(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: FilterConfig,
    derive: VersionDeriver,
    sink: EventSink,
    rule: FilterRule = filtering.filter_decisions,
) -> FilterResult:
    """Apply the filter rule to the parent version and commit a ``filter`` version."""
    started = time.monotonic()
    decisions = _decide(parent_manifest, config, rule, sink)
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), decisions, config.clip_threshold),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    given = len(decisions.scores)
    filtered = len(decisions.marks)
    metrics: dict[str, MetricValue] = {
        "input_accepted": given,
        "filtered": filtered,
        "accepted": given - filtered,
        "filter_rate": round(filtered / given, 4) if given else 0.0,
        "clip_threshold": config.clip_threshold,
    }
    for reason in sorted(REASONS):
        metrics[f"filtered_{reason}"] = sum(1 for r in decisions.marks.values() if r == reason)
    return FilterResult(record, metrics, round(time.monotonic() - started, 3))
