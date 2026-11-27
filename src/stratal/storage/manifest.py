"""Parquet manifests: one immutable file per dataset version (DATA-3.1).

The column set and its meaning are specified in docs/DATA_MODEL.md §5. Rows
are written sorted by ``row_key``, in batches, and read back in batches, so a
manifest never has to fit in memory (NFR-2).

PyArrow ships without type information, so the three "unknown type" checks are
switched off for this file only. Everything this module exports is fully typed;
PyArrow objects do not leave it.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import os
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as _compute
import pyarrow.parquet as pq

pc: Any = _compute  # its functions are generated at import, unknown to pyright

SCHEMA_VERSION = 1

Row = dict[str, Any]
"""One manifest row. Keys are the column names below; absent keys are null."""

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_DUPLICATE = "duplicate"
STATUS_FILTERED = "filtered"
STATUSES = (STATUS_ACCEPTED, STATUS_REJECTED, STATUS_DUPLICATE, STATUS_FILTERED)

_FIELDS: list[tuple[str, pa.DataType]] = [
    ("row_key", pa.string()),
    ("sha256", pa.string()),
    ("size_bytes", pa.int64()),
    ("format", pa.string()),
    ("width", pa.int32()),
    ("height", pa.int32()),
    ("status", pa.string()),
    ("decided_at_stage", pa.string()),
    ("reason_code", pa.string()),
    ("reason_detail", pa.string()),
    ("phash", pa.string()),
    ("dup_kind", pa.string()),
    ("dup_group_id", pa.string()),
    ("dup_of", pa.string()),
    ("derived_sha256", pa.string()),
    ("derived_width", pa.int32()),
    ("derived_height", pa.int32()),
    ("caption", pa.string()),
    ("caption_model", pa.string()),
    ("caption_model_revision", pa.string()),
    ("clip_score", pa.float32()),
    ("clip_model", pa.string()),
    ("clip_model_revision", pa.string()),
    ("label", pa.string()),
    ("split", pa.string()),
    ("split_group", pa.string()),
    ("split_stratum", pa.string()),
    # M10 (DATA-1.4, ADR-0019). Nullable and appended, so no existing version ID changes.
    ("record", pa.string()),
    ("target_value", pa.float64()),
    ("text", pa.string()),
    ("text_group", pa.string()),
    ("tokens", pa.string()),
    ("token_count", pa.int32()),
    # M11 (DATA-2.3, ADR-0013): the semantic cluster an accepted row is in. Nullable and
    # appended, as above.
    ("semantic_cluster_id", pa.string()),
    # M12 (DATA-5.3, ADR-0022): a document's title; a chunk's document, section path and
    # character offsets in the cleaned document. Nullable and appended, as above.
    ("title", pa.string()),
    ("document", pa.string()),
    ("section", pa.string()),
    ("char_start", pa.int32()),
    ("char_end", pa.int32()),
]
SCHEMA: pa.Schema = pa.schema(
    [pa.field(name, kind, nullable=name != "row_key") for name, kind in _FIELDS]
)
COLUMNS: tuple[str, ...] = tuple(name for name, _ in _FIELDS)


class ManifestError(ValueError):
    """The rows offered for a manifest break one of its invariants."""


@dataclass
class ManifestStats:
    row_count: int = 0
    by_status: Counter[str] = field(default_factory=lambda: Counter[str]())
    by_reason: Counter[str] = field(default_factory=lambda: Counter[str]())
    bytes_written: int = 0

    def as_counts(self) -> dict[str, Any]:
        """The shape stored in ``dataset_versions.counts``."""
        return {
            **{status: self.by_status.get(status, 0) for status in STATUSES},
            "by_reason": dict(sorted(self.by_reason.items())),
        }


def _checked(row: Mapping[str, Any], previous_key: str | None) -> Row:
    unknown = row.keys() - set(COLUMNS)
    if unknown:
        raise ManifestError(f"unknown manifest columns: {sorted(unknown)}")
    key = row.get("row_key")
    if not isinstance(key, str) or not key:
        raise ManifestError("every row needs a non-empty row_key")
    if previous_key is not None and key <= previous_key:
        raise ManifestError(
            f"rows must be sorted by unique row_key; {key!r} follows {previous_key!r}"
        )
    status = row.get("status")
    if status not in STATUSES:
        raise ManifestError(f"row {key!r} has invalid status {status!r}")
    if status != STATUS_ACCEPTED and not (row.get("decided_at_stage") and row.get("reason_code")):
        raise ManifestError(f"non-accepted row {key!r} needs decided_at_stage and reason_code")
    return {name: row.get(name) for name in COLUMNS}


def write_manifest(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    metadata: Mapping[str, str],
    batch_size: int = 2000,
) -> ManifestStats:
    """Write ``rows`` to ``path`` atomically and return what was written.

    Raises :class:`ManifestError` if rows are unsorted, duplicated, carry an
    unknown column, or break invariant INV-07 (a non-accepted row without a
    deciding stage and reason).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".part")
    file_metadata = {"stratal.schema_version": str(SCHEMA_VERSION), **metadata}
    schema = SCHEMA.with_metadata(file_metadata)
    stats = ManifestStats()
    batch: list[Row] = []
    previous_key: str | None = None

    def flush(writer: pq.ParquetWriter) -> None:
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            batch.clear()

    try:
        with pq.ParquetWriter(staging, schema) as writer:
            for raw in rows:
                row = _checked(raw, previous_key)
                previous_key = row["row_key"]
                stats.row_count += 1
                stats.by_status[row["status"]] += 1
                if row["reason_code"]:
                    stats.by_reason[row["reason_code"]] += 1
                batch.append(row)
                if len(batch) >= batch_size:
                    flush(writer)
            flush(writer)
        with staging.open("rb") as handle:
            os.fsync(handle.fileno())
        stats.bytes_written = staging.stat().st_size
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    return stats


