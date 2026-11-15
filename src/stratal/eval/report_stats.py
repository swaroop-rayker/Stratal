"""Statistics of the phase-1 dataset report (EVAL-1.1). Owner units (ADR-0012).

The report has four parts (SPEC.md §EVAL-1.1): rejection counts per stage, the
CLIP score distribution, the filter rate, and the manual review's acceptance
with a 95% Wilson interval. The evaluate stage and the report assembly (the
agent's, :mod:`stratal.eval.report`) read the manifest and the review, call
these five functions, and store what they return in ``eval_reports``.

Every function is pure: plain values in, plain values out, nothing read from
disk or the database. Tests: ``tests/unit/test_EVAL_1_1_*.py`` (strict
expected failures until these are written).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ALGORITHM_VERSION = 1
"""Bump when any function here returns something different; it is stored in each report."""

ACCEPT = "accept"
REJECT = "reject"

# ---------------------------------------------------------- rejection counts


@dataclass(frozen=True)
class RowStatus:
    """One manifest row, as the counts see it."""

    status: str
    """``accepted``, ``rejected``, ``duplicate`` or ``filtered`` (DATA_MODEL.md §5)."""
    decided_at_stage: str | None
    reason_code: str | None


@dataclass(frozen=True)
class RejectionCounts:
    total: int
    accepted: int
    by_stage: dict[str, dict[str, int]]
    """``{stage: {reason_code: rows}}`` for the rows each stage took out."""


def rejection_counts(rows: Iterable[RowStatus]) -> RejectionCounts:
    """How many rows each stage took out, and why.

    ``rows`` is every row of one manifest, streamed: it can be iterated once
    only, and a 20,000-row manifest must not be held in memory (NFR-2).

    Must satisfy:

    1. Every row that is not ``accepted`` is counted once, under its
       ``decided_at_stage`` and ``reason_code``.
    2. ``accepted`` plus every count in ``by_stage`` equals ``total``.
    3. Only stages and reasons that occur appear; there are no zero counts.
    4. A non-accepted row without a stage or a reason breaks INV-07 and is a
       ``ValueError``; so is a status that is not one of the four.
    """
    raise NotImplementedError("Owner unit EVAL-1.1: rejection_counts")


def filter_rate(counts: RejectionCounts) -> float | None:
    """The share of the rows that reached the CLIP filter which it took out.

    Must satisfy:

    1. The denominator is the rows the filter stage saw: the rows still
       accepted plus those marked at stage ``filter``. Rows that earlier stages
       took out are not part of it (they never reached the filter).
    2. ``None`` when no row reached the filter.
    3. A value between 0 and 1, not rounded.
    """
    raise NotImplementedError("Owner unit EVAL-1.1: filter_rate")


# -------------------------------------------------------- score distribution

QUANTILES: tuple[tuple[str, float], ...] = (
    ("p05", 0.05),
    ("p25", 0.25),
    ("p50", 0.50),
    ("p75", 0.75),
    ("p95", 0.95),
)
"""The quantiles a distribution reports, by name."""


@dataclass(frozen=True)
class ScoreDistribution:
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    quantiles: dict[str, float]
    """``{name: value}`` for each of :data:`QUANTILES`; empty when ``count`` is 0."""
    edges: tuple[float, ...]
    histogram: tuple[int, ...]
    """Rows per bin; ``len(edges) - 1`` bins."""
    below: int
    """Scores less than ``edges[0]``."""
    above: int
    """Scores greater than ``edges[-1]``."""


def score_distribution(scores: Sequence[float], *, edges: Sequence[float]) -> ScoreDistribution:
    """Summary and histogram of the CLIP scores of a version's accepted rows.

    The edges are fixed by the caller, not derived from the data, so that
    reports of different versions can be compared bin by bin.

    Must satisfy:

    1. ``mean`` is the arithmetic mean; ``std`` the sample standard deviation
       (divisor ``count - 1``), and 0.0 for a single score.
    2. Quantiles by linear interpolation between closest ranks: the q-th
       quantile of sorted scores ``x[0..n-1]`` is at position ``q * (n - 1)``,
       interpolating between its neighbours (numpy's default, and
       Hyndman–Fan type 7).
    3. Bins are half-open, ``[edges[i], edges[i+1])``, except the last, which
       includes its right edge. ``below`` and ``above`` count the rest, so
       ``sum(histogram) + below + above == count``.
    4. No scores: ``count`` 0, every number ``None``, no quantiles, every bin 0.
    5. ``ValueError`` for a score that is not finite, or fewer than two edges,
       or edges that are not strictly increasing.
    """
    raise NotImplementedError("Owner unit EVAL-1.1: score_distribution")


# ------------------------------------------------------------ Wilson interval


@dataclass(frozen=True)
class Interval:
    estimate: float
    """``successes / trials``."""
    low: float
    high: float
    confidence: float


def wilson_interval(successes: int, trials: int, *, confidence: float = 0.95) -> Interval:
    """The Wilson score interval for a binomial proportion.

    Used for the review's acceptance rate, where samples are small (50 rows)
    and rates are near 1, which is exactly where the textbook ``p ± z·SE``
    interval fails: it can pass 1 and shrinks to nothing when every row is
    accepted.

    Must satisfy:

    1. ``z`` is the standard normal quantile at ``1 - (1 - confidence) / 2``,
       computed, not rounded (1.959964… for 0.95, not 1.96).
    2. Agrees with published values to four decimals (the tests use Newcombe,
       *Statistics in Medicine* 1998, table I).
    3. ``0 <= low <= estimate <= high <= 1``. ``low`` is exactly 0 when
       ``successes`` is 0, and ``high`` exactly 1 when ``successes`` equals
       ``trials``.
    4. ``ValueError`` when ``trials < 1``, ``successes`` is outside
       ``0..trials``, or ``confidence`` is not strictly between 0 and 1.
    """
    raise NotImplementedError("Owner unit EVAL-1.1: wilson_interval")


# ---------------------------------------------------------- review acceptance


@dataclass(frozen=True)
class ReviewedItem:
    """One sampled row of a review session."""

    split: str | None
    """The row's split when it was sampled (DATA-4.7), or ``None`` before splitting."""
    decision: str | None
    """``accept``, ``reject``, or ``None`` while undecided."""


@dataclass(frozen=True)
class AcceptanceRate:
    decided: int
    accepted: int
    interval: Interval | None
    """``None`` when nothing was decided."""


@dataclass(frozen=True)
class Acceptance:
    sampled: int
    undecided: int
    overall: AcceptanceRate
    by_split: dict[str, AcceptanceRate]
    """One entry per split that has at least one decided item."""


def review_acceptance(items: Sequence[ReviewedItem], *, confidence: float = 0.95) -> Acceptance:
    """Acceptance rate of a review, overall and per split (LABEL-1.5, DATA-4.7.AC1).

    Must satisfy:

    1. Undecided items are counted in ``sampled`` and ``undecided`` only; rates
       are over decided items.
    2. Intervals are :func:`wilson_interval` at ``confidence``.
    3. Items without a split count in ``overall`` only.
    4. ``ValueError`` for a decision that is not ``accept``, ``reject`` or
       ``None``.
    """
    raise NotImplementedError("Owner unit EVAL-1.1: review_acceptance")
