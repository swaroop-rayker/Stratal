"""LABEL-1.5, DATA-4.7: the review's sampling and summaries, without a database.

Storage, the endpoints and the link to the version are tested against the real
database in ``tests/integration/test_M5_review.py``.
"""

import random
from collections.abc import Sequence

from stratal.db.models import ReviewItem
from stratal.eval.report_stats import Acceptance, ReviewedItem
from stratal.label.review import acceptance_summary, progress, sample_rows
from stratal.testing.fakes import overall_only_acceptance


def _item(position: int, split: str | None, decision: str | None) -> ReviewItem:
    return ReviewItem(
        session_id="rev_0001",
        position=position,
        row_key=f"{position:04d}.jpg",
        split=split,
        decision=decision,
    )


def test_LABEL_1_5_the_same_rows_and_seed_give_the_same_sample_in_any_order() -> None:
    keys = [f"img_{i:05d}.jpg" for i in range(1_000)]
    shuffled = list(keys)
    random.Random(1).shuffle(shuffled)

    first = sample_rows(keys, 50, seed=42)

    assert sample_rows(shuffled, 50, seed=42) == first
    assert len(set(first)) == 50 and set(first) <= set(keys)
    assert sample_rows(keys, 50, seed=43) != first


def test_LABEL_1_5_progress_counts_decisions_overall_and_per_split() -> None:
    items = [_item(1, "train", "accept"), _item(2, "train", "reject"), _item(3, "test", None)]
    items.append(_item(4, None, "accept"))

    assert progress(items) == {
        "sampled": 4,
        "decided": 3,
        "accept": 2,
        "reject": 1,
        "undecided": 1,
        "by_split": {
            "test": {"sampled": 1, "accept": 0, "reject": 0},
            "train": {"sampled": 1 + 1, "accept": 1, "reject": 1},
        },
    }


def test_DATA_4_7_the_summary_passes_each_item_s_split_and_decision_to_the_statistics() -> None:
    seen: list[ReviewedItem] = []

    def recording(items: Sequence[ReviewedItem]) -> Acceptance:
        seen.extend(items)
        return overall_only_acceptance(items)

    summary = acceptance_summary(
        [_item(1, "test", "accept"), _item(2, "val", None)], acceptance=recording
    )

    assert seen == [ReviewedItem("test", "accept"), ReviewedItem("val", None)]
    assert summary["acceptance_unavailable"] is None
    assert summary["acceptance"]["overall"]["accepted"] == 1


def test_LABEL_1_5_the_summary_says_why_while_the_owner_unit_is_missing() -> None:
    def missing(items: Sequence[ReviewedItem]) -> Acceptance:
        raise NotImplementedError("Owner unit EVAL-1.1: review_acceptance")

    summary = acceptance_summary([_item(1, "test", "accept")], acceptance=missing)

    assert summary["acceptance"] is None
    assert "EVAL-1.1" in summary["acceptance_unavailable"]
