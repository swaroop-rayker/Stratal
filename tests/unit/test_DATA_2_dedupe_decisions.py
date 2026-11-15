"""DATA-2.1 and DATA-2.2: the design choices made when the units were written.

The contract tests are in ``test_DATA_2_1_exact_duplicates.py`` and
``test_DATA_2_2_near_duplicates.py``. These pin the decisions the contract left
open (recorded in DECISIONS.md): which copy is kept, transitive grouping, how
unusual image modes are hashed, and the edges of the threshold.
"""

import io
import random

import pytest
from PIL import Image

from stratal.data.clean import CleanConfig
from stratal.data.dedupe import (
    ExactItem,
    HashedItem,
    group_exact_duplicates,
    group_near_duplicates,
    perceptual_hash,
)


def _distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _picture(seed: int, size: int = 256) -> Image.Image:
    rng = random.Random(seed)
    small = Image.frombytes("RGB", (12, 12), rng.randbytes(12 * 12 * 3))
    return small.resize((size, size), Image.Resampling.BICUBIC)  # pyright: ignore[reportUnknownMemberType]


def _item(
    key: str, bits: int, *, width: int = 640, height: int = 480, size: int = 1000
) -> HashedItem:
    return HashedItem(key, f"{bits:016x}", width, height, size)


def test_DATA_2_1_keeper_is_the_smallest_row_key() -> None:
    items = [ExactItem(key, "a" * 64) for key in ("valid/b.png", "copies/z.png", "valid/a.png")]

    (group,) = group_exact_duplicates(items)

    assert group.keeper == "copies/z.png"


def test_DATA_2_2_keeper_has_most_pixels_then_most_bytes_then_smallest_key() -> None:
    base = 0x8F3C_0000_1234_5678
    bigger = _item("small/big.png", base, width=1280, height=960, size=900)
    heavier = _item("a/heavy.jpg", base ^ 0b1, size=5000)
    lighter = _item("a/light.jpg", base ^ 0b11, size=3000)

    (group,) = group_near_duplicates([lighter, heavier, bigger], max_distance=8)
    (pair,) = group_near_duplicates([lighter, heavier], max_distance=8)
    (tie,) = group_near_duplicates(
        [_item("b.jpg", base), _item("a.jpg", base ^ 0b1)], max_distance=8
    )

    assert group.keeper == "small/big.png", "most pixels first"
    assert pair.keeper == "a/heavy.jpg", "then the larger file (JPEG 90 over 70)"
    assert tie.keeper == "a.jpg", "then the smallest key"


def test_DATA_2_2_grouping_is_transitive() -> None:
    base = 0x8000_0000_0000_0000
    a = _item("a.png", base)
    b = _item("b.png", base ^ 0xFF)  # 8 bits from a
    c = _item("c.png", base ^ 0xFFFF)  # 8 bits from b, 16 from a

    groups = group_near_duplicates([c, a, b], max_distance=8)

    assert [g.members for g in groups] == [("a.png", "b.png", "c.png")]


def test_DATA_2_2_threshold_edges() -> None:
    base = 0x8000_0000_0000_0001
    same = [_item("a.png", base), _item("b.png", base)]
    one_off = [_item("a.png", base), _item("b.png", base ^ 0b10)]
    opposite = [_item("a.png", base), _item("b.png", base ^ (2**64 - 1))]

    assert len(group_near_duplicates(same, max_distance=0)) == 1
    assert group_near_duplicates(one_off, max_distance=0) == []
    assert len(group_near_duplicates(opposite, max_distance=64)) == 1, "64 bits apart, limit 64"
    assert group_near_duplicates([_item("a.png", base)], max_distance=8) == []
    with pytest.raises(ValueError, match="at least 0"):
        group_near_duplicates(same, max_distance=-1)


def test_DATA_2_2_default_threshold_is_the_owners_choice() -> None:
    config = CleanConfig()

    assert (config.phash_hash_size, config.phash_max_distance) == (8, 8), "OC-5, chosen 2026-11-15"


def test_DATA_2_2_hashes_of_different_lengths_are_refused() -> None:
    items = [HashedItem("a.png", "0f" * 8, 1, 1, 1), HashedItem("b.png", "0f" * 32, 1, 1, 1)]

    with pytest.raises(ValueError, match="different lengths"):
        group_near_duplicates(items, max_distance=8)


@pytest.mark.parametrize(("hash_size", "hex_length"), [(4, 4), (5, 7), (8, 16), (16, 64)])
def test_DATA_2_2_hash_has_hash_size_squared_bits(hash_size: int, hex_length: int) -> None:
    value = perceptual_hash(_picture(1), hash_size=hash_size)

    assert len(value) == hex_length
    assert int(value, 16) < 2 ** (hash_size * hash_size)


def test_DATA_2_2_a_transparent_copy_hashes_like_its_flattened_jpeg() -> None:
    logo = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    logo.paste(_picture(7, 160).convert("RGBA"), (48, 48))
    flattened = Image.new("RGB", logo.size, (255, 255, 255))
    flattened.paste(logo, mask=logo.getchannel("A"))
    buffer = io.BytesIO()
    flattened.save(buffer, format="JPEG", quality=85)
    jpeg = Image.open(io.BytesIO(buffer.getvalue()))

    assert _distance(perceptual_hash(logo, hash_size=8), perceptual_hash(jpeg, hash_size=8)) <= 4


def test_DATA_2_2_sixteen_bit_images_are_rescaled_not_clipped() -> None:
    eight_bit = _picture(9).convert("L")
    sixteen_bit = eight_bit.convert("I").point(lambda v: v * 257).convert("I;16")  # pyright: ignore[reportUnknownMemberType]
    other = _picture(10).convert("L").convert("I").point(lambda v: v * 257).convert("I;16")  # pyright: ignore[reportUnknownMemberType]

    same_picture = _distance(
        perceptual_hash(eight_bit, hash_size=8), perceptual_hash(sixteen_bit, hash_size=8)
    )
    different = _distance(
        perceptual_hash(sixteen_bit, hash_size=8), perceptual_hash(other, hash_size=8)
    )

    assert same_picture <= 2, "a 16-bit copy hashes like the 8-bit original"
    assert different > 8, "two different 16-bit images do not collapse to one white hash"


def test_DATA_2_2_a_resized_copy_stays_near() -> None:
    picture = _picture(12, 512)

    distance = _distance(
        perceptual_hash(picture, hash_size=8),
        perceptual_hash(picture.resize((300, 300), Image.Resampling.LANCZOS), hash_size=8),  # pyright: ignore[reportUnknownMemberType]
    )

    assert distance <= 4
