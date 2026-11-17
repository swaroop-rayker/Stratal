"""Build the phase-1 dataset source: a 10k subset of COCO 2017 (M4, LABEL-1.1).

Two steps, run in the dev container from the repository root:

    docker compose run --rm dev python -I scripts/datasets/coco10k.py select
    docker compose run --rm dev python -I scripts/datasets/coco10k.py fetch

``--size 20000`` builds ``coco20k`` the same way, for the NFR-2 memory profile
(M7). Drawn with the same seed, its first 10,000 images are exactly coco10k's
(``select`` checks this), and ``fetch`` copies those from ``sources/coco10k``
rather than downloading them again.

``select`` reads the COCO 2017 annotations zip (downloaded by hand into
``sources/coco2017/`` and checked against its published checksums), keeps the
training images whose Flickr licence allows reuse including commercial reuse,
draws 10,000 of them with a fixed seed, and writes:

- ``scripts/datasets/coco10k_ids.txt``: the chosen image IDs, committed, so the
  subset can be rebuilt exactly;
- ``sources/coco10k-meta/images.jsonl``: per image its file name, licence, Flickr
  link (CC BY requires attribution) and its five human captions (for CIDEr-D in
  M5). Kept outside the image folder so ingest sees images only.

``fetch`` downloads each chosen image into ``sources/coco10k/``. It skips files
that are already there, writes each through a temporary name, and checks that
Pillow can decode it. COCO serves only plain HTTP and publishes no per-image
checksums; ingest rejects anything that does not decode.

Nothing here runs inside Stratal itself; it only prepares a source folder.
"""

import argparse
import concurrent.futures
import functools
import json
import random
import shutil
import sys
import time
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ANNOTATIONS_ZIP = ROOT / "sources" / "coco2017" / "annotations_trainval2017.zip"
CAPTIONS_MEMBER = "annotations/captions_train2017.json"
IMAGE_URL = "http://images.cocodataset.org/train2017/{file_name}"

SEED = 2026
SIZES = {10_000: "coco10k", 20_000: "coco20k"}
"""Subset sizes and their names. The 10k subset is the base; larger ones contain it."""


def ids_file(count: int) -> Path:
    return Path(__file__).with_name(f"{SIZES[count]}_ids.txt")


def meta_file(count: int) -> Path:
    return ROOT / "sources" / f"{SIZES[count]}-meta" / "images.jsonl"


def images_dir(count: int) -> Path:
    return ROOT / "sources" / SIZES[count]


# Licences that allow reuse, commercial reuse included (COCO's own licence names).
ALLOWED_LICENCES = frozenset(
    {
        "Attribution License",
        "Attribution-ShareAlike License",
        "No known copyright restrictions",
        "United States Government Work",
    }
)


def select(count: int) -> None:
    with zipfile.ZipFile(ANNOTATIONS_ZIP) as archive:
        data: dict[str, Any] = json.loads(archive.read(CAPTIONS_MEMBER))
    licences = {int(item["id"]): str(item["name"]) for item in data["licenses"]}
    missing = ALLOWED_LICENCES - set(licences.values())
    if missing:
        sys.exit(f"licence names not found in the annotations: {sorted(missing)}")
    captions: dict[int, list[str]] = {}
    for annotation in data["annotations"]:
        captions.setdefault(int(annotation["image_id"]), []).append(
            str(annotation["caption"]).strip()
        )
    images = {int(image["id"]): image for image in data["images"]}
    eligible = sorted(
        i for i, image in images.items() if licences[image["license"]] in ALLOWED_LICENCES
    )
    print(f"{len(images)} training images; {len(eligible)} with an allowed licence")
    drawn = random.Random(SEED).sample(eligible, count)
    if count != 10_000:
        base = set(random.Random(SEED).sample(eligible, 10_000))
        if set(drawn[:10_000]) != base:
            sys.exit("the larger draw does not start with the 10k subset; refusing to continue")
    chosen = sorted(drawn)

    ids_path, meta_path = ids_file(count), meta_file(count)
    ids_path.write_text(
        f"# COCO 2017 train image IDs: {count} drawn with seed {SEED} from the {len(eligible)}\n"
        f"# images whose licence is one of: {', '.join(sorted(ALLOWED_LICENCES))}.\n"
        "# Written by scripts/datasets/coco10k.py select.\n" + "".join(f"{i}\n" for i in chosen),
        encoding="utf-8",
    )
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_path.open("w", encoding="utf-8") as out:
        for image_id in chosen:
            image = images[image_id]
            record = {
                "image_id": image_id,
                "file_name": image["file_name"],
                "width": image["width"],
                "height": image["height"],
                "licence": licences[image["license"]],
                "flickr_url": image.get("flickr_url"),
                "captions": captions.get(image_id, []),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    by_licence: dict[str, int] = {}
    for image_id in chosen:
        name = licences[images[image_id]["license"]]
        by_licence[name] = by_licence.get(name, 0) + 1
    print(f"chose {count}: {by_licence}")
    print(f"wrote {ids_path.relative_to(ROOT)} and {meta_path.relative_to(ROOT)}")


def _fetch_one(file_name: str, target_dir: Path) -> tuple[str, int]:
    target = target_dir / file_name
    if target.exists():
        return "present", 0
    local = images_dir(10_000) / file_name
    if target_dir != local.parent and local.is_file():
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(local, partial)
        partial.replace(target)
        return "copied", 0
    url = IMAGE_URL.format(file_name=file_name)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                body: bytes = response.read()
            with Image.open(BytesIO(body)) as image:
                image.verify()
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(body)
            partial.replace(target)
            return "fetched", len(body)
        except Exception as exc:
            if attempt == 2:
                print(f"failed {file_name}: {exc}", flush=True)
                return "failed", 0
            time.sleep(2 * (attempt + 1))
    return "failed", 0


def fetch(count: int, workers: int) -> None:
    records = [
        json.loads(line) for line in meta_file(count).read_text(encoding="utf-8").splitlines()
    ]
    target_dir = images_dir(count)
    target_dir.mkdir(parents=True, exist_ok=True)
    counts = {"present": 0, "copied": 0, "fetched": 0, "failed": 0}
    total_bytes = 0
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (outcome, size) in enumerate(
            pool.map(
                functools.partial(_fetch_one, target_dir=target_dir),
                [str(r["file_name"]) for r in records],
            ),
            start=1,
        ):
            counts[outcome] += 1
            total_bytes += size
            if done % 500 == 0 or done == len(records):
                rate = done / (time.monotonic() - started)
                print(
                    f"{done}/{len(records)} {counts} {total_bytes / 1e6:.0f} MB, {rate:.0f}/s",
                    flush=True,
                )
    if counts["failed"]:
        sys.exit(f"{counts['failed']} images failed; run fetch again to retry them")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--size",
        type=int,
        choices=sorted(SIZES),
        default=10_000,
        help="subset size (default 10000)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("select", help="choose the image IDs and write the metadata")
    fetching = commands.add_parser("fetch", help="download the chosen images")
    fetching.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.command == "select":
        select(args.size)
    else:
        fetch(args.size, args.workers)


if __name__ == "__main__":
    main()
