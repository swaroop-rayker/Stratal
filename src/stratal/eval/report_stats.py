"""Statistics of the phase-1 dataset report (EVAL-1.1). Owner units (ADR-0012).

The report has four parts (SPEC.md §EVAL-1.1): rejection counts per stage, the
CLIP score distribution, the filter rate, and the manual review's acceptance
with a 95% Wilson interval. The evaluate stage and the report assembly (the
agent's, :mod:`stratal.eval.report`) read the manifest and the review, call
these five functions, and store what they return in ``eval_reports``.

Every function is pure: plain values in, plain values out, nothing read from
disk or the database. Tests: ``tests/unit/test_EVAL_1_1_report_stats.py``.

Written by the agent at the owner's request on 2026-11-16 ("write this EVAL
for me as well"). Each docstring gives the contract, then how it is met.
"""

import bisect
import itertools
import math
import statistics
from collections import Counter, defaultdict
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

    How: one pass, a counter per stage. Only the counters are kept, never the
    rows.
    """
    total = accepted = 0
    by_stage: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        total += 1
        if row.status == "accepted":
            accepted += 1
            continue
        if row.status not in _TAKEN_OUT:
            raise ValueError(f"unknown status {row.status!r}")
        if not row.decided_at_stage or not row.reason_code:
            raise ValueError(
                f"a {row.status} row has no deciding stage or reason (INV-07): "
                f"stage={row.decided_at_stage!r}, reason={row.reason_code!r}"
            )
        by_stage[row.decided_at_stage][row.reason_code] += 1
    return RejectionCounts(total, accepted, {s: dict(c) for s, c in by_stage.items()})


_TAKEN_OUT = frozenset({"rejected", "duplicate", "filtered"})


def filter_rate(counts: RejectionCounts) -> float | None:
    """The share of the rows that reached the CLIP filter which it took out.

    Must satisfy:

    1. The denominator is the rows the filter stage saw: the rows still
       accepted plus those marked at stage ``filter``. Rows that earlier stages
       took out are not part of it (they never reached the filter).
    2. ``None`` when no row reached the filter.
    3. A value between 0 and 1, not rounded.

    Since M5 the filter stage also rejects rows that fail schema validation
    (DATA-5.1, ``missing_caption`` or ``missing_file`` at stage ``filter``).
    They count as taken out by the filter stage, so the rate is that of the
    stage, not of the CLIP threshold alone. The report keeps the reasons
    apart under ``rejections.by_stage.filter``.
    """
    taken = sum(counts.by_stage.get(FILTER_STAGE, {}).values())
    reached = counts.accepted + taken
    return None if reached == 0 else taken / reached


FILTER_STAGE = "filter"


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

    How: sort once; quantiles read from the sorted list; each score's bin by
    binary search (``bisect_right`` gives the first edge greater than the
    score, so the bin to its left holds it, which makes bins half-open).
    """
    bounds = tuple(float(edge) for edge in edges)
    if len(bounds) < 2 or any(b <= a for a, b in itertools.pairwise(bounds)):
        raise ValueError("edges must be at least two strictly increasing numbers")
    values = sorted(float(score) for score in scores)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("every score must be a finite number")
    histogram = [0] * (len(bounds) - 1)
    below = above = 0
    for value in values:
        if value < bounds[0]:
            below += 1
        elif value > bounds[-1]:
            above += 1
        elif value == bounds[-1]:
            histogram[-1] += 1
        else:
            histogram[bisect.bisect_right(bounds, value) - 1] += 1
    count = len(values)
    if count == 0:
        return ScoreDistribution(0, None, None, None, None, {}, bounds, tuple(histogram), 0, 0)
    return ScoreDistribution(
        count=count,
        mean=statistics.fmean(values),
        std=statistics.stdev(values) if count > 1 else 0.0,
        minimum=values[0],
        maximum=values[-1],
        quantiles={name: _type7(values, q) for name, q in QUANTILES},
        edges=bounds,
        histogram=tuple(histogram),
        below=below,
        above=above,
    )


def _type7(ordered: Sequence[float], q: float) -> float:
    """The q-th quantile of sorted values by linear interpolation (Hyndman–Fan type 7)."""
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    if low + 1 >= len(ordered):
        return ordered[low]
    return ordered[low] + (ordered[low + 1] - ordered[low]) * (position - low)


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

    How: with ``p = successes / trials``, ``n = trials`` and the quantile
    ``z``, the interval is the set of proportions ``π`` for which
    ``|p − π| ≤ z·sqrt(π(1 − π)/n)``. That is the test the textbook interval
    inverts, except that the standard error uses the unknown ``π`` instead of
    the estimate ``p``. Squaring both sides gives a quadratic in ``π``. Its
    roots are::

        centre = (p + z²/2n) / (1 + z²/n)
        half   = z · sqrt(p(1 − p)/n + z²/4n²) / (1 + z²/n)

    The centre is pulled from ``p`` towards 1/2, which is why the interval
    neither passes 0 or 1 nor collapses when ``p`` is 0 or 1. At the
    extremes the formula gives exactly 0 or 1 in exact arithmetic; the code
    sets them exactly, so rounding cannot leave ``high = 0.9999999999``.
    """
    if trials < 1:
        raise ValueError(f"trials must be at least 1, not {trials}")
    if not 0 <= successes <= trials:
        raise ValueError(f"successes must be between 0 and {trials}, not {successes}")
    if not (math.isfinite(confidence) and 0 < confidence < 1):
        raise ValueError(f"confidence must be strictly between 0 and 1, not {confidence}")
    z = statistics.NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    n = trials
    p = successes / n
    shrink = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / shrink
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / shrink
    low = 0.0 if successes == 0 else min(max(centre - half, 0.0), p)
    high = 1.0 if successes == trials else max(min(centre + half, 1.0), p)
    return Interval(estimate=p, low=low, high=high, confidence=confidence)


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

    ``by_split`` lists ``train``, ``val`` and ``test`` in that order, then any
    other split names alphabetically.
    """
    odd = sorted({str(i.decision) for i in items if i.decision not in (ACCEPT, REJECT, None)})
    if odd:
        raise ValueError(f"unknown review decisions: {odd}")
    decided = [item for item in items if item.decision is not None]

    def rate(subset: Sequence[ReviewedItem]) -> AcceptanceRate:
        accepted = sum(1 for item in subset if item.decision == ACCEPT)
        interval = wilson_interval(accepted, len(subset), confidence=confidence) if subset else None
        return AcceptanceRate(len(subset), accepted, interval)

    per_split: defaultdict[str, list[ReviewedItem]] = defaultdict(list)
    for item in decided:
        if item.split is not None:
            per_split[item.split].append(item)
    known = ("train", "val", "test")
    order = [s for s in known if s in per_split] + sorted(s for s in per_split if s not in known)
    return Acceptance(
        sampled=len(items),
        undecided=len(items) - len(decided),
        overall=rate(decided),
        by_split={split: rate(per_split[split]) for split in order},
    )
