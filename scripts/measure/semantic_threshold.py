# pyright: basic, reportMissingImports=false
"""K1 (M11, ADR-0013 rule 6): the CLIP image similarity a semantic duplicate needs, on coco10k.

Measures, with the label stage's CLIP (``clip-vit-b-32-laion2b``, fp16) on the GPU:

1. **Planted variants** of ``--planted`` random images: an 80 % centre crop, a random 60 %
   crop, a mirrored 90 % crop (DATA-2.3.AC1's "cropped variants"), and K2's synthetic
   "other angle" pair: two overlapping 70 % crops from opposite corners, one warped in
   perspective, whose pHash distance (8×8, as the clean stage) is also recorded
   (DATA-4.8.AC1: far by pHash, close by CLIP).
2. **Every pair of the dataset** (about 50 M), split by whether the two images share their
   COCO category (from ``coco10k-captions``): the similarity histogram of each.
3. **For each threshold** from 0.80 to 0.99: the share of each variant found, the dataset
   pairs at or above it, and the clusters union-find makes of them (ADR-0013 rule 1): how
   many, how many rows, and the largest, which a low threshold can make huge.

Run in the GPU worker (needs the GPU, the sources and the pulled model)::

    docker compose run --rm -v ./scripts:/app/scripts:ro worker-gpu \\
        python -I scripts/measure/semantic_threshold.py > measurement.json
"""

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageOps

from stratal.core.settings import get_settings
from stratal.data.dedupe import perceptual_hash
from stratal.label.hf import load_clip

CLIP = "clip-vit-b-32-laion2b"
THRESHOLDS = [round(0.80 + 0.01 * i, 2) for i in range(20)]
FLOOR = 0.80
BATCH = 128


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def crop(image: Image.Image, side: float, x: float, y: float) -> Image.Image:
    """A square-ish crop of ``side`` of each dimension, its top-left at (x, y) of the slack."""
    w, h = image.size
    cw, ch = int(w * side), int(h * side)
    left, top = int((w - cw) * x), int((h - ch) * y)
    return image.crop((left, top, left + cw, top + ch))


