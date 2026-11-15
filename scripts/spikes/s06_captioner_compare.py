# pyright: basic, reportMissingImports=false
"""Spike S-06: Florence-2 or BLIP? (M4, docs/RISKS.md)

Captions the same 50 COCO images with each captioner and reports speed at batch
sizes 4, 8 and 16 in half precision, peak VRAM, and a mean CLIP cosine between
image and caption (the human captions are scored the same way, as a yardstick).
Writes a side-by-side sheet for the owner. Run in the GPU image, offline:

    docker compose run --rm --no-deps -v ./docs/spikes:/out worker-gpu \
        python scripts/spikes/s06_captioner_compare.py

The CLIP cosine here is a measurement for the spike only, not LABEL-1.3 (the
owner's unit). CIDEr-D is not computed: it is the owner's evaluation code (CL-13).
"""

import json
import random
import statistics
import time
from pathlib import Path

import torch
from PIL import Image

from stratal.core.settings import get_settings
from stratal.label.hf import load_captioner, load_clip, set_vram_cap

MODELS = get_settings().data_dir / "models"
COCO = Path("/sources/coco10k")
META = Path("/sources/coco10k-meta/images.jsonl")
OUT = Path("/out")
SEED = 6
COUNT = 50
RUNS = [
    ("florence-2-base", "<CAPTION>", 40, "Florence-2-base, <CAPTION>"),
    ("florence-2-base", "<DETAILED_CAPTION>", 80, "Florence-2-base, <DETAILED_CAPTION>"),
    ("blip-large", "", 40, "BLIP-large"),
]
BATCHES = (4, 8, 16)


def _cosines(clip, images: list[Image.Image], texts: list[str]) -> list[float]:
    a = torch.tensor(clip.encode_images(images))
    b = torch.tensor(clip.encode_texts(texts))
    return torch.nn.functional.cosine_similarity(a, b).tolist()


def main() -> None:
    records = [json.loads(line) for line in META.read_text().splitlines()]
    chosen = sorted(random.Random(SEED).sample(records, COUNT), key=lambda r: r["image_id"])
    images = [Image.open(COCO / r["file_name"]).convert("RGB") for r in chosen]
    set_vram_cap(0.85)
    results: dict[str, dict] = {}
    captions: dict[str, list[str]] = {}

    for name, task, tokens, label in RUNS:
        torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        model = load_captioner(
            name, MODELS, dtype="fp16", task=task or "<CAPTION>", max_new_tokens=tokens
        )
        load_s = time.monotonic() - started
        speed: dict[int, float] = {}
        for batch in BATCHES:
            model.caption(images[:batch])  # warm-up
            torch.cuda.synchronize()
            started = time.monotonic()
            out: list[str] = []
            for i in range(0, COUNT, batch):
                out.extend(model.caption(images[i : i + batch]))
            torch.cuda.synchronize()
            speed[batch] = COUNT / (time.monotonic() - started)
            if batch == 8:
                captions[label] = out
        peak_gb = torch.cuda.max_memory_reserved() / 2**30
        model.close()
        del model
        torch.cuda.empty_cache()
        results[label] = {
            "load_s": round(load_s, 1),
            "img_per_s": speed,
            "peak_reserved_gb": round(peak_gb, 2),
        }
        print(label, results[label], flush=True)

    clip = load_clip("clip-vit-b-32-laion2b", MODELS, dtype="fp16")
    humans = [r["captions"][0] for r in chosen]
    scores = {"Human (first COCO caption)": _cosines(clip, images, humans)}
    for label, texts in captions.items():
        scores[label] = _cosines(clip, images, texts)
    clip.close()

    words = {
        label: statistics.mean(len(t.split()) for t in texts) for label, texts in captions.items()
    }
    words["Human (first COCO caption)"] = statistics.mean(len(t.split()) for t in humans)
    summary = {
        "images": [r["file_name"] for r in chosen],
        "runs": results,
        "clip_cosine_mean": {k: round(statistics.mean(v), 4) for k, v in scores.items()},
        "clip_cosine_stdev": {k: round(statistics.stdev(v), 4) for k, v in scores.items()},
        "mean_words": {k: round(v, 1) for k, v in words.items()},
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "S-06-results.json").write_text(json.dumps(summary, indent=2) + "\n")

    lines = [
        "# Spike S-06: captions side by side",
        "",
        f"50 COCO 2017 images (seed {SEED}), captioned in half precision at batch size 8,"
        " three beams. CLIP is the cosine from `clip-vit-b-32-laion2b`; higher means the text"
        " matches the image better. Human captions are COCO's (CC BY 4.0); images are linked"
        " on Flickr under their own licences (`sources/coco10k-meta/images.jsonl`).",
        "",
    ]
    labels = list(captions)
    for index, record in enumerate(chosen):
        lines.append(f"### {index + 1}. {record['file_name']}")
        lines.append("")
        lines.append("| Source | Caption | CLIP |")
        lines.append("|---|---|---|")
        human = "Human (first COCO caption)"
        lines.append(f"| Human | {humans[index]} | {scores[human][index]:.3f} |")
        for label in labels:
            text = captions[label][index].replace("|", "/")
            lines.append(f"| {label} | {text} | {scores[label][index]:.3f} |")
        lines.append("")
    (OUT / "S-06-captions.md").write_text("\n".join(lines))
    print(json.dumps(summary["clip_cosine_mean"], indent=2))
    print(json.dumps(summary["mean_words"], indent=2))


if __name__ == "__main__":
    main()
