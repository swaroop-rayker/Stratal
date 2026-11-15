"""DATA-2.2: near duplicates are removed (marked, CL-17) by perceptual hash. An owner unit.

Written as strict expected-failures before the unit existed; the markers were
removed on 2026-11-15 when ``stratal.data.dedupe.perceptual_hash`` and
``group_near_duplicates`` were written (by the agent, at the owner's request:
ADR-0012).

The threshold used is the clean stage's default ``phash_max_distance``. It is
provisional; choosing it from measured distances is the owner's (OC-5). If the
default changes, these tests follow it.
"""

import io
import random
import time
from pathlib import Path

from PIL import Image

from stratal.data.clean import CleanConfig, DedupeUnits
from stratal.data.dedupe import (
    DuplicateGroup,
    HashedItem,
    group_exact_duplicates,
    group_near_duplicates,
    perceptual_hash,
)
from stratal.testing import stagekit
from stratal.testing.fakes import no_exact_duplicates
from stratal.testing.synthetic import make_image_folder

DEFAULTS = CleanConfig()
SIZE = DEFAULTS.phash_hash_size
THRESHOLD = DEFAULTS.phash_max_distance


def _picture(seed: int, size: int = 256) -> Image.Image:
    """A photo-like picture: smooth colour blobs, different for every seed."""
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


def _distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _item(key: str, phash: str, size_bytes: int = 1000) -> HashedItem:
    return HashedItem(key, phash, 256, 256, size_bytes)


def _hex(bits: int) -> str:
    return f"{bits:0{SIZE * SIZE // 4}x}"


def test_DATA_2_2_phash_is_deterministic_and_fixed_length_lowercase_hex() -> None:
    hashes = [perceptual_hash(_picture(seed), hash_size=SIZE) for seed in range(5)]

    assert hashes[0] == perceptual_hash(_picture(0), hash_size=SIZE)
    assert len({len(h) for h in hashes}) == 1
    assert all(h == h.lower() and int(h, 16) >= 0 for h in hashes)


def test_DATA_2_2_phash_accepts_every_stored_mode() -> None:
    picture = _picture(1)
    for mode in ("RGB", "L", "RGBA", "P", "CMYK"):
        assert perceptual_hash(picture.convert(mode), hash_size=SIZE)


def test_DATA_2_2_jpeg_90_and_70_of_one_picture_hash_close_and_others_far() -> None:
    picture = _picture(3)
    high = perceptual_hash(_jpeg(picture, 90), hash_size=SIZE)
    low = perceptual_hash(_jpeg(picture, 70), hash_size=SIZE)
    others = [perceptual_hash(_picture(100 + s), hash_size=SIZE) for s in range(20)]

    assert _distance(high, low) <= THRESHOLD
    assert all(_distance(high, other) > THRESHOLD for other in others)


def test_DATA_2_2_jpeg_90_and_70_pair_is_grouped_and_one_kept() -> None:
    picture = _picture(4)
    items = [
        _item("pair_q90.jpg", perceptual_hash(_jpeg(picture, 90), hash_size=SIZE), 9000),
        _item("pair_q70.jpg", perceptual_hash(_jpeg(picture, 70), hash_size=SIZE), 5000),
        _item("other.jpg", perceptual_hash(_picture(5), hash_size=SIZE)),
    ]

    groups = group_near_duplicates(items, max_distance=THRESHOLD)

    assert len(groups) == 1
    assert groups[0].members == ("pair_q70.jpg", "pair_q90.jpg")
    assert groups[0].keeper in groups[0].members


def test_DATA_2_2_threshold_is_respected_exactly() -> None:
    base = (1 << (SIZE * SIZE - 1)) | 0b1011
    at_limit = base ^ ((1 << THRESHOLD) - 1)  # exactly THRESHOLD bits differ
    beyond = base ^ ((1 << (THRESHOLD + 1)) - 1)  # one more
    near = [_item("a.png", _hex(base)), _item("b.png", _hex(at_limit))]
    far = [_item("a.png", _hex(base)), _item("c.png", _hex(beyond))]

    assert [g.members for g in group_near_duplicates(near, max_distance=THRESHOLD)] == [
        ("a.png", "b.png")
    ]
    assert group_near_duplicates(far, max_distance=THRESHOLD) == []


def test_DATA_2_2_groups_have_the_shared_shape_and_ignore_input_order() -> None:
    rng = random.Random(11)
    items: list[HashedItem] = []
    for i in range(30):
        bits = rng.getrandbits(SIZE * SIZE)
        items.append(_item(f"x_{i:02d}.png", _hex(bits)))
        if i % 3 == 0:
            items.append(_item(f"y_{i:02d}.png", _hex(bits ^ 0b1)))  # one bit away
    shuffled = list(items)
    rng.shuffle(shuffled)

    groups = group_near_duplicates(items, max_distance=THRESHOLD)

    assert groups == group_near_duplicates(shuffled, max_distance=THRESHOLD)
    assert len(groups) == 10
    _check_shape(groups)


def _check_shape(groups: list[DuplicateGroup]) -> None:
    for group in groups:
        assert len(group.members) >= 2
        assert list(group.members) == sorted(set(group.members))
        assert group.keeper in group.members
    keepers = [g.keeper for g in groups]
    assert keepers == sorted(keepers)
    members = [m for g in groups for m in g.members]
    assert len(members) == len(set(members)), "a row is in at most one group"


def test_DATA_2_2_twenty_thousand_hashes_group_in_reasonable_time() -> None:
    rng = random.Random(5)
    items = [_item(f"r_{i:05d}.png", _hex(rng.getrandbits(SIZE * SIZE))) for i in range(20000)]
    started = time.monotonic()

    group_near_duplicates(items, max_distance=THRESHOLD)

    assert time.monotonic() - started < 60.0


def test_DATA_2_2_clean_stage_keeps_one_of_each_jpeg_pair(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=20, jpeg_pairs=10)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    units = DedupeUnits(
        exact=no_exact_duplicates, phash=perceptual_hash, near=group_near_duplicates
    )

    result = stagekit.clean(data, raw, units)
    rows = stagekit.rows_of(data, result.record)

    for high, low in folder.jpeg_pairs:
        assert {rows[high]["status"], rows[low]["status"]} == {"accepted", "duplicate"}
        assert rows[high]["dup_group_id"] == rows[low]["dup_group_id"] is not None
        assert {rows[high]["dup_kind"], rows[low]["dup_kind"]} == {"near"}
    assert sum(1 for r in rows.values() if r["reason_code"] == "near_duplicate") == 10
    assert all(rows[key]["status"] == "accepted" for key in folder.valid)
    assert all(rows[key]["phash"] for key in rows)


def test_DATA_2_2_with_2_1_clean_stage_handles_copies_and_pairs_together(
    tmp_path: Path,
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=20, exact_copies=5, jpeg_pairs=5)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    units = DedupeUnits(
        exact=group_exact_duplicates, phash=perceptual_hash, near=group_near_duplicates
    )

    result = stagekit.clean(data, raw, units)

    assert result.metrics["duplicates_exact"] == 5
    assert result.metrics["duplicates_near"] == 5
    assert result.metrics["accepted"] == 20 + 5
