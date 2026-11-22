"""Semantic duplicates: clusters of images whose CLIP vectors are close (DATA-2.3).

ADR-0013: found in the label stage, which already holds every accepted image's CLIP
vector for LABEL-1.3. Rows in one cluster stay accepted (``semantic_action: mark``,
the default) and share ``semantic_cluster_id``, which the split uses to keep them in
one split group (DATA-4.8). The threshold is the owner's choice from spike S-10:
0.90 (OC-5, 2026-11-21).

A unit the owner handed to the agent (ADR-0012's log, 2026-11-20): contract first,
then strict expected-failure tests, then the code.

Tests: ``tests/unit/test_DATA_2_3_semantic_clusters.py``.
"""

import base64
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass

ALGORITHM_VERSION = 1
"""Bump when the clusters for the same vectors and threshold would change."""

DEFAULT_THRESHOLD = 0.90
"""Spike S-10, chosen by the owner on 2026-11-21 (ADR-0013, "The threshold")."""


@dataclass(frozen=True)
class EmbeddedItem:
    """One accepted image, as the clustering sees it."""

    sha256: str
    """SHA-256 of the original file. Unique among accepted rows (INV-05)."""
    vector: Sequence[float]
    """Its CLIP image embedding, not normalised."""


@dataclass(frozen=True)
class SemanticCluster:
    cluster_id: str
    """The smallest ``sha256`` among the members (ADR-0013 rule 3)."""
    members: tuple[str, ...]
    """The members' ``sha256``, sorted ascending; at least two."""


def semantic_clusters(items: Sequence[EmbeddedItem], *, threshold: float) -> list[SemanticCluster]:
    """Group images whose CLIP vectors are at least ``threshold`` alike (DATA-2.3).

    Must satisfy:

    1. Two items are *linked* when the cosine similarity of their vectors,
       ``a·b / (|a| |b|)``, is at least ``threshold``.
    2. Clusters are the connected components of the links (union-find, as DATA-4.2
       does for hashes): if A links B and B links C, all three are one cluster, even
       when A and C are not alike. Two items are never in one cluster unless a chain
       of links joins them.
    3. Only clusters of two or more are returned; an item with no link is in none.
    4. ``cluster_id`` is the smallest ``sha256`` among a cluster's members;
       ``members`` is sorted ascending; the list is sorted by ``cluster_id``.
    5. The same items in any order give the same result (NFR-4).
    6. ``ValueError`` when ``threshold`` is not in (0, 1], when the vectors do not
       all have the same length, when a vector is all zeros or not finite, or when a
       ``sha256`` occurs twice.
    7. It must finish for 20,000 vectors of 512 numbers in under a minute in the GPU
       worker (ADR-0013: one matrix product in blocks). Without PyTorch (the dev image
       and its tests) a plain-Python path gives the same answer for small inputs.

    How: the vectors are normalised once, so a dot product is the cosine. Items are
    sorted by ``sha256`` first, which makes every later step independent of the input
    order (rule 5). Pairs at or above the threshold are joined in a union-find; its
    components of two or more are the clusters (rules 2 and 3).

    Why a matrix product: 20,000 vectors are 200 million pairs. On the GPU a block of
    2,048 rows against all of them is one product of 2,048 × 512 by 512 × 20,000, and
    only the links (a few hundred at 0.90, spike S-10) come back to Python. The
    plain-Python path compares every pair and is for tests and tiny inputs only.
    """
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"threshold must be in (0, 1], got {threshold}")
    seen: set[str] = set()
    for item in items:
        if item.sha256 in seen:
            raise ValueError(f"sha256 {item.sha256!r} occurs more than once")
        seen.add(item.sha256)
    ordered = sorted(items, key=lambda item: item.sha256)
    widths = {len(item.vector) for item in ordered}
    if len(widths) > 1:
        raise ValueError(f"vectors of different lengths cannot be compared: {sorted(widths)}")
    unit: list[list[float]] = []
    for item in ordered:
        values = [float(v) for v in item.vector]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"the vector of {item.sha256[:12]}… is not finite")
        length = math.sqrt(sum(v * v for v in values))
        if length == 0.0:
            raise ValueError(f"the vector of {item.sha256[:12]}… is all zeros")
        unit.append([v / length for v in values])
    if len(unit) < 2:
        return []

    sets = _DisjointSets(len(unit))
    for i, j in _links(unit, threshold):
        sets.union(i, j)
    members: dict[int, list[int]] = {}
    for index in range(len(unit)):
        members.setdefault(sets.find(index), []).append(index)
    clusters = [
        SemanticCluster(
            cluster_id=ordered[min(indices)].sha256,  # ordered by sha256: the first is the smallest
            members=tuple(ordered[i].sha256 for i in sorted(indices)),
        )
        for indices in members.values()
        if len(indices) > 1
    ]
    return sorted(clusters, key=lambda cluster: cluster.cluster_id)


def _links(unit: list[list[float]], threshold: float) -> list[tuple[int, int]]:
    """Every pair ``i < j`` whose normalised vectors' dot product is at least ``threshold``:
    blocked matrix products with PyTorch (the GPU worker), else every pair in Python."""
    try:
        from stratal.label import semantic_torch
    except ImportError:
        return [
            (i, j)
            for i in range(len(unit))
            for j in range(i + 1, len(unit))
            if sum(a * b for a, b in zip(unit[i], unit[j], strict=True)) >= threshold
        ]
    return semantic_torch.links(unit, threshold)


class _DisjointSets:
    """Union-find with path halving and union by size (as DATA-4.2's)."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, index: int) -> int:
        parent = self._parent
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]


def pack_vector(vector: Sequence[float]) -> str:
    """A vector as float16, base64: about 1 KB for 512 numbers (ADR-0013 rule 2). The
    derivation cache stores JSON, so the bytes travel as text."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def unpack_vector(text: str) -> list[float]:
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """``a·b / (|a| |b|)``, for one pair (the cluster drawer, SCR-03 v2)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
