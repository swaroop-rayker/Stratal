"""Stage 0: ingest a folder of files into the blob store (DATA-1.1 to DATA-1.3).

Every file under the source folder becomes exactly one manifest row. Nothing is
skipped silently: a file that cannot be read, is not an image, is corrupt or is
too small still gets a row, with status ``rejected`` and a reason.

With ``label_from: folder`` the first folder of each file's path is its class
label, and the version is a labelled-images dataset (DATA-5.2, ADR-0014 rule 3).
With ``label_from: {csv: ...}`` the labels come from a CSV in the source folder
that maps each image's path to its class (DATA-1.5).

With ``table`` or ``text`` the source holds records rather than images: one CSV
or Parquet table, or JSONL and TXT files (DATA-1.4, ADR-0019). Each record is
one row; see :mod:`stratal.data.tables` and :mod:`stratal.data.texts`.
"""

import csv
import heapq
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratal.core.errors import SourceNotFound, ValidationFailed
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.records import LABELLED_TEXT, META_TABLE_SCHEMA, TABULAR, schema_to_json
from stratal.data.tables import TableIngest, build_schema, find_table, read_table, table_rows
from stratal.data.texts import TextIngest, text_rows
from stratal.data.versioning import VersionDeriver
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

STAGE = "ingest"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_PROGRESS_EVERY = 500
_EXAMPLES = 20
"""How many rows of each labels-CSV problem the ingest report names; the counts are complete."""


class CsvLabels(BaseModel):
    """Labels for images from a CSV in the source folder (DATA-1.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    csv: str = Field(
        min_length=1,
        description="The CSV's path, relative to the source folder. It is not ingested itself.",
    )
    path_column: str = Field(
        default="path",
        min_length=1,
        description="Column with each image's path, relative to the source folder.",
    )
    label_column: str = Field(
        default="label", min_length=1, description="Column with each image's class."
    )


class IngestConfig(BaseModel):
    """Settings for the ingest stage. The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_size: int = Field(
        default=256,
        ge=1,
        le=16384,
        description="Images whose shorter side is below this many pixels are rejected (DATA-1.3).",
    )
    label_from: Literal["folder"] | CsvLabels | None = Field(
        default=None,
        description="Where each image's class label comes from. 'folder': the first folder "
        "of its path, e.g. 'airplane' for airplane/train-00001.png; a file at the source's "
        "root is rejected as no_label. {csv, path_column, label_column}: a CSV mapping paths "
        "to labels; an image with no row is rejected as no_label (DATA-1.5). Null: no labels "
        "(DATA-5.2, ADR-0014).",
    )
    table: TableIngest | None = Field(
        default=None,
        description="The source is one table (CSV or Parquet): a tabular dataset (DATA-1.4).",
    )
    text: TextIngest | None = Field(
        default=None,
        description="The source is JSONL or TXT files: a labelled-text dataset (DATA-1.4).",
    )

    @model_validator(mode="after")
    def _one_kind(self) -> "IngestConfig":
        kinds = [n for n in ("label_from", "table", "text") if getattr(self, n) is not None]
        if len(kinds) > 1:
            raise ValueError(f"give at most one of label_from, table and text, not {kinds}")
        return self

    def resolved(self) -> dict[str, Any]:
        """The config as recorded in the version.

        Unset optional fields are left out, so versions made before a field
        existed keep their config, and so their ID (DATA-3.2).
        """
        return self.model_dump(mode="json", exclude_none=True)


LABELLED_IMAGES = "labelled_images"


def config_dtype(config: IngestConfig) -> str | None:
    """The dataset type an ingest config makes, or ``None`` for unlabelled images."""
    if config.table is not None:
        return TABULAR
    if config.text is not None:
        return LABELLED_TEXT
    return LABELLED_IMAGES if config.label_from is not None else None


def normalise_path(path: str) -> str:
    """A CSV's path as a source-relative POSIX path: backslashes turned, './' dropped."""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


