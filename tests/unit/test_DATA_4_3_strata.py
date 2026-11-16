"""DATA-4.3: each row's stratum.

An owner unit, written by the agent at the owner's request on 2026-11-16
(ADR-0012). DATA-4.3.AC1 (each split within one group
of its target in every stratum) is tested with the assignment, in
``test_DATA_4_split_assignment.py``.
"""

import math
import random

import pytest

from stratal.data.splitting import (
    DEFAULT_STRATUM_KEYS,
    NO_STRATUM,
    StratumItem,
    StratumKey,
    strata_for,
)

QUARTILE = StratumKey.CLIP_QUARTILE
ORIENT = StratumKey.ORIENTATION
FOLDER = StratumKey.SOURCE_FOLDER


def _item(key: str, score: float | None = 0.3, width: int = 640, height: int = 480) -> StratumItem:
    return StratumItem(key, score, width, height)


def test_DATA_4_3_the_default_keys_are_quartile_orientation_and_folder_in_that_order() -> None:
    items = [_item("train2017/a.jpg", 0.30, 640, 480), _item("val2017/b.jpg", 0.30, 480, 640)]

    strata = strata_for(items, keys=DEFAULT_STRATUM_KEYS)

    assert strata["train2017/a.jpg"].split("|")[1:] == ["landscape", "train2017"]
    assert strata["val2017/b.jpg"].split("|")[1:] == ["portrait", "val2017"]
    assert strata["train2017/a.jpg"].split("|")[0] in {"q1", "q2", "q3", "q4"}


def test_DATA_4_3_quartiles_split_at_interpolated_percentiles_ties_going_down() -> None:
    # Scores 1..8 tenths: boundaries 0.275, 0.45, 0.625 ("inclusive" quartiles).
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    items = [_item(f"{i}.jpg", score) for i, score in enumerate(scores)]

    strata = strata_for(items, keys=[QUARTILE])

    assert [strata[f"{i}.jpg"] for i in range(8)] == [
        "q1",
        "q1",
        "q2",
        "q2",
        "q3",
        "q3",
        "q4",
        "q4",
    ]


def test_DATA_4_3_a_score_on_a_boundary_belongs_to_the_lower_quartile() -> None:
    # Scores 0.1 to 0.5: the boundaries are 0.2, 0.3 and 0.4 exactly.
    items = [_item(f"{i}.jpg", score) for i, score in enumerate([0.1, 0.2, 0.3, 0.4, 0.5])]

    strata = strata_for(items, keys=[QUARTILE])

    assert [strata[f"{i}.jpg"] for i in range(5)] == ["q1", "q1", "q2", "q3", "q4"]


def test_DATA_4_3_orientation_compares_the_upright_sides() -> None:
    items = [_item("w.jpg", width=640, height=480), _item("t.jpg", width=480, height=640)]
    items.append(_item("s.jpg", width=500, height=500))

    assert strata_for(items, keys=[ORIENT]) == {
        "w.jpg": "landscape",
        "t.jpg": "portrait",
        "s.jpg": "square",
    }


def test_DATA_4_3_the_folder_is_the_first_path_component_or_a_dot() -> None:
    items = [_item("train2017/sub/a.jpg"), _item("b.jpg"), _item("odd|name/c.jpg")]

    assert strata_for(items, keys=[FOLDER]) == {
        "train2017/sub/a.jpg": "train2017",
        "b.jpg": ".",
        "odd|name/c.jpg": "odd_name",
    }


def test_DATA_4_3_keys_follow_the_given_order_and_none_means_one_stratum() -> None:
    items = [_item("x/a.jpg", width=480, height=640)]

    assert strata_for(items, keys=[FOLDER, ORIENT]) == {"x/a.jpg": "x|portrait"}
    assert strata_for(items, keys=[]) == {"x/a.jpg": NO_STRATUM}


def test_DATA_4_3_a_missing_score_is_refused_only_when_quartiles_are_used() -> None:
    items = [_item("a.jpg", 0.3), _item("b.jpg", None), _item("c.jpg", math.nan)]

    with pytest.raises(ValueError, match=r"b\.jpg|c\.jpg"):
        strata_for(items, keys=[QUARTILE])
    assert set(strata_for(items, keys=[ORIENT])) == {"a.jpg", "b.jpg", "c.jpg"}


def test_DATA_4_3_a_key_given_twice_is_refused() -> None:
    with pytest.raises(ValueError):
        strata_for([_item("a.jpg")], keys=[ORIENT, ORIENT])


def test_DATA_4_3_order_does_not_matter() -> None:
    rng = random.Random(6)
    items = [
        _item(f"f{i % 3}/{i}.jpg", rng.random(), rng.choice([480, 640]), rng.choice([480, 640]))
        for i in range(300)
    ]
    shuffled = list(items)
    rng.shuffle(shuffled)

    assert strata_for(items, keys=DEFAULT_STRATUM_KEYS) == strata_for(
        shuffled, keys=DEFAULT_STRATUM_KEYS
    )
