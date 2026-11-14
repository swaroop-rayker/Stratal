"""Version identity (DATA-3.2). OWNER-IMPLEMENTED.

The agent does not implement :func:`derive_version_identity`. The contract is
below and in docs/DATA_MODEL.md §3; the tests that define "done" are in
``tests/unit/test_DATA_3_2_version_identity.py``.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from stratal.storage.manifest import Row


@dataclass(frozen=True)
class VersionIdentity:
    content_hash: str
    """64 lowercase hex characters, derived from the manifest's logical content."""
    config_hash: str
    """64 lowercase hex characters, derived from the resolved stage config."""
    version_id: str
    """``ds_`` followed by a hex prefix. Its length is the owner's call (OC-4)."""


VersionDeriver = Callable[[Iterator[Row], Mapping[str, Any]], VersionIdentity]
"""Anything with this shape can stand in for the real derivation."""


def derive_version_identity(rows: Iterator[Row], config: Mapping[str, Any]) -> VersionIdentity:
    """Derive a dataset version's identity from its rows and its config.

    ``rows`` streams the manifest's rows (every column, nulls included) and may
    be consumed only once. ``config`` is the fully resolved stage config as
    JSON-compatible values.

    The result must satisfy (DATA-3.2.AC1, docs/DATA_MODEL.md §3):

    1. The same rows and the same config always give the same identity, on any
       machine and in any process.
    2. Any change to a row, to the set of rows, or to the config gives a
       different identity.
    3. The order in which rows arrive does not matter. Neither does the order
       of keys inside ``config``.
    4. ``content_hash`` depends only on the rows; ``config_hash`` only on the
       config; ``version_id`` on both.
    5. It must work on a manifest too large to hold in memory.
    """
    raise NotImplementedError(
        "DATA-3.2 version identity is owner-implemented and has not been written yet. "
        "See src/stratal/data/versioning.py and tests/unit/test_DATA_3_2_version_identity.py."
    )
