"""Write the COCO captions as a labelled-text source (M10, decision 1, ADR-0019 rule 7).

No download. It reads what is already on disk:

- ``sources/coco10k-meta/images.jsonl``: the 10,000 images' five captions each;
- ``sources/coco2017/annotations_trainval2017.zip``: the instance annotations,
  for each image's category.

Each caption is labelled with the category of its image's largest annotated
object (by mask area), and grouped by its image, so the five captions of one
image always share a split (DATA-4.2). Images with no annotated object are left
out, and counted. Run in the dev container from the repository root:

    docker compose run --rm dev python -I scripts/datasets/coco_captions_text.py

It writes ``sources/coco10k-captions/captions.jsonl`` (``--level supercategory``
for the 12 broad classes instead of the 80 categories). Ingest it with
``dataset_type: labelled_text``.

The instances file is about 450 MB of JSON; loading it whole needs several GB.
It is scanned as a stream instead: COCO writes every annotation's fields in the
same order (segmentation, area, iscrowd, image_id, bbox, category_id, id), so
one regular expression over a sliding window finds each annotation's area,
image and category without parsing the polygons.
"""

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

INSTANCES = "annotations/instances_train2017.json"
_ANNOTATION = re.compile(
    rb'"area":\s*([0-9.eE+-]+),\s*"iscrowd":\s*\d+,\s*"image_id":\s*(\d+),\s*"bbox":\s*\[[^\]]*\],'
    rb'\s*"category_id":\s*(\d+)'
)
_CATEGORY = re.compile(rb'\{"supercategory":\s*"([^"]+)",\s*"id":\s*(\d+),\s*"name":\s*"([^"]+)"\}')
_CHUNK = 8 << 20
_OVERLAP = 4096


def largest_categories(
    archive: Path, wanted: set[int]
) -> tuple[dict[int, int], dict[int, tuple[str, str]]]:
    """For each wanted image, the category of its largest annotation; and the category names."""
    best: dict[int, tuple[float, int]] = {}
    categories: dict[int, tuple[str, str]] = {}
    with zipfile.ZipFile(archive) as zf, zf.open(INSTANCES) as stream:
        tail = b""
        while True:
            chunk = stream.read(_CHUNK)
            buffer = tail + chunk
            # Only match up to a point every match can complete in; the rest carries over.
            limit = len(buffer) if not chunk else max(0, len(buffer) - _OVERLAP)
            consumed = 0
            for match in _ANNOTATION.finditer(buffer, 0, limit):
                area, image, category = float(match[1]), int(match[2]), int(match[3])
                if image in wanted and area > best.get(image, (-1.0, 0))[0]:
                    best[image] = (area, category)
                consumed = match.end()
            for match in _CATEGORY.finditer(buffer, 0, limit):
                categories[int(match[2])] = (match[3].decode(), match[1].decode())
            if not chunk:
                break
            # Keep what follows the last match, but at least the last _OVERLAP bytes before the
            # limit: a match (a few hundred bytes) that straddled the limit starts in them.
            tail = buffer[max(consumed, limit - _OVERLAP) :]
    return {image: category for image, (_, category) in best.items()}, categories


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--meta", type=Path, default=Path("sources/coco10k-meta/images.jsonl"))
    parser.add_argument(
        "--annotations", type=Path, default=Path("sources/coco2017/annotations_trainval2017.zip")
    )
    parser.add_argument("--out", type=Path, default=Path("sources/coco10k-captions/captions.jsonl"))
    parser.add_argument("--level", choices=("category", "supercategory"), default="category")
    args = parser.parse_args()

    images = [json.loads(line) for line in args.meta.read_text(encoding="utf-8").splitlines()]
    by_image, categories = largest_categories(
        args.annotations, {int(i["image_id"]) for i in images}
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    labels: Counter[str] = Counter()
    unlabelled = 0
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for image in images:
            category = by_image.get(int(image["image_id"]))
            if category is None:
                unlabelled += 1
                continue
            name, supercategory = categories[category]
            label = name if args.level == "category" else supercategory
            for caption in image["captions"]:
                record = {"text": caption, "label": label, "group": image["image_id"]}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                labels[label] += 1
    print(
        f"{args.out}: {sum(labels.values()):,} captions of {len(images) - unlabelled:,} images, "
        f"{len(labels)} classes; {unlabelled:,} images without an annotated object left out"
    )
    print("largest classes:", ", ".join(f"{k} {v:,}" for k, v in labels.most_common(8)))


if __name__ == "__main__":
    main()
