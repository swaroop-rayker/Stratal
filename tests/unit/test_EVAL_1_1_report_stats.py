"""EVAL-1.1: the statistics of the phase-1 dataset report. Owner units.

Strict expected failures until the functions in ``stratal.eval.report_stats``
are written (ADR-0012); then remove the marker. EVAL-1.1.AC1 (a report per
dataset version, with all four parts) is tested with the report assembly and
the evaluate stage, which are the agent's.

The Wilson values are from Newcombe, "Two-sided confidence intervals for the
single proportion: comparison of seven methods", Statistics in Medicine 17
(1998), table I, method 3 (score interval without continuity correction).
"""

import math
from collections.abc import Iterator

import pytest

from stratal.eval.report_stats import (
    ACCEPT,
    QUANTILES,
    REJECT,
    RejectionCounts,
    ReviewedItem,
    RowStatus,
    filter_rate,
    rejection_counts,
    review_acceptance,
    score_distribution,
    wilson_interval,
)

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="owner unit EVAL-1.1 not written yet"
)

# --------------------------------------------------------- rejection counts


def _rows() -> list[RowStatus]:
    rows = [RowStatus("accepted", None, None)] * 70
    rows += [RowStatus("rejected", "ingest", "corrupt_image")] * 2
    rows += [RowStatus("rejected", "ingest", "below_min_size")] * 3
    rows += [RowStatus("duplicate", "clean", "exact_duplicate")] * 4
    rows += [RowStatus("duplicate", "clean", "near_duplicate")] * 1
    rows += [RowStatus("filtered", "filter", "clip_below_threshold")] * 18
    rows += [RowStatus("filtered", "filter", "missing_caption")] * 2
    return rows


def test_EVAL_1_1_rejections_are_counted_per_stage_and_reason() -> None:
    counts = rejection_counts(_rows())

    assert counts == RejectionCounts(
        total=100,
        accepted=70,
        by_stage={
            "ingest": {"corrupt_image": 2, "below_min_size": 3},
            "clean": {"exact_duplicate": 4, "near_duplicate": 1},
            "filter": {"clip_below_threshold": 18, "missing_caption": 2},
        },
    )


def test_EVAL_1_1_rejection_counts_read_the_rows_once() -> None:
    def once() -> Iterator[RowStatus]:
        yield from _rows()

    counts = rejection_counts(once())

    assert counts.accepted + sum(n for s in counts.by_stage.values() for n in s.values()) == 100


@pytest.mark.parametrize(
    "row",
    [
        RowStatus("filtered", None, "clip_below_threshold"),
        RowStatus("rejected", "ingest", None),
        RowStatus("lost", "ingest", "corrupt_image"),
    ],
    ids=["no stage", "no reason", "unknown status"],
)
def test_EVAL_1_1_a_row_that_breaks_INV_07_is_refused(row: RowStatus) -> None:
    with pytest.raises(ValueError):
        rejection_counts([RowStatus("accepted", None, None), row])


def test_EVAL_1_1_the_filter_rate_is_over_the_rows_the_filter_saw() -> None:
    counts = rejection_counts(_rows())

    rate = filter_rate(counts)

    assert rate == pytest.approx(
        20 / 90
    )  # 70 kept + 20 filtered; the 10 earlier ones never got there


def test_EVAL_1_1_no_filter_rate_when_nothing_reached_the_filter() -> None:
    counts = RejectionCounts(total=3, accepted=0, by_stage={"ingest": {"corrupt_image": 3}})

    assert filter_rate(counts) is None


# ------------------------------------------------------- score distribution

SCORES = [0.21, 0.24, 0.27, 0.30, 0.33, 0.36, 0.39, 0.42, 0.45, 0.48]
EDGES = (0.2, 0.3, 0.4, 0.5)


def test_EVAL_1_1_the_score_summary_matches_known_values() -> None:
    dist = score_distribution(SCORES, edges=EDGES)

    assert dist.count == 10
    assert dist.mean == pytest.approx(0.345)
    assert dist.std == pytest.approx(0.0908295106, abs=1e-9)  # sample standard deviation
    assert (dist.minimum, dist.maximum) == (0.21, 0.48)
    assert list(dist.quantiles) == [name for name, _ in QUANTILES]
    expected = {"p05": 0.2235, "p25": 0.2775, "p50": 0.345, "p75": 0.4125, "p95": 0.4665}
    for name, value in expected.items():
        assert dist.quantiles[name] == pytest.approx(value, abs=1e-12), name


def test_EVAL_1_1_histogram_bins_are_half_open_except_the_last() -> None:
    dist = score_distribution([*SCORES, 0.5, 0.1, 0.6], edges=EDGES)

    assert dist.edges == EDGES
    assert dist.histogram == (3, 4, 4)  # 0.30 opens the second bin; 0.5 closes the last
    assert (dist.below, dist.above) == (1, 1)
    assert sum(dist.histogram) + dist.below + dist.above == dist.count


def test_EVAL_1_1_one_score_and_no_scores() -> None:
    one = score_distribution([0.3], edges=EDGES)
    none = score_distribution([], edges=EDGES)

    assert one.std == 0.0
    assert one.quantiles["p05"] == one.quantiles["p95"] == 0.3
    assert none.count == 0
    assert (none.mean, none.std, none.minimum, none.maximum) == (None, None, None, None)
    assert none.quantiles == {}
    assert none.histogram == (0, 0, 0)


