"""DATA-4.1, DATA-4.3.AC1, DATA-4.4, DATA-4.5: giving each split group a split.

An owner unit, written by the agent at the owner's request on 2026-11-16
(ADR-0012). The stage-level halves of these criteria (manifests, version IDs, the
config refusing bad proportions, the carry-over lookup and the list of moved
rows) are the agent's and are tested with the split stage.

"Within one group" below is the contract's tolerance: a count differs from its
unrounded target by less than the size of the largest group in that stratum,
or in the whole set for the totals.
"""

import hashlib
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

import pytest

from stratal.data.splitting import (
    SPLITS,
    TEST,
    TRAIN,
    VAL,
    GroupToAssign,
    Member,
    Proportions,
    assign_splits,
    targets,
)

DEFAULT = Proportions()
SEED = 20261115


def _anchor(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _group(
    keys: Sequence[str], stratum: str, previous: Mapping[str, str] | None = None
) -> GroupToAssign:
    known = previous or {}
    members = tuple(Member(key, known.get(key)) for key in sorted(keys))
    return GroupToAssign(_anchor(min(keys)), stratum, members)


def _population(sizes: Mapping[str, int], *, seed: int, prefix: str = "img") -> list[GroupToAssign]:
    """Groups for each stratum: mostly single rows, about 2 % in groups of 2 to 4,
    like the measured COCO rows (docs/drafts/DATA-4_v1.md)."""
    rng = random.Random(seed)
    groups: list[GroupToAssign] = []
    for stratum, rows in sizes.items():
        left = rows
        index = 0
        while left:
            size = min(left, rng.choice([2, 3, 4]) if rng.random() < 0.01 else 1)
            keys = [f"{prefix}/{stratum}/{index + k:05d}.jpg" for k in range(size)]
            groups.append(_group(keys, stratum))
            index += size
            left -= size
    return groups


def _counts(
    groups: Sequence[GroupToAssign], result: Mapping[str, str]
) -> tuple[dict[str, Counter[str]], Counter[str]]:
    per_stratum: dict[str, Counter[str]] = defaultdict(Counter)
    total: Counter[str] = Counter()
    for group in groups:
        per_stratum[group.stratum][result[group.anchor]] += len(group.members)
        total[result[group.anchor]] += len(group.members)
    return per_stratum, total


def _assert_within_one_group(
    groups: Sequence[GroupToAssign], result: Mapping[str, str], proportions: Proportions
) -> None:
    per_stratum, total = _counts(groups, result)
    for stratum, counts in per_stratum.items():
        members = [g for g in groups if g.stratum == stratum]
        largest = max(len(g.members) for g in members)
        rows = sum(len(g.members) for g in members)
        for split, target in targets(rows, proportions).items():
            assert abs(counts[split] - target) < largest, (stratum, split, counts, target)
    largest = max(len(g.members) for g in groups)
    rows = sum(len(g.members) for g in groups)
    for split, target in targets(rows, proportions).items():
        assert abs(total[split] - target) < largest, ("total", split, total, target)


# ------------------------------------------------------------------ DATA-4.1

MEASURED = {
    f"q{q}|{o}": rows
    for (q, o), rows in zip(
        [(q, o) for q in range(1, 5) for o in ("landscape", "portrait", "square")],
        [543, 180, 30, 520, 190, 41, 498, 205, 44, 510, 160, 37],
        strict=True,
    )
}
"""Twelve strata of 30 to 543 rows, as measured on 2,958 labelled COCO rows."""


def test_DATA_4_1_every_group_gets_exactly_one_split() -> None:
    groups = _population(MEASURED, seed=1)

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    assert set(result) == {group.anchor for group in groups}
    assert set(result.values()) <= set(SPLITS)


@pytest.mark.parametrize("proportions", [DEFAULT, Proportions(0.7, 0.15, 0.15)])
def test_DATA_4_1_each_split_total_is_within_one_group_of_its_target(
    proportions: Proportions,
) -> None:
    groups = _population(MEASURED, seed=2)

    result = assign_splits(groups, proportions=proportions, seed=SEED)

    _assert_within_one_group(groups, result, proportions)


def test_DATA_4_1_totals_and_strata_round_together_when_every_group_is_one_row() -> None:
    """48 strata of 37 rows: each stratum's targets are 3.7 / 3.7 / 29.6, the totals
    177.6 / 177.6 / 1420.8. Rounding each stratum on its own misses the totals."""
    sizes = {f"s{index:02d}": 37 for index in range(48)}
    groups = [_group([f"{s}/{i:02d}.jpg"], s) for s, rows in sizes.items() for i in range(rows)]

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    _assert_within_one_group(groups, result, DEFAULT)


@pytest.mark.parametrize(
    "proportions",
    [Proportions(0.9, 0.2, -0.1), Proportions(0.8, 0.1, 0.0), Proportions(0.8, 0.2, 0.1)],
    ids=["negative", "sums to 0.9", "sums to 1.1"],
)
def test_DATA_4_1_bad_proportions_are_refused(proportions: Proportions) -> None:
    with pytest.raises(ValueError):
        assign_splits(_population({"all": 20}, seed=3), proportions=proportions, seed=SEED)


@pytest.mark.parametrize(
    "groups",
    [
        [_group(["a.jpg"], "all"), _group(["a.jpg"], "all")],
        [GroupToAssign(_anchor("a.jpg"), "all", ())],
        [_group(["a.jpg"], "all", {"a.jpg": "holdout"})],
    ],
    ids=["repeated anchor", "no members", "unknown previous split"],
)
def test_DATA_4_1_bad_groups_are_refused(groups: list[GroupToAssign]) -> None:
    with pytest.raises(ValueError):
        assign_splits(groups, proportions=DEFAULT, seed=SEED)


def test_DATA_4_1_splits_do_not_follow_row_key_order() -> None:
    """Rule 7: within a stratum, the split must not be a cut through the sorted keys."""
    groups = [_group([f"{i:04d}.jpg"], "all") for i in range(1_000)]

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    by_key = [result[group.anchor] for group in groups]
    for split in (VAL, TEST):
        positions = [i for i, s in enumerate(by_key) if s == split]
        deciles = {position // 100 for position in positions}
        assert len(deciles) >= 6, f"{split} rows sit in only {sorted(deciles)}"
        first_half = sum(1 for position in positions if position < 500)
        assert 25 <= first_half <= 75, (
            f"{first_half} of {len(positions)} {split} rows in the first half"
        )


# ------------------------------------------------------------------ DATA-4.3


def test_DATA_4_3_every_stratum_is_within_one_group_of_its_targets() -> None:
    """AC1 on the measured mix of strata, small strata included."""
    groups = _population(MEASURED, seed=4)

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    per_stratum, _ = _counts(groups, result)
    assert set(per_stratum) == set(MEASURED)
    _assert_within_one_group(groups, result, DEFAULT)


def test_DATA_4_3_strata_that_are_one_group_each_still_meet_the_totals() -> None:
    """A group too big for every quota of its stratum must overshoot one split.

    Ten strata, each one group of 5 rows: each stratum's targets are 4 / 0.5 /
    0.5, which no group of 5 fits. Any split keeps the stratum within one group,
    but the totals (40 / 5 / 5) are met only if the overshoots are spread:
    8 strata to train, 1 to val, 1 to test.
    """
    groups = [_group([f"s{s}/{i}.jpg" for i in range(5)], f"s{s:02d}") for s in range(10)]

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    _assert_within_one_group(groups, result, DEFAULT)


def test_DATA_4_3_groups_too_big_for_their_stratum_are_balanced_with_the_rest() -> None:
    """Twelve tiny strata of 7 rows, each a group of 6 and one single row, next to
    strata of ordinary size: the overshoots must not pile up in one split."""
    groups = [_group([f"tiny{s}/{i}.jpg" for i in range(6)], f"tiny{s:02d}") for s in range(12)]
    groups += [_group([f"tiny{s}/single.jpg"], f"tiny{s:02d}") for s in range(12)]
    groups += _population({"big_a": 400, "big_b": 250}, seed=11)

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    _assert_within_one_group(groups, result, DEFAULT)


# ------------------------------------------------------------------ DATA-4.4


def test_DATA_4_4_the_same_groups_in_any_order_give_the_same_splits() -> None:
    groups = _population(MEASURED, seed=5)
    shuffled = list(groups)
    random.Random(5).shuffle(shuffled)

    first = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    assert assign_splits(shuffled, proportions=DEFAULT, seed=SEED) == first
    assert assign_splits(groups, proportions=DEFAULT, seed=SEED) == first


def test_DATA_4_4_a_different_seed_changes_the_assignments() -> None:
    groups = _population(MEASURED, seed=6)

    first = assign_splits(groups, proportions=DEFAULT, seed=SEED)
    other = assign_splits(groups, proportions=DEFAULT, seed=SEED + 1)

    changed = sum(1 for anchor in first if first[anchor] != other[anchor])
    assert changed >= len(groups) // 10
    _assert_within_one_group(groups, other, DEFAULT)


# ------------------------------------------------------------------ DATA-4.5

_STRATA = (
    *(f"{folder}|{shape}" for folder in "abc" for shape in ("landscape", "portrait")),
    "d|landscape",
    "d|square",
    "d|portrait",
)
OLD = dict(zip(_STRATA, [2_000, 1_500, 1_500, 1_200, 1_000, 800, 600, 500, 400], strict=True))
NEW = dict(zip(_STRATA, [100, 80, 80, 60, 50, 40, 40, 30, 20], strict=True))


def _carried(groups: Sequence[GroupToAssign], result: Mapping[str, str]) -> list[GroupToAssign]:
    return [
        GroupToAssign(
            group.anchor,
            group.stratum,
            tuple(Member(m.row_key, result[group.anchor]) for m in group.members),
        )
        for group in groups
    ]


def test_DATA_4_5_existing_rows_keep_their_split_when_500_are_added() -> None:
    """AC1: split 9,500 rows, add 500 from the same mix, split again."""
    assert sum(OLD.values()) == 9_500
    assert sum(NEW.values()) == 500
    old = _population(OLD, seed=7)
    first = assign_splits(old, proportions=DEFAULT, seed=SEED)
    everything = _carried(old, first) + _population(NEW, seed=8, prefix="added")

    second = assign_splits(everything, proportions=DEFAULT, seed=SEED)

    assert all(second[group.anchor] == first[group.anchor] for group in old)
    _assert_within_one_group(everything, second, DEFAULT)


def test_DATA_4_5_a_new_row_that_joins_a_group_takes_the_group_s_split() -> None:
    groups = [_group(["old.jpg", "new.jpg"], "all", {"old.jpg": TEST})]
    groups += _population({"all": 99}, seed=9)

    result = assign_splits(groups, proportions=DEFAULT, seed=SEED)

    assert result[groups[0].anchor] == TEST


def test_DATA_4_5_a_group_joining_two_splits_goes_to_the_side_with_more_members() -> None:
    """AC2's decision; the shell then lists the moved rows."""
    previous = {"t1.jpg": TRAIN, "t2.jpg": TRAIN, "t3.jpg": TRAIN, "x1.jpg": TEST}
    joined = _group([*previous, "bridge.jpg"], "all", previous)

    result = assign_splits(
        [joined, *_population({"all": 95}, seed=10)], proportions=DEFAULT, seed=SEED
    )

    assert result[joined.anchor] == TRAIN


@pytest.mark.parametrize(
    ("previous", "proportions", "expected"),
    [
        ({"v.jpg": VAL, "t.jpg": TEST}, DEFAULT, VAL),
        ({"r.jpg": TRAIN, "t.jpg": TEST}, DEFAULT, TRAIN),
        ({"v.jpg": VAL, "t.jpg": TEST}, Proportions(0.6, 0.1, 0.3), TEST),
    ],
    ids=["equal shares: earlier name", "larger share", "larger share, not name order"],
)
def test_DATA_4_5_a_tied_merge_goes_to_the_larger_proportion_then_the_earlier_name(
    previous: dict[str, str], proportions: Proportions, expected: str
) -> None:
    joined = _group([*previous, "bridge.jpg"], "all", previous)

    result = assign_splits([joined], proportions=proportions, seed=SEED)

    assert result[joined.anchor] == expected


@pytest.mark.parametrize("heavy_share", [0.0, 0.3, 1.0], ids=["realistic", "mixed", "all heavy"])
def test_DATA_4_1_random_mixes_stay_within_one_group(heavy_share: float) -> None:
    """Rules 4 and 6 on 100 seeded random mixes of strata each: sizes from 1 to
    300 rows, and a share of "heavy" strata made mostly of groups of 2 to 6."""
    for trial in range(100):
        rng = random.Random(trial)
        groups: list[GroupToAssign] = []
        for stratum in range(rng.randint(1, 40)):
            left = rng.choice([1, 3, 5, 8, 20, 60, 300])
            heavy = rng.random() < heavy_share
            index = 0
            while left:
                size = 1
                if heavy or rng.random() < 0.02:
                    size = min(left, rng.choice([2, 3, 4, 5, 6]))
                keys = [f"t{trial}/s{stratum}/{index + k}.jpg" for k in range(size)]
                groups.append(_group(keys, f"s{stratum:02d}"))
                index += size
                left -= size

        result = assign_splits(groups, proportions=DEFAULT, seed=trial)

        _assert_within_one_group(groups, result, DEFAULT)