@dataclass
class LabelMap:
    """A labels CSV, read and checked against the files in the source (DATA-1.5)."""

    labels: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    conflicting: set[str] = field(default_factory=lambda: set[str]())
    rows: int = 0
    problems: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"missing_files": [], "repeated_paths": [], "empty_labels": []}
    )

    def report(self, accepted_labelled: int) -> dict[str, Any]:
        """The ingest summary's ``labels_csv`` part: complete counts, the first rows named."""
        return {
            "rows": self.rows,
            "labelled_images": accepted_labelled,
            **{
                name: {"count": len(found), "examples": found[:_EXAMPLES]}
                for name, found in self.problems.items()
            },
            "conflicting_paths": sorted(self.conflicting)[:_EXAMPLES],
        }


def read_labels_csv(source: Path, config: CsvLabels, files: set[str]) -> LabelMap:
    """Map each image to its class from the CSV (DATA-1.5).

    Nothing in it is dropped silently: a row whose file is missing, a path listed
    twice and an empty label are reported with their line numbers; a path listed
    twice with different labels is a conflict, and the image is rejected.
    """
    path = source / normalise_path(config.csv)
    if not path.is_file():
        raise ValidationFailed(
            f"The labels CSV {config.csv!r} is not in the source folder.",
            suggested_action="Give its path relative to the source folder (DATA-1.5).",
        )
    found = LabelMap()
    seen: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        missing = [c for c in (config.path_column, config.label_column) if c not in columns]
        if missing:
            raise ValidationFailed(
                f"The labels CSV {config.csv!r} has no column {', '.join(map(repr, missing))}; "
                f"its columns are {', '.join(columns)}.",
                suggested_action="Set label_from.path_column and label_from.label_column.",
            )
        for line, record in enumerate(reader, start=2):
            found.rows += 1
            image = normalise_path(record.get(config.path_column) or "")
            label = (record.get(config.label_column) or "").strip()
            if not label:
                found.problems["empty_labels"].append({"line": line, "path": image})
                continue
            if image not in files:
                found.problems["missing_files"].append({"line": line, "path": image})
                continue
            seen[image].append((line, label))
    for image, entries in sorted(seen.items()):
        labels = {label for _, label in entries}
        if len(entries) > 1:
            found.problems["repeated_paths"].append(
                {"lines": [line for line, _ in entries], "path": image, "labels": sorted(labels)}
            )
        if len(labels) > 1:
            found.conflicting.add(image)
        else:
            found.labels[image] = entries[0][1]
    return found


def folder_label(relpath: str) -> str | None:
    """The class label of ``relpath`` under ``label_from: folder``: its first folder."""
    head, sep, _ = relpath.partition("/")
    return head if sep and head else None


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

    def __init__(
        self,
        source: Path,
        blobs: BlobStore,
        config: IngestConfig,
        sink: EventSink,
        labels: LabelMap | None = None,
    ):
        self.source = source
        self.blobs = blobs
        self.config = config
        self.sink = sink
        self.labels = labels
        self.new_blob_bytes = 0
        self.labelled = 0

    def rows(self, relpaths: list[str]) -> Iterator[Row]:
        total = len(relpaths)
        accepted = rejected = 0
        started = time.monotonic()
        for index, relpath in enumerate(relpaths, start=1):
            row: Row = {"row_key": relpath}
            if self.config.label_from == "folder":
                row["label"] = folder_label(relpath)
            elif self.labels is not None:
                row["label"] = self.labels.labels.get(relpath)
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
            if row["status"] == STATUS_ACCEPTED and "label" in row and row["label"] is None:
                if self.labels is None:
                    detail = "file is at the source's root, so it has no class folder"
                    row = _reject(row, "no_label", detail)
                elif relpath in self.labels.conflicting:
                    row = _reject(row, "conflicting_labels", "the labels CSV gives it two labels")
                else:
                    row = _reject(row, "no_label", "the labels CSV has no row for it")
            if row["status"] == STATUS_ACCEPTED and self.labels is not None:
                self.labelled += 1
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
    if config.table is not None or config.text is not None:
        record = _ingest_records(source, relpaths, data_dir, config, derive, sink)
        return IngestResult(record, 0, round(time.monotonic() - started, 3))

    labels: LabelMap | None = None
    if isinstance(config.label_from, CsvLabels):
        csv_path = normalise_path(config.label_from.csv)
        relpaths = [p for p in relpaths if p != csv_path]
        labels = read_labels_csv(source, config.label_from, set(relpaths))
        _announce_label_problems(labels, csv_path, sink)
    ingest = _Ingest(source, BlobStore(data_dir), config, sink, labels)
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=None,
        rows=ingest.rows(relpaths),
        config=config.resolved(),
        derive=derive,
        sink=sink,
    )
    if config.label_from is not None:
        record = replace(record, dtype=LABELLED_IMAGES)
    if labels is not None:
        report = labels.report(ingest.labelled)
        record = replace(record, counts={**record.counts, "labels_csv": report})
    return IngestResult(record, ingest.new_blob_bytes, round(time.monotonic() - started, 3))


