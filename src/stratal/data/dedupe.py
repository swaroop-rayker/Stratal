"""Duplicate detection (DATA-2.1, DATA-2.2).

An owner unit (ADR-0012), written by the agent at the owner's request on
2026-11-15. The contracts are in each function's docstring and in docs/API.md
§5; the tests are ``tests/unit/test_DATA_2_1_exact_duplicates.py`` and
``tests/unit/test_DATA_2_2_near_duplicates.py``. How and why it works is
explained in ``docs/learning/sys-design.md``.

The clean stage (:mod:`stratal.data.clean`) calls them like this:

1. :func:`group_exact_duplicates` on every row that is still ``accepted``.
2. :func:`perceptual_hash` on each image that is left, through the derivation
   cache, so an image is hashed once per ``(content, hash_size,
   ALGORITHM_VERSION, Pillow version)`` however often the stage runs.
3. :func:`group_near_duplicates` on the hashes of the rows still accepted after
   step 1.

The stage then marks every member of a group except its keeper as
``duplicate`` (CL-17: marked, never deleted) and checks these functions' output
against their contracts before anything is written. Nothing here touches
manifests, the database or events.
"""

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from PIL import Image

ALGORITHM_VERSION = 1
"""Bump this whenever you change what any function in this file returns.

It is part of the clean stage's config, so a new value gives a new version ID
and makes the stage recompute instead of reusing a version or cached pHash
values made by the old code (ARCHITECTURE.md §3, stage memoization).
"""

HIGH_FREQUENCY_FACTOR = 4
"""The image is shrunk to ``hash_size * 4`` pixels a side before the DCT.

The hash keeps only the lowest ``hash_size`` frequencies in each direction, so
the extra resolution is there only to compute those frequencies accurately.
Four is the usual factor (it is what the ``imagehash`` library uses).
"""

_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})


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


