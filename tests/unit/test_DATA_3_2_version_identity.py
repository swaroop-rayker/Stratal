"""DATA-3.2: version ID derived from content hash and config hash.

The contract is in the function's docstring and in docs/DATA_MODEL.md §3.
"""

import hashlib
import re
from collections.abc import Iterator
from typing import Any

import pytest

from stratal.data.versioning import VersionIdentity, derive_version_identity
from stratal.storage.manifest import COLUMNS, Row

HEX64 = re.compile(r"[0-9a-f]{64}")
KNOWN_VERSION_ID = "ds_804a1a43ff19"
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


def test_DATA_3_2_version_id_is_ds_plus_twelve_hex() -> None:
    assert re.fullmatch(r"ds_[0-9a-f]{12}", _identity(_rows()).version_id)


def test_DATA_3_2_known_answer_pins_the_algorithm() -> None:
    """If this changes, every stored version ID changes. Do not update it casually."""
    identity = _identity(
        [{"row_key": "a.png", "status": "accepted", "width": 640}], {"min_size": 256}
    )

    assert identity.version_id == KNOWN_VERSION_ID


def test_DATA_3_2_null_and_absent_columns_are_the_same() -> None:
    with_nulls = _rows()
    without = [{k: v for k, v in row.items() if v is not None} for row in _rows()]

    assert _identity(with_nulls) == _identity(without)


def test_DATA_3_2_values_of_different_types_do_not_collide() -> None:
    hashes = {
        _identity([_row("a.png", reason_detail=value)]).content_hash
        for value in (1, 1.0, True, "1")
    }

    assert len(hashes) == 4


def test_DATA_3_2_duplicate_rows_do_not_cancel_out() -> None:
    one, two = [_row("a.png")], [_row("a.png"), _row("a.png")]

    assert (
        len({_identity([]).content_hash, _identity(one).content_hash, _identity(two).content_hash})
        == 3
    )


def test_DATA_3_2_positive_and_negative_zero_are_equal() -> None:
    assert _identity([_row("a.png", clip_score=0.0)]) == _identity([_row("a.png", clip_score=-0.0)])


def test_DATA_3_2_unsupported_value_types_are_refused() -> None:
    with pytest.raises(TypeError, match="cannot hash"):
        _identity([_row("a.png", caption=["a", "list"])])


def test_DATA_3_2_config_with_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        _identity(_rows(), {"threshold": float("nan")})
