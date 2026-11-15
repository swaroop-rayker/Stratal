"""LABEL-1.4: the filter rule. An owner unit.

Strict expected failures until ``stratal.label.filtering.filter_decisions`` is
written (ADR-0012); then remove the marker. That the stage marks rather than
deletes is tested with the stage itself.
"""

import math
import random

import pytest

from stratal.label.filtering import (
    REASON_BELOW_THRESHOLD,
    REASON_MISSING_CAPTION,
    ScoredItem,
    filter_decisions,
)

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="owner unit LABEL-1.4 not written yet"
)


def test_LABEL_1_4_rows_below_the_threshold_are_marked_with_a_reason() -> None:
    items = [
        ScoredItem("a.jpg", "a dog on a beach", 0.31),
        ScoredItem("b.jpg", "a red bus", 0.12),
        ScoredItem("c.jpg", "two cats", 0.20),
    ]

    assert filter_decisions(items, threshold=0.20) == {"b.jpg": REASON_BELOW_THRESHOLD}


def test_LABEL_1_4_a_missing_or_blank_caption_is_marked_whatever_its_score() -> None:
    items = [ScoredItem("a.jpg", None, None), ScoredItem("b.jpg", "   ", 0.9)]

    assert filter_decisions(items, threshold=0.2) == {
        "a.jpg": REASON_MISSING_CAPTION,
        "b.jpg": REASON_MISSING_CAPTION,
    }


def test_LABEL_1_4_a_captioned_row_without_a_score_is_refused() -> None:
    with pytest.raises(ValueError, match=r"c\.jpg"):
        filter_decisions([ScoredItem("c.jpg", "a tree", None)], threshold=0.2)


def test_LABEL_1_4_order_does_not_matter_and_the_threshold_must_be_finite() -> None:
    rng = random.Random(4)
    items = [ScoredItem(f"{i}.jpg", "x", rng.random()) for i in range(200)]
    shuffled = list(items)
    rng.shuffle(shuffled)

    assert filter_decisions(items, threshold=0.5) == filter_decisions(shuffled, threshold=0.5)
    with pytest.raises(ValueError):
        filter_decisions(items, threshold=math.nan)
