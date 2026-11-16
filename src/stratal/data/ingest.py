"""Stage 0: ingest a folder of files into the blob store (DATA-1.1 to DATA-1.3).

Every file under the source folder becomes exactly one manifest row. Nothing is
skipped silently: a file that cannot be read, is not an image, is corrupt or is
too small still gets a row, with status ``rejected`` and a reason.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import SourceNotFound
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.versioning import VersionDeriver
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

STAGE = "ingest"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_PROGRESS_EVERY = 500


class IngestConfig(BaseModel):
    """Settings for the ingest stage. The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_size: int = Field(
        default=256,
        ge=1,
        le=16384,
        description="Images whose shorter side is below this many pixels are rejected (DATA-1.3).",
    )


@dataclass(frozen=True)
class IngestResult:
    record: VersionRecord
    new_blob_bytes: int
    """Bytes of blobs first written by this run (DATA-3.4)."""
    seconds: float


def scan(source: Path) -> list[str]:
    """Relative POSIX paths of every file under ``source``, sorted."""
    if not source.is_dir():
        raise SourceNotFound(
            f"Source folder {source} does not exist or is not a folder.",
            suggested_action="Check the path, and that it is mounted into the container.",
        )
    return sorted(p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file())


def _reject(row: Row, code: str, detail: str, *paths: Path) -> Row:
    """Mark ``row`` rejected. Every path in ``detail`` is replaced by the row key.

    Decoder and OS messages name the file they failed on, by its absolute path
    in the blob store or the source mount. The detail is part of the manifest
    and so of the version ID, which must not depend on where the data folder
    is (NFR-4, DATA-3.2 rule 1; E-010).
    """
    for path in sorted({str(p) for p in paths}, key=len, reverse=True):
        detail = detail.replace(path, str(row["row_key"]))
    row.update(
        status=STATUS_REJECTED, decided_at_stage=STAGE, reason_code=code, reason_detail=detail[:500]
    )
    return row


def _inspect(path: Path, relpath: str, row: Row, config: IngestConfig) -> Row:
    """Fill in format and dimensions, and decide accept or reject."""
    try:
        with Image.open(path) as image:
            row.update(format=image.format, width=image.width, height=image.height)
            # Decode the whole stream so truncated files are caught. For JPEG,
            # ask for a reduced-size decode: same bytes read, far less work.
            image.draft("RGB", (64, 64))
            image.load()
    except UnidentifiedImageError as exc:
        known = Path(relpath).suffix.lower() in _IMAGE_SUFFIXES
        return _reject(row, "corrupt_image" if known else "unsupported_format", str(exc), path)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        return _reject(row, "corrupt_image", f"{type(exc).__name__}: {exc}", path)

    shorter = min(int(row["width"]), int(row["height"]))
    if shorter < config.min_size:
        return _reject(
            row,
            "below_min_size",
            f"shorter side {shorter}px is below the minimum {config.min_size}px",
        )
    row["status"] = STATUS_ACCEPTED
    return row


class _Ingest:
    """Holds the counters the row generator updates as it is consumed."""

    def __init__(self, source: Path, blobs: BlobStore, config: IngestConfig, sink: EventSink):
        self.source = source
        self.blobs = blobs
        self.config = config
        self.sink = sink
        self.new_blob_bytes = 0

    def rows(self, relpaths: list[str]) -> Iterator[Row]:
        total = len(relpaths)
        accepted = rejected = 0
        started = time.monotonic()
        for index, relpath in enumerate(relpaths, start=1):
            row: Row = {"row_key": relpath}
            try:
                stored = self.blobs.put_file(self.source / relpath)
            except OSError as exc:
                yield _reject(
                    row, "unreadable", f"{type(exc).__name__}: {exc}", self.source / relpath
                )
                rejected += 1
                continue
            if stored.created:
                self.new_blob_bytes += stored.size_bytes
            row.update(sha256=stored.sha256, size_bytes=stored.size_bytes)
            row = _inspect(self.blobs.path_for(stored.sha256), relpath, row, self.config)
            if row["status"] == STATUS_ACCEPTED:
                accepted += 1
            else:
                rejected += 1
            yield row

            if index % _PROGRESS_EVERY == 0 or index == total:
                elapsed = max(time.monotonic() - started, 1e-9)
                self.sink.emit(
                    stage=STAGE,
                    substage="read_metadata",
                    message=f"Reading metadata {index:,}/{total:,}",
                    step=index,
                    total=total,
                    metrics={
                        "files_per_s": round(index / elapsed, 1),
                        "accepted": accepted,
                        "rejected": rejected,
                    },
                )


def run_ingest(
    *,
    source: Path,
    data_dir: Path,
    config: IngestConfig,
    derive: VersionDeriver,
    sink: EventSink,
) -> IngestResult:
    """Ingest ``source`` and commit the result as a raw (stage 0) version."""
    started = time.monotonic()
    sink.emit(stage=STAGE, substage="scan", message="Scanning folder…")
    relpaths = scan(source)
    sink.emit(
        stage=STAGE,
        substage="scan",
        message=f"Scanning folder… {len(relpaths):,} files found",
        metrics={"files": len(relpaths)},
    )
    ingest = _Ingest(source, BlobStore(data_dir), config, sink)
    resolved: dict[str, Any] = config.model_dump(mode="json")
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=None,
        rows=ingest.rows(relpaths),
        config=resolved,
        derive=derive,
        sink=sink,
    )
    return IngestResult(record, ingest.new_blob_bytes, round(time.monotonic() - started, 3))
