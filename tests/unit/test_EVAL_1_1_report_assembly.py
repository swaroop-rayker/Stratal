"""EVAL-1.1: assembling the phase-1 report around the owner's statistics, with fakes.

Passes now: the statistics are fakes or recording stand-ins, so this tests what
the agent's assembly feeds them and where their results go. The statistics
themselves are tested in ``test_EVAL_1_1_report_stats.py``; the stored report
per version in ``tests/integration/test_M5_report.py``.
"""

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stratal.data.commit import VersionRecord, commit_version
from stratal.data.versioning import derive_version_identity
from stratal.eval.report import (
    DEFAULT_EDGES,
    EvaluateConfig,
    ReportStats,
    ReviewInput,
    assemble_phase1_report,
)
from stratal.eval.report_stats import (
    Acceptance,
    RejectionCounts,
    ReviewedItem,
    RowStatus,
    ScoreDistribution,
)
from stratal.storage.manifest import Row
from stratal.testing.fakes import (
    ListSink,
    crude_score_distribution,
    lumped_rejection_counts,
    no_filter_rate,
    overall_only_acceptance,
)


def _row(key: str, status: str, score: float | None, split: str | None = None) -> Row:
    row: Row = {
        "row_key": key,
        "sha256": hashlib.sha256(key.encode()).hexdigest(),
        "status": status,
        "clip_score": score,
        "split": split,
    }
    if status == "rejected":
        row.update(decided_at_stage="ingest", reason_code="corrupt_image")
    elif status == "filtered":
        row.update(decided_at_stage="filter", reason_code="clip_below_threshold")
    return row


ROWS = [
    _row("a.jpg", "accepted", 0.31, "train"),
    _row("b.jpg", "accepted", 0.28, "train"),
    _row("c.jpg", "accepted", 0.35, "test"),
    _row("d.jpg", "accepted", 0.22, "val"),
    _row("e.jpg", "filtered", 0.12),
    _row("f.jpg", "rejected", None),
]


def _split_version(data_dir: Path) -> VersionRecord:
    return commit_version(
        data_dir=data_dir,
        stage="split",
        parent_version_id="ds_parent00000",
        rows=ROWS,
        config={"seed": 0},
        derive=derive_version_identity,
        sink=ListSink(),
    )


class Recorder:
    """Fakes that remember what the assembly gave them."""

    def __init__(self) -> None:
        self.statuses: list[RowStatus] = []
        self.score_calls: list[list[float]] = []
        self.edges: list[tuple[float, ...]] = []
        self.reviewed: list[ReviewedItem] = []
        self.confidence: float | None = None

    def rejections(self, rows: Iterable[RowStatus]) -> RejectionCounts:
        self.statuses = list(rows)
        return lumped_rejection_counts(self.statuses)

    def distribution(self, scores: Sequence[float], *, edges: Sequence[float]) -> ScoreDistribution:
        self.score_calls.append(sorted(scores))
        self.edges.append(tuple(edges))
        return crude_score_distribution(scores, edges=edges)

    def acceptance(self, items: Sequence[ReviewedItem], *, confidence: float = 0.95) -> Acceptance:
        self.reviewed = list(items)
        self.confidence = confidence
        return overall_only_acceptance(items, confidence=confidence)

    def stats(self) -> ReportStats:
        return ReportStats(self.rejections, no_filter_rate, self.distribution, self.acceptance)


def _assemble(
    data_dir: Path, stats: ReportStats, review: ReviewInput | None = None, **config: Any
) -> dict[str, Any]:
    record = _split_version(data_dir)
    return assemble_phase1_report(
        manifest=data_dir / record.manifest_relpath,
        version_id=record.version_id,
        stage="split",
        config=EvaluateConfig.model_validate(config),
        review=review,
        stats=stats,
    )


def test_EVAL_1_1_the_report_has_all_four_parts(tmp_path: Path) -> None:
    review = ReviewInput("rev_0001", "closed", 3, 7, [ReviewedItem("test", "accept")] * 3)

    report = _assemble(tmp_path, Recorder().stats(), review)

    assert report["kind"] == "phase1_dataset" and report["stage"] == "split"
    assert report["rejections"]["total"] == 6
    assert set(report["clip_scores"]) == {"edges", "labelled", "accepted", "by_split"}
    assert "filter_rate" in report
    assert report["review"]["review_id"] == "rev_0001"
    assert report["review"]["acceptance"]["overall"]["accepted"] == 3
    assert report["rows"] == {
        "total": 6,
        "accepted": 4,
        "by_split": {"train": 2, "val": 1, "test": 1},
    }


def test_EVAL_1_1_every_row_is_counted_and_the_right_scores_reach_each_histogram(
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    report = _assemble(tmp_path, recorder.stats())

    assert len(recorder.statuses) == len(ROWS)
    assert RowStatus("filtered", "filter", "clip_below_threshold") in recorder.statuses
    labelled, accepted, train, val, test = recorder.score_calls
    assert labelled == pytest.approx([0.12, 0.22, 0.28, 0.31, 0.35])
    assert accepted == pytest.approx([0.22, 0.28, 0.31, 0.35])
    assert (train, val, test) == (
        pytest.approx([0.28, 0.31]),
        pytest.approx([0.22]),
        pytest.approx([0.35]),
    )
    assert set(recorder.edges) == {DEFAULT_EDGES}
    assert report["clip_scores"]["edges"] == list(DEFAULT_EDGES)
    assert report["review"] is None


def test_DATA_4_7_the_review_s_splits_and_decisions_reach_the_acceptance(tmp_path: Path) -> None:
    items = [
        ReviewedItem("test", "accept"),
        ReviewedItem("train", "reject"),
        ReviewedItem("val", None),
    ]
    recorder = Recorder()

    report = _assemble(
        tmp_path, recorder.stats(), ReviewInput("rev_0002", "open", 3, 1, items), confidence=0.9
    )

    assert recorder.reviewed == items
    assert recorder.confidence == 0.9
    assert report["review"]["decided"] == 2
    assert report["review"]["status"] == "open"


def test_EVAL_1_1_the_report_config_checks_its_bins() -> None:
    assert EvaluateConfig().score_edges[0] == 0.0 and EvaluateConfig().score_edges[-1] == 0.5
    for edges in ([0.2], [0.2, 0.2], [0.3, 0.2]):
        with pytest.raises(ValidationError):
            EvaluateConfig.model_validate({"score_edges": edges})
    with pytest.raises(ValidationError):
        EvaluateConfig.model_validate({"confidence": 1.0})
