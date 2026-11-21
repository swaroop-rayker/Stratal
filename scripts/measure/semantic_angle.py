# pyright: basic, reportMissingImports=false
"""K2 (M11, DATA-4.8.AC1): how close CLIP keeps a synthetic "same scene, other angle" pair.

Two crops of one photo from opposite corners, the second warped in perspective, for
several crop sizes and warps: their CLIP similarity and their pHash distance (8×8, as
the clean stage). DATA-4.8.AC1 needs a pair far by pHash (over the clean stage's 8 bits)
and close by CLIP (at or above the semantic threshold). Companion to
``semantic_threshold.py``; same images, same seed.

    docker compose run --rm -v ./scripts:/app/scripts:ro worker-gpu \\
        python -I scripts/measure/semantic_angle.py > angles.json
"""

import argparse
import json
import random
from pathlib import Path

import torch
from PIL import Image

from stratal.core.settings import get_settings
from stratal.data.dedupe import perceptual_hash
from stratal.label.hf import load_clip

CLIP = "clip-vit-b-32-laion2b"
SIDES = (0.7, 0.8, 0.85, 0.9)
WARPS = (0.0, 0.06, 0.12)


def crop(image: Image.Image, side: float, x: float, y: float) -> Image.Image:
    w, h = image.size
    cw, ch = int(w * side), int(h * side)
    left, top = int((w - cw) * x), int((h - ch) * y)
    return image.crop((left, top, left + cw, top + ch))


def perspective(image: Image.Image, amount: float) -> Image.Image:
    if amount == 0:
        return image
    w, h = image.size
    dx, dy = int(w * amount), int(h * amount * 0.5)
    quad = (dx, dy, 0, h, w, h, w - dx, 0)
    return image.transform((w, h), Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", default="/sources/coco10k")
    parser.add_argument("--planted", type=int, default=400)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    files = sorted(Path(args.source).glob("*.jpg"))
    chosen = rng.sample(range(len(files)), args.planted)
    originals = [Image.open(files[i]).convert("RGB") for i in chosen]
    clip = load_clip(CLIP, get_settings().data_dir / "models", dtype="fp16")

    def encode(images: list[Image.Image]) -> torch.Tensor:
        vectors: list[list[float]] = []
        for start in range(0, len(images), 128):
            vectors += clip.encode_images(images[start : start + 128])
        return torch.nn.functional.normalize(torch.tensor(vectors), dim=1)

    rows = []
    for side in SIDES:
        a = [crop(im, side, 0.0, 0.0) for im in originals]
        ea = encode(a)
        ha = [perceptual_hash(x, hash_size=8) for x in a]
        for warp in WARPS:
            b = [perspective(crop(im, side, 1.0, 1.0), warp) for im in originals]
            sims = sorted((ea * encode(b)).sum(1).tolist())
            far = [hamming(x, perceptual_hash(y, hash_size=8)) for x, y in zip(ha, b, strict=True)]
            rows.append(
                {
                    "side": side,
                    "overlap_area": round((2 * side - 1) ** 2, 3),
                    "warp": warp,
                    "clip_p10": round(sims[len(sims) // 10], 4),
                    "clip_median": round(sims[len(sims) // 2], 4),
                    "found_at": {
                        t: round(sum(s >= t for s in sims) / len(sims), 4)
                        for t in (0.85, 0.88, 0.9, 0.92, 0.93)
                    },
                    "phash_median": sorted(far)[len(far) // 2],
                    "phash_over_8": round(sum(d > 8 for d in far) / len(far), 4),
                }
            )
    print(
        json.dumps(
            {"model": CLIP, "planted": args.planted, "seed": args.seed, "rows": rows}, indent=1
        )
    )


if __name__ == "__main__":
    main()
