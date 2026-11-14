"""Deterministic synthetic image folders with planted problems.

The acceptance criteria of DATA-1 and DATA-2 are phrased as "N planted X";
this generator plants them. The same arguments always produce the same bytes,
so tests are reproducible and need no dataset download.
"""

import io
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image


@dataclass
class ImageFolder:
    root: Path
    valid: list[str] = field(default_factory=lambda: list[str]())
    corrupt: list[str] = field(default_factory=lambda: list[str]())
    exact_copies: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(copy, original) pairs with identical bytes."""
    jpeg_pairs: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    """(quality 90, quality 70) pairs of the same picture."""
    undersized: list[str] = field(default_factory=lambda: list[str]())
    not_images: list[str] = field(default_factory=lambda: list[str]())

    @property
    def total_files(self) -> int:
        return (
            len(self.valid)
            + len(self.corrupt)
            + len(self.exact_copies)
            + 2 * len(self.jpeg_pairs)
            + len(self.undersized)
            + len(self.not_images)
        )


def _blocky(rng: random.Random, size: int) -> Image.Image:
    """A unique, highly compressible picture: an 8x8 grid of random colours."""
    small = Image.frombytes("RGB", (8, 8), rng.randbytes(8 * 8 * 3))
    return small.resize((size, size), Image.Resampling.NEAREST)  # pyright: ignore[reportUnknownMemberType]


def _smooth(rng: random.Random, size: int) -> Image.Image:
    """A photo-like picture: smooth colour blobs that JPEG compresses naturally."""
    small = Image.frombytes("RGB", (12, 12), rng.randbytes(12 * 12 * 3))
    return small.resize((size, size), Image.Resampling.BICUBIC)  # pyright: ignore[reportUnknownMemberType]


def _jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def make_image_folder(
    root: Path,
    *,
    valid: int = 20,
    corrupt: int = 0,
    exact_copies: int = 0,
    jpeg_pairs: int = 0,
    undersized: int = 0,
    not_images: int = 0,
    size: int = 256,
    seed: int = 0,
) -> ImageFolder:
    """Create the folder and return a record of what was planted where."""
    if exact_copies > valid:
        raise ValueError("exact_copies cannot exceed valid: each copy needs an original")
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    folder = ImageFolder(root)

    for i in range(valid):
        name = f"valid/img_{i:05d}.png"
        (root / name).parent.mkdir(exist_ok=True)
        _blocky(rng, size).save(root / name, format="PNG")
        folder.valid.append(name)

    for i in range(corrupt):
        name = f"corrupt/broken_{i:03d}.jpg"
        (root / name).parent.mkdir(exist_ok=True)
        if i % 2 == 0:
            good = _jpeg_bytes(_smooth(rng, size), 90)
            (root / name).write_bytes(good[: len(good) // 2])  # truncated JPEG
        else:
            (root / name).write_bytes(rng.randbytes(2048))  # not an image at all
        folder.corrupt.append(name)

    for i in range(exact_copies):
        original = folder.valid[i]
        name = f"copies/copy_{i:03d}.png"
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_bytes((root / original).read_bytes())
        folder.exact_copies.append((name, original))

    for i in range(jpeg_pairs):
        picture = _smooth(rng, size)
        high, low = f"pairs/pair_{i:03d}_q90.jpg", f"pairs/pair_{i:03d}_q70.jpg"
        (root / high).parent.mkdir(exist_ok=True)
        (root / high).write_bytes(_jpeg_bytes(picture, 90))
        (root / low).write_bytes(_jpeg_bytes(picture, 70))
        folder.jpeg_pairs.append((high, low))

    for i in range(undersized):
        name = f"small/tiny_{i:03d}.png"
        (root / name).parent.mkdir(exist_ok=True)
        _blocky(rng, 64).save(root / name, format="PNG")
        folder.undersized.append(name)

    for i in range(not_images):
        name = f"other/notes_{i:03d}.txt"
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_text(f"not an image {i}\n", encoding="utf-8")
        folder.not_images.append(name)

    return folder
