"""DATA-4.2: leakage grouping for the split.

An owner unit, written by the agent at the owner's request on 2026-11-16
(ADR-0012). DATA-4.2.AC2 on the real COCO 10k
version is the split stage's own all-pairs check, tested with the stage and
shown in the M5 demo; here the same property is checked on synthetic hashes.
"""

import hashlib
import io
import random
import time

import pytest
from PIL import Image

from stratal.data.dedupe import perceptual_hash
from stratal.data.splitting import LeakageItem, SplitGroup, group_for_split

LEAKAGE = 12
"""The default leakage distance (DECISIONS.md, M5)."""
BITS = 64


def _sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _hex(value: int) -> str:
    return f"{value:016x}"


def _flip(value: int, bits: int, start: int = 0) -> int:
    """``value`` with ``bits`` consecutive bits flipped, from bit ``start``."""
    return value ^ (((1 << bits) - 1) << start)


def _item(key: str, phash: int | str, dup_group_id: str | None = None) -> LeakageItem:
    text = phash if isinstance(phash, str) else _hex(phash)
    return LeakageItem(key, _sha(key), text, dup_group_id)


def _group_of(groups: list[SplitGroup], key: str) -> SplitGroup:
    found = [group for group in groups if key in group.members]
    assert len(found) == 1, f"{key} is in {len(found)} groups"
    return found[0]


def _same(groups: list[SplitGroup], a: str, b: str) -> bool:
    return _group_of(groups, a) == _group_of(groups, b)


def _picture(seed: int, size: int = 320) -> Image.Image:
    rng = random.Random(seed)
    small = Image.frombytes("RGB", (12, 12), rng.randbytes(12 * 12 * 3))
    return small.resize((size, size), Image.Resampling.BICUBIC)  # pyright: ignore[reportUnknownMemberType]