@pytest.mark.parametrize(
    ("scores", "edges"),
    [([0.3, math.nan], EDGES), ([0.3, math.inf], EDGES), ([0.3], (0.2,)), ([0.3], (0.2, 0.4, 0.4))],
    ids=["nan score", "infinite score", "one edge", "edges not increasing"],
)
def test_EVAL_1_1_bad_scores_or_edges_are_refused(
    scores: list[float], edges: tuple[float, ...]
) -> None:
    with pytest.raises(ValueError):
        score_distribution(scores, edges=edges)


# ---------------------------------------------------------- Wilson interval


@pytest.mark.parametrize(
    ("successes", "trials", "low", "high"),
    [
        (81, 263, 0.2553, 0.3662),
        (15, 148, 0.0624, 0.1605),
        (0, 20, 0.0, 0.1611),
        (1, 29, 0.0061, 0.1718),
    ],
)
def test_EVAL_1_1_wilson_matches_newcombe_1998(
    successes: int, trials: int, low: float, high: float
) -> None:
    interval = wilson_interval(successes, trials)

    assert interval.low == pytest.approx(low, abs=5e-5)
    assert interval.high == pytest.approx(high, abs=5e-5)
    assert interval.estimate == pytest.approx(successes / trials)
    assert interval.confidence == 0.95


def test_EVAL_1_1_wilson_uses_the_exact_normal_quantile_not_1_96() -> None:
    interval = wilson_interval(81, 263)

    # With z = 1.96 these would be 0.25528761 and 0.36621068.
    assert interval.low == pytest.approx(0.25528852, abs=2e-7)
    assert interval.high == pytest.approx(0.36620958, abs=2e-7)


def test_EVAL_1_1_wilson_at_another_confidence() -> None:
    interval = wilson_interval(81, 263, confidence=0.90)

    assert (interval.low, interval.high) == (
        pytest.approx(0.26331362, abs=2e-7),
        pytest.approx(0.35656635, abs=2e-7),
    )
    assert interval.confidence == 0.90


def test_EVAL_1_1_wilson_stays_inside_0_and_1_at_the_extremes() -> None:
    """The review's usual case: 50 rows, nearly all accepted."""
    all_accepted = wilson_interval(50, 50)
    none_accepted = wilson_interval(0, 50)

    assert all_accepted.high == 1.0
    assert all_accepted.low == pytest.approx(0.9287, abs=5e-5)
    assert none_accepted.low == 0.0
    assert wilson_interval(48, 50).low == pytest.approx(0.8654, abs=5e-5)
    assert wilson_interval(48, 50).high == pytest.approx(0.9890, abs=5e-5)


@pytest.mark.parametrize(
    ("successes", "trials", "confidence"),
    [(0, 0, 0.95), (6, 5, 0.95), (-1, 5, 0.95), (3, 5, 1.0), (3, 5, 0.0)],
    ids=["no trials", "more successes than trials", "negative", "confidence 1", "confidence 0"],
)
def test_EVAL_1_1_wilson_refuses_impossible_counts(
    successes: int, trials: int, confidence: float
) -> None:
    with pytest.raises(ValueError):
        wilson_interval(successes, trials, confidence=confidence)


# -------------------------------------------------------- review acceptance


def test_EVAL_1_1_review_acceptance_overall_and_for_the_test_split() -> None:
    """DATA-4.7.AC1's figures: acceptance overall and for ``test``."""
    items = [ReviewedItem("train", ACCEPT)] * 38 + [ReviewedItem("train", REJECT)] * 2
    items += [ReviewedItem("test", ACCEPT)] * 4 + [ReviewedItem("test", REJECT)] * 1
    items += [ReviewedItem("val", ACCEPT)] * 3 + [ReviewedItem("val", None)] * 2

    acceptance = review_acceptance(items)

    assert (acceptance.sampled, acceptance.undecided) == (50, 2)
    assert (acceptance.overall.decided, acceptance.overall.accepted) == (48, 45)
    assert acceptance.overall.interval == wilson_interval(45, 48)
    assert set(acceptance.by_split) == {"train", "val", "test"}
    test = acceptance.by_split["test"]
    assert (test.decided, test.accepted) == (5, 4)
    assert test.interval is not None
    assert test.interval.low == pytest.approx(0.37553463, abs=2e-7)
    assert test.interval.high == pytest.approx(0.96377589, abs=2e-7)


def test_EVAL_1_1_review_acceptance_without_splits_or_decisions() -> None:
    unsplit = review_acceptance([ReviewedItem(None, ACCEPT), ReviewedItem(None, REJECT)])
    undecided = review_acceptance([ReviewedItem("test", None)])

    assert unsplit.by_split == {}
    assert unsplit.overall.interval == wilson_interval(1, 2)
    assert undecided.overall.decided == 0
    assert undecided.overall.interval is None
    assert undecided.by_split == {}


def test_EVAL_1_1_review_acceptance_refuses_unknown_decisions() -> None:
    with pytest.raises(ValueError):
        review_acceptance([ReviewedItem("test", "maybe")])
