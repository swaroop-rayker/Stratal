"""DATA-2.1: byte-identical duplicates are removed (marked, CL-17). OWNER'S UNIT.

Every test here is a strict expected-failure until
``stratal.data.dedupe.group_exact_duplicates`` is implemented. When your
implementation makes them pass, pytest reports unexpected passes and fails the
run: that is the signal to delete the ``xfail`` line below, and then DATA-2.1
can be ticked in docs/MILESTONES.md.

The contract is in the function's docstring. The last test runs the whole clean
stage with your function and fakes for the two DATA-2.2 functions, so DATA-2.1
can pass before DATA-2.2 exists.
"""

import hashlib
import random
import time
from pathlib import Path

import pytest

from stratal.data.clean import DedupeUnits
from stratal.data.dedupe import DuplicateGroup, ExactItem, group_exact_duplicates
from stratal.testing import stagekit
from stratal.testing.fakes import fake_perceptual_hash, no_near_duplicates
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="DATA-2.1 is not implemented yet (owner)"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _items(copies: int, uniques: int) -> list[ExactItem]:
    """``copies`` originals each with one copy, plus ``uniques`` files with no copy."""
    items: list[ExactItem] = []
    for i in range(copies):
        items.append(ExactItem(f"valid/img_{i:05d}.png", _sha(f"picture {i}")))
        items.append(ExactItem(f"copies/copy_{i:03d}.png", _sha(f"picture {i}")))
    for i in range(uniques):
        items.append(ExactItem(f"other/u_{i:03d}.png", _sha(f"unique {i}")))
    return items


def _check_shape(groups: list[DuplicateGroup]) -> None:
    for group in groups:
        assert len(group.members) >= 2
        assert list(group.members) == sorted(set(group.members))
        assert group.keeper in group.members
    keepers = [g.keeper for g in groups]
    assert keepers == sorted(keepers)


def test_DATA_2_1_twenty_planted_copies_give_twenty_groups_with_one_keeper_each() -> None:
    groups = group_exact_duplicates(_items(copies=20, uniques=10))

    _check_shape(groups)
    assert len(groups) == 20
    assert all(len(g.members) == 2 for g in groups)
    in_groups = [m for g in groups for m in g.members]
    assert len(in_groups) == len(set(in_groups)) == 40, "every copy and original, once"


def test_DATA_2_1_files_with_unique_content_are_in_no_group() -> None:
    groups = group_exact_duplicates(_items(copies=0, uniques=15))

    assert groups == []


def test_DATA_2_1_three_identical_files_form_one_group() -> None:
    items = [ExactItem(key, _sha("same")) for key in ("c.png", "a.png", "b.png")]
    items.append(ExactItem("d.png", _sha("other")))

    groups = group_exact_duplicates(items)

    assert len(groups) == 1
    assert groups[0].members == ("a.png", "b.png", "c.png")


def test_DATA_2_1_input_order_changes_neither_groups_nor_keepers() -> None:
    items = _items(copies=12, uniques=8)
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)

    assert group_exact_duplicates(shuffled) == group_exact_duplicates(items)


def test_DATA_2_1_empty_input_gives_no_groups() -> None:
    assert group_exact_duplicates([]) == []


def test_DATA_2_1_twenty_thousand_items_in_a_few_seconds() -> None:
    items = _items(copies=5000, uniques=10000)
    started = time.monotonic()

    groups = group_exact_duplicates(items)

    assert len(groups) == 5000
    assert time.monotonic() - started < 5.0


def test_DATA_2_1_clean_stage_marks_planted_copies_and_keeps_one_per_group(
    tmp_path: Path,
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=25, exact_copies=20)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    units = DedupeUnits(
        exact=group_exact_duplicates, phash=fake_perceptual_hash, near=no_near_duplicates
    )

    result = stagekit.clean(data, raw, units)
    rows = stagekit.rows_of(data, result.record)

    duplicates = [r for r in rows.values() if r["status"] == "duplicate"]
    assert len(duplicates) == 20
    assert all(r["reason_code"] == "exact_duplicate" for r in duplicates)
    for copy, original in folder.exact_copies:
        pair = {rows[copy]["status"], rows[original]["status"]}
        assert pair == {"accepted", "duplicate"}, "one kept per group"
        assert rows[copy]["dup_group_id"] == rows[original]["dup_group_id"] is not None
        loser = rows[copy] if rows[copy]["status"] == "duplicate" else rows[original]
        assert loser["dup_of"] in (copy, original) and loser["dup_of"] != loser["row_key"]
    assert result.metrics["duplicates_exact"] == 20
    assert len(rows) == 45, "rows are marked, never removed (CL-17, INV-06)"
