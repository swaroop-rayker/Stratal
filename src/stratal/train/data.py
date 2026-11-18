# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Labelled images from a split version, as tensors (ADR-0014 rules 4 and 5).

The input streams: a :class:`LabelledImages` dataset reads each row's
preprocessed copy from the blob store and decodes it in the loader's worker
processes. :class:`EpochOrder` gives each epoch a permutation seeded by
(seed, epoch), so a resumed run replays the rest of an epoch in the same order.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset, Sampler

from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import STATUS_ACCEPTED

MEAN = 0.5
STD = 0.25
"""Pixels are scaled to [0, 1], then shifted and scaled to about [-2, 2]. The
models normalise further with batch norm; exact dataset statistics are not needed."""


@dataclass(frozen=True)
class Item:
    row_key: str
    path: Path
    label: int


@dataclass(frozen=True)
class SplitItems:
    classes: list[str]
    """Class names in index order: the sorted labels of every accepted row."""
    by_split: dict[str, list[Item]]


def split_items(rows: Iterable[Mapping[str, Any]], blobs: BlobStore) -> SplitItems:
    """The accepted rows of a labelled split version, by split, in ``row_key`` order."""
    accepted = [r for r in rows if r.get("status") == STATUS_ACCEPTED and r.get("split")]
    classes = sorted({str(r["label"]) for r in accepted})
    index = {name: i for i, name in enumerate(classes)}
    by_split: dict[str, list[Item]] = {"train": [], "val": [], "test": []}
    for row in sorted(accepted, key=lambda r: str(r["row_key"])):
        sha256 = row.get("derived_sha256") or row["sha256"]
        item = Item(str(row["row_key"]), blobs.path_for(str(sha256)), index[str(row["label"])])
        by_split.setdefault(str(row["split"]), []).append(item)
    return SplitItems(classes, by_split)


def to_tensor(image: Image.Image) -> torch.Tensor:
    """An RGB image as a normalised float tensor, (C, H, W)."""
    array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    tensor = torch.from_numpy(array.copy()).permute(2, 0, 1).float().div_(255.0)
    return tensor.sub_(MEAN).div_(STD)


class LabelledImages(Dataset):
    """Decodes one preprocessed image per item, in the loader's worker process."""

    def __init__(self, items: list[Item]) -> None:
        self.items = items

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        item = self.items[index]
        with Image.open(item.path) as image:
            return to_tensor(image), item.label


class EpochOrder(Sampler[int]):
    """A permutation of ``range(size)`` per epoch, from (seed, epoch); can start mid-epoch."""

    def __init__(self, size: int, *, seed: int, epoch: int, start: int = 0) -> None:
        self.size = size
        self.seed = seed
        self.epoch = epoch
        self.start = start

    def order(self) -> list[int]:
        generator = torch.Generator().manual_seed(self.seed * 1_000_003 + self.epoch)
        return torch.randperm(self.size, generator=generator).tolist()

    def __iter__(self) -> Iterator[int]:
        return iter(self.order()[self.start :])

    def __len__(self) -> int:
        return self.size - self.start
