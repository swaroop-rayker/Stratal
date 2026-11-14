"""DATA-3.2: version ID derived from content hash and config hash. OWNER'S UNIT.

Every test here is a strict expected-failure until
``stratal.data.versioning.derive_version_identity`` is implemented. When your
implementation makes one pass, pytest reports it as an unexpected pass and
fails the run: that is the signal to delete the ``xfail`` line below, and
then DATA-3.2 can be ticked in docs/MILESTONES.md.

The contract is in the function's docstring and in docs/DATA_MODEL.md §3.
"""

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest

from stratal.data.versioning import VersionIdentity, derive_version_identity
from stratal.storage.manifest import COLUMNS, Row

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="DATA-3.2 is not implemented yet (owner)"
)

HEX64 = re.compile(r"[0-9a-f]{64}")
CONFIG: dict[str, Any] = {"min_size": 256, "nested": {"b": 2, "a": 1}}


def _row(key: str, **values: Any) -> Row:
    row: Row = dict.fromkeys(COLUMNS)
    row.update(row_key=key, sha256=hashlib.sha256(key.encode()).hexdigest(), status="accepted")
    row.update(values)
    return row


def _rows() -> list[Row]:
    return [
        _row("a.png", width=640, height=480, size_bytes=1000),
        _row("b.png", width=300, height=300, size_bytes=2000, caption="a dog", clip_score=0.25),
        _row("c.jpg", status="rejected", decided_at_stage="ingest", reason_code="corrupt_image"),
    ]


def _identity(rows: list[Row], config: dict[str, Any] | None = None) -> VersionIdentity:
    stream: Iterator[Row] = iter(rows)
    return derive_version_identity(stream, CONFIG if config is None else config)


def test_DATA_3_2_same_input_and_config_give_the_same_id() -> None:
    assert _identity(_rows()) == _identity(_rows())


def test_DATA_3_2_identity_has_the_documented_shape() -> None:
    identity = _identity(_rows())

    assert HEX64.fullmatch(identity.content_hash)
    assert HEX64.fullmatch(identity.config_hash)
    assert re.fullmatch(r"ds_[0-9a-f]{6,64}", identity.version_id)


def test_DATA_3_2_row_order_does_not_matter() -> None:
    assert _identity(_rows()) == _identity(list(reversed(_rows())))


def test_DATA_3_2_config_key_order_does_not_matter() -> None:
    reordered = {"nested": {"a": 1, "b": 2}, "min_size": 256}

    assert _identity(_rows(), reordered) == _identity(_rows(), CONFIG)


@pytest.mark.parametrize(
    "change",
    [
        {"width": 641},
        {"status": "rejected", "decided_at_stage": "ingest", "reason_code": "below_min_size"},
        {"caption": "a cat"},
        {"sha256": "f" * 64},
        {"clip_score": 0.26},
        {"size_bytes": None},
    ],
)
def test_DATA_3_2_any_change_to_a_row_gives_a_new_id(change: dict[str, Any]) -> None:
    changed = _rows()
    changed[1].update(change)

    original, altered = _identity(_rows()), _identity(changed)

    assert altered.content_hash != original.content_hash
    assert altered.version_id != original.version_id
    assert altered.config_hash == original.config_hash


def test_DATA_3_2_adding_or_removing_a_row_gives_a_new_id() -> None:
    base = _identity(_rows())

    assert _identity([*_rows(), _row("d.png")]).version_id != base.version_id
    assert _identity(_rows()[:-1]).version_id != base.version_id


def test_DATA_3_2_any_change_to_the_config_gives_a_new_id() -> None:
    base = _identity(_rows())
    changed = _identity(_rows(), {"min_size": 128, "nested": {"b": 2, "a": 1}})

    assert changed.config_hash != base.config_hash
    assert changed.version_id != base.version_id
    assert changed.content_hash == base.content_hash


def test_DATA_3_2_a_value_cannot_move_between_columns_unnoticed() -> None:
    one = _rows()
    one[0].update(caption="x", reason_detail=None)
    other = _rows()
    other[0].update(caption=None, reason_detail="x")

    assert _identity(one).content_hash != _identity(other).content_hash


def test_DATA_3_2_a_value_cannot_move_between_rows_unnoticed() -> None:
    one = _rows()
    one[0]["caption"], one[1]["caption"] = "ab", "c"
    other = _rows()
    other[0]["caption"], other[1]["caption"] = "a", "bc"

    assert _identity(one).content_hash != _identity(other).content_hash


def test_DATA_3_2_empty_manifest_has_an_identity() -> None:
    assert HEX64.fullmatch(_identity([]).content_hash)


def test_DATA_3_2_rows_are_consumed_as_a_stream() -> None:
    """Rule 5: it must work without holding every row. A generator is enough."""
    many = (_row(f"{i:07d}.png", width=i) for i in range(50_000))

    identity = derive_version_identity(many, CONFIG)

    assert HEX64.fullmatch(identity.content_hash)