def _jpeg(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    decoded = Image.open(buffer)
    decoded.load()
    return decoded


def test_DATA_4_2_every_row_is_in_exactly_one_group_singletons_included() -> None:
    rng = random.Random(1)
    items = [_item(f"img_{i:03d}.jpg", rng.getrandbits(BITS)) for i in range(50)]

    groups = group_for_split(items, max_distance=LEAKAGE)

    members = [key for group in groups for key in group.members]
    assert sorted(members) == sorted(item.row_key for item in items)
    assert len(members) == len(set(members))


def test_DATA_4_2_hashes_within_the_leakage_distance_land_in_one_group() -> None:
    """AC1 for "shots of one scene at 10 to 12 bits"; 13 bits is beyond the distance."""
    base = 0x9F3C_0A55_E1D2_7B46
    items = [_item("scene.jpg", base)]
    for bits in (10, 11, 12):
        items.append(_item(f"scene_{bits}bits.jpg", _flip(base, bits)))
    items.append(_item("other_13bits.jpg", _flip(base, 13, start=40)))

    groups = group_for_split(items, max_distance=LEAKAGE)

    for bits in (10, 11, 12):
        assert _same(groups, "scene.jpg", f"scene_{bits}bits.jpg")
    assert not _same(groups, "scene.jpg", "other_13bits.jpg")


def test_DATA_4_2_planted_reencoded_and_resized_copies_land_in_one_group() -> None:
    """AC1 for re-encoded and resized copies, hashed by the real DATA-2.2 hash."""
    original = _picture(7)
    variants = {
        "scene.jpg": original,
        "scene_q60.jpg": _jpeg(original, 60),
        "scene_half.jpg": original.resize((160, 160), Image.Resampling.LANCZOS),  # pyright: ignore[reportUnknownMemberType]
    }
    items = [_item(key, perceptual_hash(image, hash_size=8)) for key, image in variants.items()]
    items += [
        _item(f"unrelated_{seed}.jpg", perceptual_hash(_picture(100 + seed), hash_size=8))
        for seed in range(10)
    ]

    groups = group_for_split(items, max_distance=LEAKAGE)

    assert set(_group_of(groups, "scene.jpg").members) >= set(variants)


def test_DATA_4_2_a_shared_duplicate_group_joins_rows_however_far_apart() -> None:
    items = [
        _item("kept_then.jpg", 0x0000_0000_0000_0000, dup_group_id="dg_aaaaaaaaaaaa"),
        _item("kept_now.jpg", 0xFFFF_FFFF_FFFF_FFFF, dup_group_id="dg_aaaaaaaaaaaa"),
        _item("alone.jpg", 0x00FF_00FF_00FF_00FF, dup_group_id="dg_bbbbbbbbbbbb"),
    ]

    groups = group_for_split(items, max_distance=LEAKAGE)

    assert _same(groups, "kept_then.jpg", "kept_now.jpg")
    assert not _same(groups, "kept_then.jpg", "alone.jpg")


def test_DATA_4_2_grouping_is_transitive_but_needs_a_chain() -> None:
    a = 0x0000_0000_0000_0000
    b = _flip(a, 10)  # 10 from a
    c = _flip(b, 10, start=20)  # 10 from b, 20 from a
    far = _flip(a, 30, start=34)  # 30 from a, b and c alike or more
    items = [_item("a.jpg", a), _item("b.jpg", b), _item("c.jpg", c), _item("far.jpg", far)]

    groups = group_for_split(items, max_distance=LEAKAGE)

    assert _group_of(groups, "a.jpg").members == ("a.jpg", "b.jpg", "c.jpg")
    assert _group_of(groups, "far.jpg").members == ("far.jpg",)


def test_DATA_4_2_anchor_is_the_smallest_sha256_and_the_output_is_sorted() -> None:
    rng = random.Random(2)
    base = rng.getrandbits(BITS)
    items = [_item(f"near_{i}.jpg", _flip(base, i)) for i in range(5)]
    items += [_item(f"img_{i}.jpg", rng.getrandbits(BITS)) for i in range(30)]
    sha_of = {item.row_key: item.sha256 for item in items}

    groups = group_for_split(items, max_distance=LEAKAGE)

    for group in groups:
        assert group.anchor == min(sha_of[key] for key in group.members)
        assert list(group.members) == sorted(group.members)
    assert [group.anchor for group in groups] == sorted(group.anchor for group in groups)


def test_DATA_4_2_order_does_not_matter() -> None:
    rng = random.Random(3)
    base = rng.getrandbits(BITS)
    items = [_item(f"near_{i}.jpg", _flip(base, i, start=i)) for i in range(8)]
    items += [_item(f"img_{i}.jpg", rng.getrandbits(BITS)) for i in range(200)]
    shuffled = list(items)
    rng.shuffle(shuffled)

    assert group_for_split(items, max_distance=LEAKAGE) == group_for_split(
        shuffled, max_distance=LEAKAGE
    )


@pytest.mark.parametrize(
    ("items", "max_distance"),
    [
        ([_item("a.jpg", 1), _item("b.jpg", 2)], -1),
        ([_item("a.jpg", "00ff"), _item("b.jpg", "00ff00ff00ff00ff")], 12),
        ([_item("a.jpg", 1), _item("a.jpg", 2)], 12),
        ([_item("a.jpg", 1), LeakageItem("b.jpg", _sha("a.jpg"), _hex(2), None)], 12),
    ],
    ids=["negative distance", "mixed hash lengths", "repeated row_key", "repeated sha256"],
)
def test_DATA_4_2_bad_inputs_are_refused(items: list[LeakageItem], max_distance: int) -> None:
    with pytest.raises(ValueError):
        group_for_split(items, max_distance=max_distance)


def test_DATA_4_2_no_two_rows_within_the_distance_end_up_in_different_groups() -> None:
    """The property DATA-4.2.AC2 checks on COCO 10k, on 2,000 synthetic hashes."""
    rng = random.Random(4)
    values = [rng.getrandbits(BITS) for _ in range(1_900)]
    values += [_flip(values[i], rng.randint(6, 12), start=rng.randint(0, 40)) for i in range(100)]
    items = [_item(f"img_{i:05d}.jpg", value) for i, value in enumerate(values)]

    groups = group_for_split(items, max_distance=LEAKAGE)

    group_of = {key: group.anchor for group in groups for key in group.members}
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if (values[i] ^ values[j]).bit_count() <= LEAKAGE:
                assert group_of[items[i].row_key] == group_of[items[j].row_key]


def test_DATA_4_2_ten_thousand_rows_finish_well_within_the_limit() -> None:
    """Rule 8: 20,000 rows in under a minute; here half of that in half the time."""
    rng = random.Random(5)
    items = [_item(f"img_{i:05d}.jpg", rng.getrandbits(BITS)) for i in range(10_000)]

    started = time.monotonic()
    groups = group_for_split(items, max_distance=LEAKAGE)

    assert time.monotonic() - started < 30
    assert sum(len(group.members) for group in groups) == 10_000
