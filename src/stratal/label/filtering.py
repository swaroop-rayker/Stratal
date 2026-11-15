"""The filter rule (LABEL-1.4). An owner unit (ADR-0012).

The filter stage reads the label stage's version, gives this function every row
that is still accepted, and marks the rows it returns as ``filtered`` with the
returned reason code. Rows are marked, never deleted (CL-17, LABEL-1.4.AC1).

Tests: ``tests/unit/test_LABEL_1_4_filter_rule.py`` (strict expected failures
until this is written).
"""

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
    """
    raise NotImplementedError("Owner unit LABEL-1.4: filter_decisions")
