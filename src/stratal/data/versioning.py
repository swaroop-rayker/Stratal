"""Version identity (DATA-3.2).

A dataset version is identified by what it contains and how it was made:

    content_hash  from the manifest's rows
    config_hash   from the resolved stage config
    version_id    from both

The contract is in docs/DATA_MODEL.md §3 and in the tests in
``tests/unit/test_DATA_3_2_version_identity.py``.

How it works
------------
Each row is turned into bytes in one fixed way (:func:`_encode_row`) and hashed
with SHA-256. The row digests are then *added together* as 256-bit integers.
Addition does not care about order, so the rows may arrive in any order, and
only one running total is kept, so a manifest of any size can be hashed as a
stream. The final content hash covers that total and the number of rows.

Two limits worth knowing:

* Adding digests is safe against accidents, not against an attacker who builds
  rows on purpose to collide. Stratal is a single-user local tool (SPEC §5),
  so that is acceptable here.
* A null value and an absent column mean the same thing. That is deliberate:
  adding a new, empty column to the manifest schema later does not change the
  identity of any existing version.
"""

import hashlib
import json
import math
import struct
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from stratal.storage.manifest import Row

VERSION_ID_HEX_LENGTH = 12
"""Hex characters after ``ds_`` (OC-4). 48 bits: a collision among a million
versions has a probability of about one in 560."""

_MODULUS = 1 << 256
_ROWS_DOMAIN = b"stratal/content/v1\x00"
_CONFIG_DOMAIN = b"stratal/config/v1\x00"
_VERSION_DOMAIN = b"stratal/version/v1\x00"


@dataclass(frozen=True)
class VersionIdentity:
    content_hash: str
    """64 lowercase hex characters, derived from the manifest's logical content."""
    config_hash: str
    """64 lowercase hex characters, derived from the resolved stage config."""
    version_id: str
    """``ds_`` followed by the first :data:`VERSION_ID_HEX_LENGTH` hex characters."""


VersionDeriver = Callable[[Iterator[Row], Mapping[str, Any]], VersionIdentity]
"""Anything with this shape can stand in for the real derivation."""


def _framed(payload: bytes) -> bytes:
    """Prefix ``payload`` with its length, so two fields can never run together."""
    return len(payload).to_bytes(8, "big") + payload


def _encode_value(value: Any) -> bytes:
    """One value as a type tag plus bytes. The tag keeps 1, 1.0, True and "1" apart."""
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return b"b" + (b"\x01" if value else b"\x00")
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii")
    if isinstance(value, float):
        if math.isnan(value):
            return b"f" + b"nan"
        # 0.0 and -0.0 compare equal, so they must hash equal.
        return b"f" + struct.pack(">d", value + 0.0)
    if isinstance(value, str):
        return b"s" + value.encode("utf-8")
    raise TypeError(f"cannot hash a manifest value of type {type(value).__name__}: {value!r}")


def _encode_row(row: Mapping[str, Any]) -> bytes:
    """A row as bytes: its non-null columns in name order, each framed."""
    parts: list[bytes] = []
    for name in sorted(row):
        value = row[name]
        if value is None:
            continue
        parts.append(_framed(name.encode("utf-8")))
        parts.append(_framed(_encode_value(value)))
    return b"".join(parts)


def _content_hash(rows: Iterator[Row]) -> str:
    total = 0
    count = 0
    for row in rows:
        digest = hashlib.sha256(_ROWS_DOMAIN + _encode_row(row)).digest()
        total = (total + int.from_bytes(digest, "big")) % _MODULUS
        count += 1
    final = hashlib.sha256(_ROWS_DOMAIN)
    final.update(count.to_bytes(8, "big"))
    final.update(total.to_bytes(32, "big"))
    return final.hexdigest()


def _config_hash(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(_CONFIG_DOMAIN + canonical.encode("utf-8")).hexdigest()


def derive_version_identity(rows: Iterator[Row], config: Mapping[str, Any]) -> VersionIdentity:
    """Derive a dataset version's identity from its rows and its config.

    ``rows`` streams the manifest's rows and is consumed once. ``config`` is
    the fully resolved stage config as JSON-compatible values.

    Guarantees (DATA-3.2.AC1, docs/DATA_MODEL.md §3):

    1. The same rows and config always give the same identity, in any process
       on any machine.
    2. Any change to a row, to the set of rows, or to the config gives a
       different identity.
    3. Row order does not matter. Neither does key order inside ``config``.
    4. ``content_hash`` depends only on the rows, ``config_hash`` only on the
       config, ``version_id`` on both.
    5. Memory use does not grow with the number of rows.

    Raises ``TypeError`` for a row value that is not null, bool, int, float or
    str, and ``ValueError`` for a config that holds NaN or infinity.
    """
    content_hash = _content_hash(rows)
    config_hash = _config_hash(config)
    combined = hashlib.sha256(
        _VERSION_DOMAIN + content_hash.encode("ascii") + b":" + config_hash.encode("ascii")
    ).hexdigest()
    return VersionIdentity(content_hash, config_hash, f"ds_{combined[:VERSION_ID_HEX_LENGTH]}")