# ---------------------------------------------------------------- DATA-2.1


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
       the same groups and the same keepers (NFR-4).
    5. The list itself is sorted by ``keeper``.
    6. It must work for 20,000 items without holding anything but the items and
       the groups in memory (NFR-2).

    How: one pass puts each row key in a bucket keyed by its hash (a dict), so
    the work is linear in the number of rows. Buckets with two or more keys are
    the groups.

    Keeper: the smallest ``row_key``. The files are byte-for-byte identical, so
    no copy is better than another; any rule that does not depend on input
    order would do, and the smallest key is the simplest one to state and to
    check.
    """
    by_hash: defaultdict[str, list[str]] = defaultdict(list)
    for item in items:
        by_hash[item.sha256].append(item.row_key)

    groups: list[DuplicateGroup] = []
    for keys in by_hash.values():
        if len(keys) < 2:
            continue
        members = tuple(sorted(keys))
        groups.append(DuplicateGroup(keeper=members[0], members=members))
    groups.sort(key=lambda group: group.keeper)
    return groups


# ---------------------------------------------------------------- DATA-2.2, the hash


def perceptual_hash(image: Image.Image, *, hash_size: int) -> str:
    """Compute a perceptual hash of ``image`` (DATA-2.2, first half).

    ``image`` is the original file as decoded by Pillow, in whatever mode it was
    stored (RGB, L, RGBA, P, CMYK…). No EXIF rotation has been applied.
    ``hash_size`` comes from the clean stage's config.

    Must satisfy:

    1. Deterministic: the same pixels give the same hash on any machine.
    2. The result is the hash's bits written as lowercase hex, of fixed length
       for a given ``hash_size`` (``hash_size * hash_size`` bits: 16 hex
       characters at 8), so it can be stored in the manifest's ``phash`` column
       and in the derivation cache as text.
    3. Visually identical images give hashes a small Hamming distance apart
       (the number of differing bits), and unrelated images give hashes far
       apart.

    How (the classic DCT pHash, the same steps as the ``imagehash`` library's
    ``phash``, written here without new dependencies):

    1. Greyscale, with transparency flattened onto white and 16-bit or float
       images rescaled to 0–255 (see :func:`_to_grey`). Colour and storage
       format stop mattering.
    2. Shrink to ``hash_size * 4`` pixels a side with Lanczos resampling. Fine
       detail, noise and compression artefacts are averaged away.
    3. Take the two-dimensional discrete cosine transform (DCT-II), but only its
       lowest ``hash_size`` frequencies in each direction: ``hash_size²``
       numbers describing the picture's coarse structure.
    4. One bit per number: 1 if it is above the median of all of them, else 0.
       Comparing with the median makes the hash ignore overall brightness and
       contrast.
    5. Read the bits row by row, most significant first, and write them as hex.

    Re-encoding a picture (JPEG 90 against 70, say) moves those numbers a
    little, so only the few that sit near the median flip.

    Limits: the hash does not survive rotation, mirroring or heavy cropping;
    it is not meant to. Floating-point sums can differ in the last bit between
    platforms, so a number exactly at the median could in principle round the
    other way elsewhere; the derivation cache key includes the Pillow version
    for the same reason (resampling can change between versions).
    """
    if hash_size < 2:
        raise ValueError(f"hash_size must be at least 2, got {hash_size}")
    side = hash_size * HIGH_FREQUENCY_FACTOR
    small = _to_grey(image).resize((side, side), Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]
    pixels = small.tobytes()
    cosines = _cosine_table(hash_size, side)

    # The 2-D DCT is separable: transform every row, then every column of the
    # result. Only the ``hash_size`` lowest frequencies are ever computed.
    # rows[y][v]: frequency v (left to right) of pixel row y.
    rows = [
        [
            sum(c * p for c, p in zip(cosines[v], pixels[y * side : (y + 1) * side], strict=True))
            for v in range(hash_size)
        ]
        for y in range(side)
    ]
    # coefficients[u * hash_size + v]: vertical frequency u, horizontal v.
    coefficients = [
        sum(cosines[u][y] * rows[y][v] for y in range(side))
        for u in range(hash_size)
        for v in range(hash_size)
    ]

    median = statistics.median(coefficients)
    value = 0
    for coefficient in coefficients:
        value = (value << 1) | (coefficient > median)
    return f"{value:0{_hex_width(hash_size * hash_size)}x}"


@lru_cache(maxsize=8)
def _cosine_table(frequencies: int, n: int) -> tuple[tuple[float, ...], ...]:
    """``table[k][x] = cos(pi * k * (2x + 1) / (2n))``: the DCT-II basis, unscaled.

    This is the unnormalised DCT-II, the convention of scipy's ``dct`` with its
    default ``norm=None`` (which ``imagehash`` uses), apart from scipy's factor
    of 2 per axis. A factor shared by every coefficient cannot change which
    ones are above their median. The orthonormal DCT is different: it scales
    frequency 0 by less than the others, which could change the bits of the
    first row and column, so it is not used.
    """
    return tuple(
        tuple(math.cos(math.pi * k * (2 * x + 1) / (2 * n)) for x in range(n))
        for k in range(frequencies)
    )


def _to_grey(image: Image.Image) -> Image.Image:
    """The image as 8-bit greyscale (mode ``L``), whatever mode it was stored in.

    - Transparent pixels are composited onto white, the same rule the
      preprocess stage uses, so a logo stored as a transparent PNG and as a
      flattened JPEG hash alike.
    - 16-bit, 32-bit and float images are rescaled from their own minimum and
      maximum to 0–255. Pillow's plain conversion would clip them at 255, which
      turns most 16-bit images white and would make unrelated ones look alike.
    - Anything Pillow cannot convert to greyscale directly goes through RGBA.
    """
    mode = image.mode
    if mode == "L":
        return image
    if mode in ("I", "F") or mode.startswith("I;16"):
        as_float = image.convert("F")
        # A single-band image gives one (min, max) pair; Pillow's type covers multi-band too.
        low, high = cast(tuple[float, float], as_float.getextrema())
        if not high > low:
            return Image.new("L", image.size, 0)
        scale = 255.0 / (float(high) - float(low))
        offset = -float(low) * scale
        stretched = as_float.point(lambda v: v * scale + offset)  # pyright: ignore[reportUnknownMemberType]
        return stretched.convert("L")
    if mode in _ALPHA_MODES or (mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("L")
    try:
        return image.convert("L")
    except ValueError:
        return image.convert("RGBA").convert("L")


def _hex_width(bits: int) -> int:
    return (bits + 3) // 4


# ---------------------------------------------------------------- DATA-2.2, the groups


def group_near_duplicates(
    items: Sequence[HashedItem], *, max_distance: int
) -> list[DuplicateGroup]:
    """Group near-duplicate images by hash distance and choose keepers (DATA-2.2).

    ``items`` holds every row still accepted after exact duplicates were
    marked, in no particular order. ``max_distance`` comes from the clean
    stage's config; choosing its value from measured distances is the owner's
    (OC-5).

    Must satisfy:

    1. Two items whose hashes are at most ``max_distance`` bits apart (Hamming
       distance of the hex strings read as bits) end up in the same group, and
       two items further apart than that are never grouped because of each
       other alone.
    2. Rules 1, 3, 4 and 5 of :func:`group_exact_duplicates` hold here too: no
       single-member groups, sorted members containing the keeper, a
       deterministic keeper, the list sorted by keeper. A row is in at most one
       group.
    3. For the same picture saved at JPEG quality 90 and 70, exactly one of the
       two stays accepted (DATA-2.2.AC1).
    4. It must finish in reasonable time for 20,000 items.

    Grouping is transitive: if A is near B and B is near C, all three are one
    group even when A and C are further apart than ``max_distance``. Each
    group is a connected component of the "is near" graph, built with a
    union-find. Reasons: copies of copies (re-saved, re-encoded) form chains,
    and every row then belongs to exactly one group, which a non-transitive
    rule cannot promise. The cost is that a long chain could join two images
    that are not alike; with a small threshold (8 of 64 bits) that is rare,
    and the review on SCR-03 shows each group side by side.

    Keeper: the copy with the most pixels, then the larger file (for the same
    picture, usually the higher quality: JPEG 90 over 70), then the smallest
    ``row_key`` so that ties are still decided the same way every time.

    Speed: comparing every pair of 20,000 items is 200 million comparisons.
    Instead, a pigeonhole index: the hash's bits are split into
    ``max_distance + 1`` chunks. Two hashes that differ in at most
    ``max_distance`` bits cannot differ in every chunk, so they agree exactly on
    at least one. Items are bucketed by each chunk's value, and only items that
    share a bucket are compared. For random 64-bit hashes and a threshold of 8
    that is about 13 million cheap comparisons instead of 200 million; similar
    images make buckets larger, but only for the images that are alike.
    """
    if max_distance < 0:
        raise ValueError(f"max_distance must be at least 0, got {max_distance}")
    if len(items) < 2:
        return []
    widths = {len(item.phash) for item in items}
    if len(widths) != 1:
        raise ValueError(f"hashes of different lengths cannot be compared: {sorted(widths)}")
    bits = 4 * widths.pop()
    hashes = [int(item.phash, 16) for item in items]
    sets = _DisjointSets(len(items))

    if max_distance >= bits:
        # Every pair is within the distance: one group of everything.
        for index in range(1, len(items)):
            sets.union(0, index)
    else:
        for start, end in _chunks(bits, max_distance + 1):
            mask = (1 << (end - start)) - 1
            buckets: defaultdict[int, list[int]] = defaultdict(list)
            for index, value in enumerate(hashes):
                buckets[(value >> start) & mask].append(index)
            for bucket in buckets.values():
                _join_near(bucket, hashes, max_distance, sets)

    by_root: defaultdict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        by_root[sets.find(index)].append(index)

    groups: list[DuplicateGroup] = []
    for indices in by_root.values():
        if len(indices) < 2:
            continue
        members = [items[index] for index in indices]
        keeper = min(members, key=_keeper_rank)
        groups.append(
            DuplicateGroup(
                keeper=keeper.row_key,
                members=tuple(sorted(member.row_key for member in members)),
            )
        )
    groups.sort(key=lambda group: group.keeper)
    return groups


def _keeper_rank(item: HashedItem) -> tuple[int, int, str]:
    """Smallest rank is kept: most pixels, then most bytes, then smallest key."""
    return (-(item.width * item.height), -item.size_bytes, item.row_key)


def _chunks(bits: int, count: int) -> list[tuple[int, int]]:
    """Split bit positions ``0 .. bits`` into ``count`` contiguous, nearly equal ranges."""
    edges = [round(i * bits / count) for i in range(count + 1)]
    return [(edges[i], edges[i + 1]) for i in range(count) if edges[i + 1] > edges[i]]


def _join_near(
    bucket: list[int], hashes: list[int], max_distance: int, sets: "_DisjointSets"
) -> None:
    """Union every pair in ``bucket`` whose hashes are within ``max_distance`` bits."""
    for position, first in enumerate(bucket):
        first_hash = hashes[first]
        for second in bucket[position + 1 :]:
            if (first_hash ^ hashes[second]).bit_count() <= max_distance:
                sets.union(first, second)


class _DisjointSets:
    """Union-find over ``0 .. size - 1``, with path halving and union by size."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, item: int) -> int:
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, first: int, second: int) -> None:
        root_a, root_b = self.find(first), self.find(second)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]