def perspective(image: Image.Image, amount: float = 0.12) -> Image.Image:
    """The image seen from another angle: its far edge pulled in, as a QUAD transform."""
    w, h = image.size
    dx, dy = int(w * amount), int(h * amount * 0.5)
    quad = (dx, dy, 0, h, w, h, w - dx, 0)  # upper-left, lower-left, lower-right, upper-right
    return image.transform((w, h), Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def encode(clip: Any, images: list[Image.Image]) -> torch.Tensor:
    vectors: list[list[float]] = []
    for start in range(0, len(images), BATCH):
        vectors += clip.encode_images(images[start : start + BATCH])
    tensor = torch.tensor(vectors, dtype=torch.float32, device="cuda")
    return torch.nn.functional.normalize(tensor, dim=1)


def categories(captions: Path) -> dict[int, str]:
    found: dict[int, str] = {}
    with captions.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            found.setdefault(int(row["group"]), str(row["label"]))
    return found


def clusters(n: int, pairs: list[tuple[int, int]]) -> list[int]:
    """Sizes of union-find's clusters of two or more, largest first."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    sizes = Counter(find(i) for i in range(n))
    return sorted((s for s in sizes.values() if s > 1), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", default="/sources/coco10k")
    parser.add_argument("--captions", default="/sources/coco10k-captions/captions.jsonl")
    parser.add_argument("--planted", type=int, default=400)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    started = time.monotonic()
    rng = random.Random(args.seed)

    files = sorted(Path(args.source).glob("*.jpg"))
    category = categories(Path(args.captions))
    labels = [category.get(int(f.stem), "?") for f in files]
    log(f"{len(files)} images, {len(set(labels))} categories")

    clip = load_clip(CLIP, get_settings().data_dir / "models", dtype="fp16")
    torch.cuda.reset_peak_memory_stats()
    chunks: list[torch.Tensor] = []
    for start in range(0, len(files), 1024):
        batch = [Image.open(f).convert("RGB") for f in files[start : start + 1024]]
        chunks.append(encode(clip, batch))
        log(f"encoded {start + len(batch)}/{len(files)}")
    embeddings = torch.cat(chunks)

    # 1. Planted variants of a random sample.
    chosen = rng.sample(range(len(files)), args.planted)
    originals = [Image.open(files[i]).convert("RGB") for i in chosen]
    variants: dict[str, list[Image.Image]] = {
        "crop_80_centre": [crop(im, 0.8, 0.5, 0.5) for im in originals],
        "crop_60_random": [crop(im, 0.6, rng.random(), rng.random()) for im in originals],
        "mirror_crop_90": [ImageOps.mirror(crop(im, 0.9, 0.5, 0.5)) for im in originals],
    }
    angle_a = [crop(im, 0.7, 0.0, 0.0) for im in originals]
    angle_b = [perspective(crop(im, 0.7, 1.0, 1.0)) for im in originals]
    base = embeddings[chosen]
    planted: dict[str, list[float]] = {}
    for name, images in variants.items():
        planted[name] = (base * encode(clip, images)).sum(1).tolist()
    planted["other_angle"] = (encode(clip, angle_a) * encode(clip, angle_b)).sum(1).tolist()
    angle_phash = [
        hamming(perceptual_hash(a, hash_size=8), perceptual_hash(b, hash_size=8))
        for a, b in zip(angle_a, angle_b, strict=True)
    ]
    crop_phash = [
        hamming(perceptual_hash(o, hash_size=8), perceptual_hash(c, hash_size=8))
        for o, c in zip(originals, variants["crop_60_random"], strict=True)
    ]
    log("planted variants encoded")

    # 2. Every pair of the dataset, by category.
    n = len(files)
    label_ids = {name: i for i, name in enumerate(sorted(set(labels)))}
    label_tensor = torch.tensor([label_ids[x] for x in labels], device="cuda")
    bins = 100
    same_hist = torch.zeros(bins, dtype=torch.long, device="cuda")
    other_hist = torch.zeros(bins, dtype=torch.long, device="cuda")
    high: list[tuple[int, int, float]] = []
    for start in range(0, n, 512):
        block = embeddings[start : start + 512] @ embeddings.T
        rows = torch.arange(start, min(start + 512, n), device="cuda")[:, None]
        upper = torch.arange(n, device="cuda")[None, :] > rows
        same = label_tensor[rows] == label_tensor[None, :]
        index = (block.clamp(0, 0.9999) * bins).long()
        same_hist += torch.bincount(index[upper & same], minlength=bins)
        other_hist += torch.bincount(index[upper & ~same], minlength=bins)
        hit = torch.nonzero(upper & (block >= FLOOR))
        values = block[hit[:, 0], hit[:, 1]].tolist()
        high += [(start + r, c, v) for (r, c), v in zip(hit.tolist(), values, strict=True)]
    log(f"{len(high)} pairs at or above {FLOOR}")

    # 3. Per threshold.
    def found(values: list[float], t: float) -> float:
        return round(sum(v >= t for v in values) / len(values), 4)

    table: list[dict[str, Any]] = []
    for t in THRESHOLDS:
        pairs = [(a, b) for a, b, s in high if s >= t]
        sizes = clusters(n, pairs)
        table.append(
            {
                "threshold": t,
                "found": {name: found(values, t) for name, values in planted.items()},
                "pairs": len(pairs),
                "pairs_same_category": sum(labels[a] == labels[b] for a, b in pairs),
                "clusters": len(sizes),
                "rows_in_clusters": sum(sizes),
                "largest": sizes[0] if sizes else 0,
                "top_sizes": sizes[:8],
            }
        )

    def examples(low: float, top: float) -> list[dict[str, Any]]:
        band = sorted((p for p in high if low <= p[2] < top), key=lambda p: -p[2])
        picks = band[:: max(1, len(band) // 6)][:6]
        return [
            {
                "a": files[a].name,
                "b": files[b].name,
                "similarity": round(s, 4),
                "categories": [labels[a], labels[b]],
            }
            for a, b, s in picks
        ]

    def quantiles(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        return {
            f"p{q}": round(ordered[min(len(ordered) - 1, int(q / 100 * len(ordered)))], 4)
            for q in (1, 5, 10, 25, 50, 75, 90)
        }

    result = {
        "model": CLIP,
        "images": n,
        "planted": args.planted,
        "seed": args.seed,
        "planted_quantiles": {name: quantiles(values) for name, values in planted.items()},
        "other_angle_phash": {
            "min": min(angle_phash),
            "median": sorted(angle_phash)[len(angle_phash) // 2],
            "share_over_8": round(sum(d > 8 for d in angle_phash) / len(angle_phash), 4),
        },
        "crop_60_phash": {
            "median": sorted(crop_phash)[len(crop_phash) // 2],
            "share_over_8": round(sum(d > 8 for d in crop_phash) / len(crop_phash), 4),
        },
        "pair_histogram": {
            "bin_width": 1 / bins,
            "same_category": same_hist.tolist(),
            "other_category": other_hist.tolist(),
        },
        "thresholds": table,
        "examples": {
            "0.97+": examples(0.97, 1.01),
            "0.95-0.97": examples(0.95, 0.97),
            "0.92-0.95": examples(0.92, 0.95),
            "0.90-0.92": examples(0.90, 0.92),
        },
        "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "seconds": round(time.monotonic() - started, 1),
    }
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
