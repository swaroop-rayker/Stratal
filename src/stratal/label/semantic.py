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
    """
    raise NotImplementedError("DATA-2.3: semantic_clusters (agent, at the owner's request)")


def pack_vector(vector: Sequence[float]) -> str:
    """A vector as float16, base64: about 1 KB for 512 numbers (ADR-0013 rule 2). The
    derivation cache stores JSON, so the bytes travel as text."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def unpack_vector(text: str) -> list[float]:
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))
