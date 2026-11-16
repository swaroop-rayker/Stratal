"""Stage 4: mark pairs the owner's filter rule rejects (LABEL-1.4).

The shell around :func:`~stratal.label.filtering.filter_decisions` (ADR-0012):
it gives the rule every accepted row's caption and CLIP score, checks what comes
back, and marks those rows ``filtered`` with the reason, keeping every row
(CL-17, LABEL-1.4.AC1). Changing the threshold reruns only this stage and what
follows; nothing is captioned again (ARCHITECTURE.md §3).
"""

import dataclasses
import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.dataset_types import DATASET_TYPES, apply_report
from stratal.data.versioning import VersionDeriver
from stratal.label import filtering
from stratal.label.filtering import (
    REASON_BELOW_THRESHOLD,
    REASON_MISSING_CAPTION,
    ScoredItem,
)
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
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
    validate_as: Literal["image_caption"] = Field(
        default="image_caption",
        description="The dataset type the rows left after the rule are validated against "
        "(DATA-5.1). Part of the config so versions made before M5, without validation, are "
        "not reused by memoization.",
    )


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
    """Apply the filter rule, validate what is left, and commit a ``filter`` version.

    After the rule, the rows still accepted are checked against the
    image–caption schema (DATA-5.1): a row without a caption, or whose original
    or preprocessed image is missing from the blob store, is marked
    ``rejected`` with ``missing_caption`` or ``missing_file`` and listed in the
    validation report, stored as ``counts.validation``. Rejecting a missing
    file rather than failing the stage is the owner's choice of 2026-11-16
    (DECISIONS.md, M5); the WARN event makes the loss visible. The version is
    then of type ``image_caption``.
    """
    started = time.monotonic()
    decisions = _decide(parent_manifest, config, rule, sink)
    blobs = BlobStore(data_dir)
    threshold = config.clip_threshold
    validation = DATASET_TYPES[config.validate_as](
        _apply(iter_rows(parent_manifest), decisions, threshold), blobs.exists
    )
    reasons = validation.by_reason()
    if validation.problems:
        first = validation.problems[0]
        sink.emit(
            stage=STAGE,
            substage="validate",
            level="WARN",
            message=f"Schema validation rejected {len(validation.problems):,} rows "
            f"({', '.join(f'{n} {r}' for r, n in sorted(reasons.items()))}), "
            f"e.g. {first.row_key}: {first.detail}",
            reason="Rows of an image–caption dataset need a caption and their image files "
            "(DATA-5.1).",
            suggested_action="A missing file means data was lost after preprocess: check the "
            "data volume. The rows are kept, marked rejected, and listed in the version's "
            "validation report.",
            metrics={f"rejected_{r}": n for r, n in reasons.items()},
        )
    else:
        sink.emit(
            stage=STAGE,
            substage="validate",
            message=f"Schema validation passed: {validation.checked:,} image–caption rows",
            metrics={"validated": validation.checked},
        )
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=apply_report(
            _apply(iter_rows(parent_manifest), decisions, threshold), validation, stage=STAGE
        ),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    report = {
        "dtype": validation.dtype,
        "checked": validation.checked,
        "valid": validation.valid,
        "by_reason": dict(sorted(reasons.items())),
        "rejected_rows": [
            {"row_key": p.row_key, "reason_code": p.reason_code, "detail": p.detail}
            for p in sorted(validation.problems, key=lambda p: p.row_key)
        ],
    }
    record = dataclasses.replace(
        record, dtype=validation.dtype, counts={**record.counts, "validation": report}
    )
    given = len(decisions.scores)
    filtered = len(decisions.marks)
    metrics: dict[str, MetricValue] = {
        "input_accepted": given,
        "filtered": filtered,
        "accepted": validation.valid,
        "filter_rate": round(filtered / given, 4) if given else 0.0,
        "clip_threshold": config.clip_threshold,
        "validated": validation.checked,
    }
    for reason in sorted(REASONS):
        metrics[f"filtered_{reason}"] = sum(1 for r in decisions.marks.values() if r == reason)
    for reason in ("missing_caption", "missing_file"):
        metrics[f"rejected_{reason}"] = reasons.get(reason, 0)
    return FilterResult(record, metrics, round(time.monotonic() - started, 3))
