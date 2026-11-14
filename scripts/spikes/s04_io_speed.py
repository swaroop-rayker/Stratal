"""Spike S-04: how slow is a Windows-mounted folder compared with the Linux volume?

Creates N files in a folder on the Windows bind mount, then times:
  1. reading and hashing them through the mount,
  2. copying them into the Docker volume,
  3. reading and hashing them from the volume.

Run:
  docker compose run --rm dev python scripts/spikes/s04_io_speed.py \
      --mount-dir /app/.spike-tmp/s04 --volume-dir /data/spike-tmp/s04

Caveat: files just written or read are in an operating-system cache, so these
are warm-cache figures. The per-file overhead of the mount still shows.
"""

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path


def make_files(folder: Path, count: int, size: int) -> float:
    folder.mkdir(parents=True, exist_ok=True)
    payload = os.urandom(size)
    t0 = time.perf_counter()
    for i in range(count):
        (folder / f"img_{i:05d}.bin").write_bytes(payload[i % 251 :] + payload[: i % 251])
    return time.perf_counter() - t0


def read_and_hash(folder: Path) -> tuple[float, int, int]:
    t0 = time.perf_counter()
    files = 0
    total = 0
    for entry in sorted(os.scandir(folder), key=lambda e: e.name):
        digest = hashlib.sha256()
        with open(entry.path, "rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                total += len(chunk)
        files += 1
    return time.perf_counter() - t0, files, total


def copy_all(src: Path, dst: Path) -> float:
    dst.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    for entry in os.scandir(src):
        shutil.copyfile(entry.path, dst / entry.name)
    return time.perf_counter() - t0


def rate(seconds: float, files: int, total_bytes: int) -> dict[str, float]:
    return {
        "seconds": round(seconds, 2),
        "files_per_s": round(files / seconds),
        "mb_per_s": round(total_bytes / 2**20 / seconds, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mount-dir", type=Path, required=True)
    parser.add_argument("--volume-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--size-kb", type=int, default=100)
    args = parser.parse_args()

    size = args.size_kb * 1024
    total = args.count * size
    report: dict[str, object] = {"files": args.count, "size_kb": args.size_kb}

    report["write_through_mount"] = rate(
        make_files(args.mount_dir, args.count, size), args.count, total
    )
    seconds, files, read_bytes = read_and_hash(args.mount_dir)
    report["read_hash_through_mount"] = rate(seconds, files, read_bytes)
    report["copy_mount_to_volume"] = rate(
        copy_all(args.mount_dir, args.volume_dir), args.count, total
    )
    seconds, files, read_bytes = read_and_hash(args.volume_dir)
    report["read_hash_from_volume"] = rate(seconds, files, read_bytes)

    shutil.rmtree(args.volume_dir, ignore_errors=True)
    shutil.rmtree(args.mount_dir, ignore_errors=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
