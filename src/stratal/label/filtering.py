"""The filter rule (LABEL-1.4).

An owner unit (ADR-0012), written by the agent at the owner's request on
2026-11-15. The threshold itself stays the owner's to choose (OC-5).

The filter stage reads the label stage's version, gives this function every row
that is still accepted, and marks the rows it returns as ``filtered`` with the
returned reason code. Rows are marked, never deleted (CL-17, LABEL-1.4.AC1).

Tests: ``tests/unit/test_LABEL_1_4_filter_rule.py`` and
``tests/unit/test_LABEL_M4_unit_decisions.py``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

ALGORITHM_VERSION = 1
"""Bump when the rule changes; it is part of the filter stage's config."""

REASON_BELOW_THRESHOLD = "clip_below_threshold"
REASON_MISSING_CAPTION = "missing_caption"


@dataclass(frozen=True)
class ScoredItem:
    row_key: str
    caption: str | None
    clip_score: float | None


def filter_decisions(items: Sequence[ScoredItem], *, threshold: float) -> dict[str, str]:
    """Which rows to mark ``filtered``, and why: ``{row_key: reason_code}``.

    Must satisfy:

    1. A row whose caption is missing or blank is marked
       ``REASON_MISSING_CAPTION``, whatever its score.
    2. Otherwise a row whose ``clip_score`` is below ``threshold`` is marked
       ``REASON_BELOW_THRESHOLD``. A score equal to the threshold is kept.
    3. A row with a caption but no score is a ``ValueError`` naming the row: the
       label stage scores every captioned row (LABEL-1.3.AC1), so this means a
       broken input, not a decision.
    4. Rows that stay accepted are absent from the result. Only keys given in
       ``items`` appear in it.
    5. The result depends only on the items and the threshold, not their order.
    6. A threshold that is not a finite number is a ``ValueError``.

    Two more refusals, so that rule 5 and the stage's counts can be trusted:

    - The same ``row_key`` twice is a ``ValueError``. A manifest's keys are
      unique, so this is broken input; accepting it would make the answer
      depend on which copy came last.
    - A score that is not a finite number (NaN, infinity) is a ``ValueError``
      naming the row. NaN compares false with every threshold, so without this
      a broken score would silently keep its row.

    Missing captions are checked before scores (rule 1), so a row without a
    caption is marked whatever its score, including no score at all.
    """
    if not math.isfinite(threshold):
        raise ValueError(f"the CLIP threshold must be a finite number, got {threshold!r}")
    seen: set[str] = set()
    marks: dict[str, str] = {}
    for item in items:
        if item.row_key in seen:
            raise ValueError(f"row {item.row_key!r} is given twice")
        seen.add(item.row_key)
        if item.caption is None or not item.caption.strip():
            marks[item.row_key] = REASON_MISSING_CAPTION
            continue
        if item.clip_score is None:
            raise ValueError(
                f"row {item.row_key!r} has a caption but no CLIP score; the label stage "
                "scores every captioned row (LABEL-1.3.AC1)"
            )
        if not math.isfinite(item.clip_score):
            raise ValueError(f"row {item.row_key!r} has a CLIP score that is not finite")
        if item.clip_score < threshold:
            marks[item.row_key] = REASON_BELOW_THRESHOLD
    return dict(sorted(marks.items()))
