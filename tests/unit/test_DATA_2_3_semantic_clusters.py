"""DATA-2.3 (ADR-0013): semantic duplicates as clusters of close CLIP vectors, and DATA-4.8:
a cluster joins one split group.

Written first as strict expected failures (the working model's order, ADR-0012); the markers
came off with the code.
The vectors are small and hand-made: what is checked is the contract, not CLIP. A pipeline
job runs it in ``tests/integration/test_M11_semantic.py``; real CLIP vectors were measured in
spike S-10.
"""

import hashlib
import math
import random

import pytest

from stratal.data.splitting import LeakageItem, group_for_split
from stratal.label.semantic import (
    DEFAULT_THRESHOLD,
    EmbeddedItem,
    SemanticCluster,
    pack_vector,
    semantic_clusters,
    unpack_vector,
)


def _sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _unit(angle_deg: float, length: float = 10.0) -> list[float]:
    """A 3-number vector at ``angle_deg`` from the x axis, in the x-y plane."""
    a = math.radians(angle_deg)
    return [length * math.cos(a), length * math.sin(a), 0.0]


def _item(key: str, vector: list[float]) -> EmbeddedItem:
    return EmbeddedItem(_sha(key), vector)


# cos(20°) = 0.940, cos(30°) = 0.866: at 0.90 the first is linked, the second is not.
def test_DATA_2_3_cropped_variants_are_grouped_together() -> None:
    items = [
        _item("photo.jpg", _unit(0)),
        _item("photo_crop80.jpg", _unit(20, length=7.0)),  # length does not matter
        _item("unrelated.jpg", _unit(90)),
    ]
    clusters = semantic_clusters(items, threshold=DEFAULT_THRESHOLD)
    pair = sorted([_sha("photo.jpg"), _sha("photo_crop80.jpg")])
    assert clusters == [SemanticCluster(cluster_id=pair[0], members=tuple(pair))]


def test_DATA_2_3_threshold_is_inclusive_and_below_it_nothing_links() -> None:
    a, b = _item("a", _unit(0)), _item("b", _unit(30))
    exact = math.cos(math.radians(30))
    assert semantic_clusters([a, b], threshold=round(exact, 6) - 1e-9) != []
    assert semantic_clusters([a, b], threshold=0.90) == []


def test_DATA_2_3_clusters_chain_through_links() -> None:
    # 0°, 20°, 40°: neighbours are 0.94 alike, the ends only 0.77; one cluster by the chain.
    items = [
        _item("a", _unit(0)),
        _item("b", _unit(20)),
        _item("c", _unit(40)),
        _item("far", _unit(120)),
    ]
    (cluster,) = semantic_clusters(items, threshold=0.90)
    assert set(cluster.members) == {_sha("a"), _sha("b"), _sha("c")}
    assert cluster.cluster_id == min(cluster.members)
    assert list(cluster.members) == sorted(cluster.members)


def test_DATA_2_3_order_does_not_matter() -> None:
    rng = random.Random(7)
    items = [_item(f"img{i}", [rng.gauss(0, 1) for _ in range(16)]) for i in range(60)]
    # plant three near copies
    for k in range(3):
        base = list(items[k].vector)
        items.append(_item(f"copy{k}", [v + rng.gauss(0, 0.05) for v in base]))
    first = semantic_clusters(items, threshold=0.95)
    shuffled = items[:]
    rng.shuffle(shuffled)
    assert semantic_clusters(shuffled, threshold=0.95) == first
    assert len(first) >= 3 and first == sorted(first, key=lambda c: c.cluster_id)


@pytest.mark.parametrize(
    ("items", "threshold"),
    [
        ([("a", [1.0, 0.0]), ("b", [0.0, 1.0])], 0.0),
        ([("a", [1.0, 0.0]), ("b", [0.0, 1.0])], 1.5),
        ([("a", [1.0, 0.0]), ("b", [0.0, 1.0, 0.0])], 0.9),
        ([("a", [0.0, 0.0]), ("b", [0.0, 1.0])], 0.9),
        ([("a", [float("nan"), 0.0]), ("b", [0.0, 1.0])], 0.9),
        ([("a", [1.0, 0.0]), ("a", [0.0, 1.0])], 0.9),
    ],
)
def test_DATA_2_3_bad_input_is_refused(
    items: list[tuple[str, list[float]]], threshold: float
) -> None:
    with pytest.raises(ValueError):
        semantic_clusters([_item(k, v) for k, v in items], threshold=threshold)


def test_DATA_2_3_a_lone_or_empty_input_gives_no_cluster() -> None:
    assert semantic_clusters([], threshold=0.9) == []
    assert semantic_clusters([_item("a", _unit(0))], threshold=0.9) == []


def test_DATA_2_3_vectors_pack_as_float16() -> None:
    vector = [0.5, -1.25, 3.0, 65504.0]
    text = pack_vector(vector)
    assert unpack_vector(text) == vector
    assert len(pack_vector([0.1] * 512)) <= 1400, "about 1 KB for 512 numbers"


def test_DATA_4_8_a_semantic_cluster_joins_one_split_group() -> None:
    # Two photos of one scene from different angles: 64 bits apart by pHash, one cluster by CLIP.
    one = LeakageItem("scene_left.jpg", _sha("left"), f"{0:016x}", None, semantic_cluster_id="sc1")
    two = LeakageItem(
        "scene_right.jpg", _sha("right"), f"{2**64 - 1:016x}", None, semantic_cluster_id="sc1"
    )
    other = LeakageItem("other.jpg", _sha("other"), f"{0x00FF00FF00FF00FF:016x}", None)
    groups = group_for_split([one, two, other], max_distance=12)
    together = [g for g in groups if "scene_left.jpg" in g.members]
    assert together and "scene_right.jpg" in together[0].members
    assert all("other.jpg" not in g.members for g in together)