def _announce_label_problems(labels: LabelMap, csv_path: str, sink: EventSink) -> None:
    """One WARN per kind of problem in the labels CSV, naming its first line (DATA-1.5)."""
    for name, found in labels.problems.items():
        if not found:
            continue
        first = found[0]
        where = first.get("line") or first.get("lines")
        sink.emit(
            stage=STAGE,
            substage="labels_csv",
            level="WARN",
            message=f"{csv_path}: {len(found):,} {name.replace('_', ' ')}, e.g. line {where} "
            f"({first['path']})",
            reason="Rows of the labels CSV that label no image, or label one twice (DATA-1.5).",
            suggested_action="Fix the CSV and ingest again; the version's counts list them.",
            metrics={f"labels_csv_{name}": len(found)},
        )


def _ingest_records(
    source: Path,
    relpaths: list[str],
    data_dir: Path,
    config: IngestConfig,
    derive: VersionDeriver,
    sink: EventSink,
) -> VersionRecord:
    """A table or a set of texts: one row per record (DATA-1.4, ADR-0019 rules 1 and 3)."""
    metadata: dict[str, str] = {}
    if config.table is not None:
        relpath = find_table(source, relpaths)
        read = read_table(source, relpath)
        schema = build_schema(read, config.table)
        metadata[META_TABLE_SCHEMA] = schema_to_json(schema)
        others: list[Row] = [
            {
                "row_key": p,
                "status": STATUS_REJECTED,
                "decided_at_stage": STAGE,
                "reason_code": "unsupported_format",
                "reason_detail": "not the source's table",
            }
            for p in relpaths
            if p != relpath
        ]
        # Both streams are sorted by row key: the records by number, the other files by path.
        rows: Iterable[Mapping[str, Any]] = heapq.merge(
            table_rows(read, schema, stage=STAGE),
            sorted(others, key=lambda r: str(r["row_key"])),
            key=lambda r: str(r["row_key"]),
        )
        dtype = TABULAR
    else:
        assert config.text is not None
        rows = text_rows(source, relpaths, config.text, stage=STAGE)
        dtype = LABELLED_TEXT
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=None,
        rows=rows,
        config=config.resolved(),
        derive=derive,
        sink=sink,
        metadata=metadata,
    )
    accepted = int(record.counts.get("accepted", 0))
    sink.emit(
        stage=STAGE,
        substage="read_records",
        message=f"Read {record.row_count:,} records · {accepted:,} accepted",
        step=record.row_count,
        total=record.row_count,
        metrics={
            "records": record.row_count,
            "accepted": accepted,
            "rejected": record.row_count - accepted,
        },
    )
    return replace(record, dtype=dtype)
