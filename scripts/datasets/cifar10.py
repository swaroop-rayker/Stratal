"""Build the phase-2 image source: CIFAR-10 as class folders (M8, decision 1).

Run in the dev container from the repository root:

    docker compose run --rm dev python -I scripts/datasets/cifar10.py fetch
    docker compose run --rm dev python -I scripts/datasets/cifar10.py extract

``fetch`` downloads the *binary* version of CIFAR-10 (about 162 MB) from the
University of Toronto into ``sources/cifar10-download/`` and checks it against the
MD5 the site publishes. The binary version is used because the "python" version is
a set of pickle files, and NFR-8 forbids unpickling downloaded files.

``extract`` reads the six batch files straight out of the archive (nothing is
extracted by name) and writes one PNG per image:

    sources/cifar10/<class>/<split>-<nnnnn>.png

The class folder is the label; ``split`` is CIFAR-10's own (``train``: 50,000,
``test``: 10,000), kept in the file name so the pipeline can honour it or not
(ADR-0014). Each record is 1 label byte and 3,072 pixel bytes: 1,024 red, then
green, then blue, row by row.

Nothing here runs inside Stratal itself; it only prepares a source folder.
"""

import argparse
import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
URL = "https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz"
MD5 = "c32a1d4ab5d03f1284b67883e8d87530"  # published on https://www.cs.toronto.edu/~kriz/cifar.html
ARCHIVE = ROOT / "sources" / "cifar10-download" / "cifar-10-binary.tar.gz"
IMAGES_DIR = ROOT / "sources" / "cifar10"
CLASSES = (
    "airplane", "automobile", "bird", "cat", "deer",
    "dog", "frog", "horse", "ship", "truck",
)  # fmt: skip
"""The order of CIFAR-10's label bytes, as listed in its ``batches.meta.txt``."""
BATCHES = {
    "cifar-10-batches-bin/data_batch_1.bin": "train",
    "cifar-10-batches-bin/data_batch_2.bin": "train",
    "cifar-10-batches-bin/data_batch_3.bin": "train",
    "cifar-10-batches-bin/data_batch_4.bin": "train",
    "cifar-10-batches-bin/data_batch_5.bin": "train",
    "cifar-10-batches-bin/test_batch.bin": "test",
}
RECORD = 1 + 32 * 32 * 3
PER_BATCH = 10_000


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch() -> None:
    if ARCHIVE.is_file() and _md5(ARCHIVE) == MD5:
        print(f"{ARCHIVE.relative_to(ROOT)} already present and verified")
        return
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    partial = ARCHIVE.with_name(ARCHIVE.name + ".part")
    with urllib.request.urlopen(URL, timeout=60) as response, partial.open("wb") as out:
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if done % (20 << 20) < (1 << 20):
                print(f"{done / 1e6:.0f} MB", flush=True)
    got = _md5(partial)
    if got != MD5:
        partial.unlink()
        sys.exit(f"MD5 mismatch: got {got}, expected {MD5}; the file was deleted")
    partial.replace(ARCHIVE)
    print(f"downloaded {ARCHIVE.stat().st_size / 1e6:.0f} MB, MD5 verified")


def _batch_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.getmember(name)
    if not member.isfile() or member.size != RECORD * PER_BATCH:
        sys.exit(f"{name}: expected a regular file of {RECORD * PER_BATCH} bytes")
    handle = archive.extractfile(member)
    if handle is None:
        sys.exit(f"{name}: cannot be read")
    return handle.read()


def extract() -> None:
    if not ARCHIVE.is_file() or _md5(ARCHIVE) != MD5:
        sys.exit("run fetch first: the archive is missing or does not match its MD5")
    for name in CLASSES:
        (IMAGES_DIR / name).mkdir(parents=True, exist_ok=True)
    counts = {"train": 0, "test": 0}
    written = 0
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for member_name, split in BATCHES.items():
            data = _batch_bytes(archive, member_name)
            for offset in range(0, len(data), RECORD):
                label = data[offset]
                if label >= len(CLASSES):
                    sys.exit(f"{member_name}: label byte {label} out of range")
                pixels = data[offset + 1 : offset + RECORD]
                planes = [
                    Image.frombytes("L", (32, 32), pixels[i * 1024 : (i + 1) * 1024])
                    for i in range(3)
                ]
                index = counts[split]
                counts[split] += 1
                target = IMAGES_DIR / CLASSES[label] / f"{split}-{index:05d}.png"
                if target.exists():
                    continue
                partial = target.with_name(target.name + ".part")
                Image.merge("RGB", planes).save(partial, format="PNG")
                partial.replace(target)
                written += 1
            print(f"{member_name}: done ({counts})", flush=True)
    print(
        f"{sum(counts.values())} images, {written} written now, in {IMAGES_DIR.relative_to(ROOT)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="download and verify the binary archive")
    commands.add_parser("extract", help="write one PNG per image into class folders")
    args = parser.parse_args()
    fetch() if args.command == "fetch" else extract()


if __name__ == "__main__":
    main()