def iter_rows(
    path: Path, *, columns: list[str] | None = None, batch_size: int = 2000
) -> Iterator[Row]:
    """Stream a manifest's rows in file order.

    A requested column that an older manifest lacks (one appended since, such as M11's
    ``semantic_cluster_id``) comes back as null, as a column of the current schema would.
    """
    parquet = pq.ParquetFile(path)
    try:
        present = set(parquet.schema_arrow.names)
        wanted = None if columns is None else [c for c in columns if c in present]
        missing = [] if columns is None else [c for c in columns if c not in present]
        for batch in parquet.iter_batches(batch_size=batch_size, columns=wanted):
            rows = batch.to_pylist()
            if missing:
                for row in rows:
                    row.update(dict.fromkeys(missing))
            yield from rows
    finally:
        parquet.close()


@dataclass(frozen=True)
class RowPage:
    rows: list[Row]
    matched: int
    """Rows matching the filters, over every page."""


def select_rows(
    path: Path,
    *,
    equals: Mapping[str, str],
    key_contains: str | None = None,
    text_contains: str | None = None,
    after_key: str | None = None,
    limit: int,
    not_null: Sequence[str] = (),
) -> RowPage:
    """One page of rows, in ``row_key`` order, that match every filter (DATA-3.1, UI-2.1).

    ``equals`` maps column names to the value they must equal; ``key_contains``
    is a substring of ``row_key``; ``text_contains`` a substring of ``text``, any case
    (a corpus's chunks, DATA-5.3); ``after_key`` is the cursor, the last key of
    the previous page; ``not_null`` names columns that must have a value. The
    manifest is read whole: at 1 to 2 MB per 10,000 rows
    (DATA_MODEL.md §10) that is cheaper than streaming it in batches.
    """
    table = pq.read_table(path)
    mask: Any = None

    def both(condition: Any) -> None:
        nonlocal mask
        mask = condition if mask is None else pc.and_(mask, condition)

    for name, value in equals.items():
        if name not in table.column_names:  # an older manifest: the column is all null
            return RowPage(rows=[], matched=0)
        both(pc.equal(table[name], value))
    for name in not_null:
        if name not in table.column_names:
            return RowPage(rows=[], matched=0)
        both(pc.is_valid(table[name]))
    if key_contains:
        both(pc.match_substring(table["row_key"], key_contains))
    if text_contains:
        if "text" not in table.column_names:
            return RowPage(rows=[], matched=0)
        both(
            pc.fill_null(pc.match_substring(table["text"], text_contains, ignore_case=True), False)
        )
    if mask is not None:
        table = table.filter(mask)
    matched = table.num_rows
    if after_key is not None:
        table = table.filter(pc.greater(table["row_key"], after_key))
    return RowPage(rows=table.slice(0, limit).to_pylist(), matched=matched)


def read_all(path: Path) -> list[Row]:
    """Every row of a manifest, with null for columns an older manifest lacks (SCR-03 v2's
    distributions and diff read whole manifests: 1 to 2 MB per 10,000 rows)."""
    table = pq.read_table(path)
    rows: list[Row] = table.to_pylist()
    missing = [c for c in COLUMNS if c not in table.column_names]
    for row in rows:
        row.update(dict.fromkeys(missing))
    return rows


def read_metadata(path: Path) -> dict[str, str]:
    """Return the ``stratal.*`` key-value metadata of a manifest."""
    raw = pq.read_schema(path).metadata or {}
    decoded = {key.decode(): value.decode() for key, value in raw.items()}
    return {key: value for key, value in decoded.items() if key.startswith("stratal.")}


def corpus_extent(path: Path) -> tuple[int, int]:
    """Documents and their characters behind a chunk manifest's accepted rows (SCR-03's
    corpus facts). Chunks overlap, so a document's length is its last chunk's end."""
    if not {"document", "char_end"} <= set(pq.read_schema(path).names):
        return 0, 0
    table = pq.read_table(path, columns=["status", "document", "char_end"])
    table = table.filter(
        pc.and_(pc.equal(table["status"], "accepted"), pc.is_valid(table["document"]))
    )
    if table.num_rows == 0:
        return 0, 0
    ends = table.group_by("document").aggregate([("char_end", "max")])
    return ends.num_rows, int(pc.sum(ends["char_end_max"]).as_py() or 0)
