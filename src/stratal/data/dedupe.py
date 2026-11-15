"""Duplicate detection (DATA-2.1, DATA-2.2). OWNER-IMPLEMENTED.

The agent does not implement the three functions below. Their contracts are in
their docstrings and in docs/API.md §5; the tests that define "done" are in
``tests/unit/test_DATA_2_1_exact_duplicates.py`` and
``tests/unit/test_DATA_2_2_near_duplicates.py``.

The clean stage (:mod:`stratal.data.clean`, agent's plumbing) calls them like this:

1. :func:`group_exact_duplicates` on every row that is still ``accepted``.
2. :func:`perceptual_hash` on each image that is left, through the derivation
   cache, so an image is hashed once per ``(content, hash_size,
   ALGORITHM_VERSION)`` however often the stage runs.
3. :func:`group_near_duplicates` on the hashes of the rows still accepted after
   step 1.

The stage then marks every member of a group except its keeper as
``duplicate`` (CL-17: marked, never deleted), checks your output against the
contract, and fails the stage with a clear message if it does not hold. You do
not touch manifests, the database or events.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image

ALGORITHM_VERSION = 1
"""Bump this whenever you change what any function in this file returns.

It is part of the clean stage's config, so a new value gives a new version ID
and makes the stage recompute instead of reusing a version or cached pHash
values made by the old code (ARCHITECTURE.md §3, stage memoization).
"""


@dataclass(frozen=True)
class ExactItem:
    """One accepted row, as the exact-duplicate step sees it."""

    row_key: str
    sha256: str
    """SHA-256 of the original file's bytes (64 lowercase hex)."""


@dataclass(frozen=True)
class HashedItem:
    """One accepted row with its perceptual hash, as the near-duplicate step sees it."""

    row_key: str
    phash: str
    """What :func:`perceptual_hash` returned for this row's image."""
    width: int
    height: int
    size_bytes: int


@dataclass(frozen=True)
class DuplicateGroup:
    """Rows that are copies of each other, and the one to keep."""

    keeper: str
    """``row_key`` of the row that stays accepted."""
    members: tuple[str, ...]
    """Every ``row_key`` in the group, the keeper included, sorted ascending."""


def group_exact_duplicates(items: Sequence[ExactItem]) -> list[DuplicateGroup]:
    """Group byte-identical files and choose one keeper per group (DATA-2.1).

    ``items`` holds every row still accepted when the clean stage starts, in no
    particular order. Two items are duplicates when their ``sha256`` is equal.

    Must satisfy:

    1. Only groups of two or more rows are returned. A row with a unique hash
       is in no group.
    2. Every row whose hash occurs more than once is in exactly one group, with
       every other row of that hash.
    3. ``members`` is sorted ascending and includes ``keeper``.
    4. The keeper is chosen deterministically: the same items in any order give
       the same groups and the same keepers (NFR-4). Which member you keep is
       your decision; say why in the docstring when you write it.
    5. The list itself is sorted by ``keeper``.
    6. It must work for 20,000 items without holding anything but the items and
       the groups in memory (NFR-2).
    """
    raise NotImplementedError(
        "DATA-2.1 exact-duplicate grouping is owner-implemented and has not been written yet. "
        "See src/stratal/data/dedupe.py and tests/unit/test_DATA_2_1_exact_duplicates.py."
    )


def perceptual_hash(image: Image.Image, *, hash_size: int) -> str:
    """Compute a perceptual hash of ``image`` (DATA-2.2, first half).

    ``image`` is the original file as decoded by Pillow, in whatever mode it was
    stored (RGB, L, RGBA, P, CMYK…). No EXIF rotation has been applied.
    ``hash_size`` comes from the clean stage's config.

    Must satisfy:

    1. Deterministic: the same pixels give the same hash on any machine.
    2. The result is the hash's bits written as lowercase hex, of fixed length
       for a given ``hash_size`` (``hash_size * hash_size`` bits for a classic
       pHash: 16 hex characters at 8), so it can be stored in the manifest's
       ``phash`` column and in the derivation cache as text.
    3. Visually identical images give hashes a small Hamming distance apart
       (the number of differing bits), and unrelated images give hashes far
       apart.

    You may use the ``imagehash`` library (named in SPEC §7.2) or write the DCT
    yourself. Adding the library is a dependency change: say so and it will be
    locked and built in.
    """
    raise NotImplementedError(
        "DATA-2.2 perceptual hashing is owner-implemented and has not been written yet. "
        "See src/stratal/data/dedupe.py and tests/unit/test_DATA_2_2_near_duplicates.py."
    )


def group_near_duplicates(
    items: Sequence[HashedItem], *, max_distance: int
) -> list[DuplicateGroup]:
    """Group near-duplicate images by hash distance and choose keepers (DATA-2.2).

    ``items`` holds every row still accepted after exact duplicates were
    marked, in no particular order. ``max_distance`` comes from the clean
    stage's config; choosing its value from measured distances is yours (OC-5).

    Must satisfy:

    1. Two items whose hashes are at most ``max_distance`` bits apart (Hamming
       distance of the hex strings read as bits) end up in the same group, and
       two items further apart than that are never grouped because of each
       other alone. Whether grouping is transitive (A near B and B near C puts
       A, B and C together even if A and C are far apart) is your decision;
       document it.
    2. Rules 1, 3, 4 and 5 of :func:`group_exact_duplicates` hold here too: no
       single-member groups, sorted members containing the keeper, a
       deterministic keeper, the list sorted by keeper. A row is in at most one
       group.
    3. For the same picture saved at JPEG quality 90 and 70, exactly one of the
       two stays accepted (DATA-2.2.AC1). Which one is your call; ``size_bytes``,
       ``width`` and ``height`` are given in case you want to keep the better
       copy. The tests check only that one is kept.
    4. It must finish in reasonable time for 20,000 items. Comparing every pair
       is 200 million comparisons: say in the docstring how you avoid that, or
       why it is acceptable.
    """
    raise NotImplementedError(
        "DATA-2.2 near-duplicate grouping is owner-implemented and has not been written yet. "
        "See src/stratal/data/dedupe.py and tests/unit/test_DATA_2_2_near_duplicates.py."
    )
