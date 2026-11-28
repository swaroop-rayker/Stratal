"""Tabular ingestion: one CSV or Parquet table into manifest rows (DATA-1.4, ADR-0019 rule 1).

The source folder holds one table. Each record becomes one row keyed
``<file>#<record number>``. The ingest config names the target column and may
set any column's kind; the other kinds are inferred: a column whose every
non-empty value is a number is numeric, anything else categorical. The schema
that results is stored with the version (``stratal.table_schema``), and the
target's kind decides the task.

PyArrow reads both formats; it ships without type information, so the unknown
type checks are off for this file, and no PyArrow object leaves it.

A table streams (ADR-0019 Addendum B rule 31): a Parquet file in record batches
of 65,536 rows with its own column types; a CSV in two passes, the first keeping
per column only whether every value is a number (its kind), the second
converting one block at a time. One batch is held at a time.

A table saved as delimited text (``.txt``, ``.tsv``, ``.dat``, ``.data``) is
found when the folder has no ``.csv`` or ``.parquet`` (DATA-1.4.AC2): its
dialect is detected (``stratal.data.delimited``), it is converted once to a
Parquet file under ``converted/`` in the data folder (the sources mount is
read-only), and that file is read like any Parquet table. Its rows keep the text
file's name and the format ``TXT``.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import hashlib
import math
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.compute as _compute
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import ValidationFailed
from stratal.data.dataset_types import Column, TableSchema, table_problem
from stratal.data.delimited import DELIMITED_SUFFIXES, NULL_MARKERS, Dialect, sniff
from stratal.data.records import canonical_json, content_hash
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

pc: Any = _compute  # its functions are generated at import, unknown to pyright

TABLE_FORMATS = {".csv": "CSV", ".parquet": "PARQUET"}
Kind = Literal["numeric", "categorical"]


class TableIngest(BaseModel):
    """How a table is read (DATA-1.4). Part of the ingest config, so of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(
        min_length=1,
        max_length=128,
        description="The column to predict. A categorical target makes the dataset a "
        "classification task; a numeric one regression (ADR-0019 rule 2).",
    )
    kinds: dict[str, Kind] = Field(
        default_factory=lambda: dict[str, Kind](),
        description="Columns whose kind is set rather than inferred, e.g. a numeric code that "
        "is really a category.",
    )


BATCH_ROWS = 65_536
"""Records per batch when a table streams (ADR-0019 Addendum B rule 31)."""
_CSV_BLOCK_BYTES = 8 << 20


@dataclass(frozen=True)
class ReadTable:
    """A table's shape, read without holding its records; :meth:`batches` streams them."""

    relpath: str
    format: str
    names: list[str]
    inferred: dict[str, Kind]
    count: int
    path: Path
    bool_columns: frozenset[str] = frozenset()
    """CSV columns of true/false words only, which PyArrow's inference reads as booleans."""
    empty_columns: frozenset[str] = frozenset()
    """CSV columns with no value at all, which PyArrow's inference reads as nulls."""
    dialect: Dialect | None = None
    """Set when the table was delimited text, converted to the Parquet file at ``path``."""

    @property
    def row_format(self) -> str:
        """The ``format`` its manifest rows record: the source file's, not the converted one's."""
        return "TXT" if self.dialect is not None else self.format

    def batches(self) -> Iterator[list[dict[str, Any]]]:
        """The records in source order: Parquet in :data:`BATCH_ROWS`, a CSV a block at a time."""
        if self.format == "PARQUET":
            for batch in pq.ParquetFile(self.path, pre_buffer=False).iter_batches(
                batch_size=BATCH_ROWS
            ):
                yield batch.to_pylist()
            return
        for batch in _csv_strings(self.path, self.names):
            yield _csv_records(batch, self)


def delimited_tables(source: Path, relpaths: list[str]) -> list[tuple[str, Dialect]]:
    """The folder's delimited-text files that read as tables (DATA-1.4.AC2)."""
    found: list[tuple[str, Dialect]] = []
    for p in relpaths:
        if Path(p).suffix.lower() in DELIMITED_SUFFIXES:
            try:
                dialect = sniff(source / p)
            except OSError:
                continue
            if dialect is not None:
                found.append((p, dialect))
    return found


def find_table(source: Path, relpaths: list[str]) -> str:
    """The one table file of a tabular source: a .csv or .parquet, or, when there is none,
    one delimited-text file that reads as a table (DATA-1.4.AC2)."""
    tables = [p for p in relpaths if Path(p).suffix.lower() in TABLE_FORMATS]
    if not tables:
        tables = [p for p, _ in delimited_tables(source, relpaths)]
    if len(tables) != 1:
        found = ", ".join(tables[:5]) or "none"
        raise ValidationFailed(
            f"A tabular source holds exactly one table (.csv, .parquet, or delimited text such "
            f"as .txt); {source} has {len(tables)} ({found}).",
            suggested_action="Put one table in the folder, or one folder per table. A text "
            "file is read as a table when every line has the same number of fields, separated "
            "by ; tab , or |.",
        )
    return tables[0]


_DEFAULTS = pa_csv.ConvertOptions()
_NULLS: Any = pa.array(list(_DEFAULTS.null_values), type=pa.string())
_WORDS: Any = pa.array(sorted({*_DEFAULTS.true_values, *_DEFAULTS.false_values}), type=pa.string())
_TRUE = frozenset(_DEFAULTS.true_values)


_TEXT_NULLS: Any = pa.array([*_DEFAULTS.null_values, *NULL_MARKERS], type=pa.string())
_MARKERS: Any = pa.array(list(NULL_MARKERS), type=pa.string())


def _options(dialect: Dialect | None, block: int | None = None) -> dict[str, Any]:
    """PyArrow's read (and parse) options for a CSV, or for delimited text."""
    read = pa_csv.ReadOptions(
        column_names=list(dialect.names) if dialect is not None and not dialect.header else None
    )
    if block is not None:
        read.block_size = block
    if dialect is None:
        return {"read_options": read}
    return {"read_options": read, "parse_options": pa_csv.ParseOptions(delimiter=dialect.delimiter)}


def _csv_strings(
    path: Path, names: list[str] | None = None, dialect: Dialect | None = None
) -> Iterator[Any]:
    """A CSV's record batches with every column as text, as written."""
    convert = pa_csv.ConvertOptions(
        column_types={n: pa.string() for n in names} if names is not None else None,
        strings_can_be_null=False,
        null_values=[],
    )
    reader = pa_csv.open_csv(path, convert_options=convert, **_options(dialect, _CSV_BLOCK_BYTES))
    try:
        yield from reader
    finally:
        reader.close()


def _nulled(column: Any, nulls: Any = _NULLS) -> Any:
    """PyArrow's null words (empty, NA, null …) as nulls, as its CSV reader does for numbers."""
    return pc.if_else(pc.is_in(column, value_set=nulls), pa.scalar(None, pa.string()), column)


def _csv_names(path: Path, dialect: Dialect | None = None) -> list[str]:
    if dialect is not None and not dialect.header:
        return list(dialect.names)
    reader = pa_csv.open_csv(
        path,
        convert_options=pa_csv.ConvertOptions(strings_can_be_null=False, null_values=[]),
        **_options(dialect),
    )
    try:
        return [str(n) for n in reader.schema.names]
    finally:
        reader.close()


@dataclass(frozen=True)
class _Scan:
    names: list[str]
    inferred: dict[str, Kind]
    count: int
    bools: frozenset[str]
    empty: frozenset[str]


def _scan_csv(path: Path, dialect: Dialect | None = None) -> _Scan:
    """Pass 1 over a CSV (rule 31): per column only whether every value is a number, a
    true/false word or empty, and the record count. The kinds match what reading the whole
    table with PyArrow inferred before M11b, so version IDs do not change. Delimited text
    (``dialect``) also counts ``?`` as missing."""
    names = _csv_names(path, dialect)
    nulls = _NULLS if dialect is None else _TEXT_NULLS
    numeric = dict.fromkeys(names, True)
    words = dict.fromkeys(names, True)
    seen = dict.fromkeys(names, False)
    count = 0
    for batch in _csv_strings(path, names, dialect):
        count += batch.num_rows
        for i, name in enumerate(names):
            present = _nulled(batch.column(i), nulls).drop_null()
            if not len(present):
                continue
            seen[name] = True
            if numeric[name]:
                try:
                    pc.cast(present, pa.float64())
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    numeric[name] = False
            if words[name]:
                words[name] = bool(pc.all(pc.is_in(present, value_set=_WORDS)).as_py())
    inferred: dict[str, Kind] = {
        n: "numeric" if numeric[n] and seen[n] else "categorical" for n in names
    }
    bools = frozenset(n for n in names if seen[n] and words[n] and not numeric[n])
    empty = frozenset(n for n in names if not seen[n])
    return _Scan(names, inferred, count, bools, empty)


def _csv_records(batch: Any, read: ReadTable) -> list[dict[str, Any]]:
    columns: dict[str, list[Any]] = {}
    for i, name in enumerate(read.names):
        raw = batch.column(i)
        if name in read.empty_columns:
            columns[name] = [None] * batch.num_rows
        elif read.inferred[name] == "numeric":
            columns[name] = pc.cast(_nulled(raw), pa.float64()).to_pylist()
        elif name in read.bool_columns:
            columns[name] = [None if v is None else v in _TRUE for v in _nulled(raw).to_pylist()]
        else:
            columns[name] = raw.to_pylist()
    return [{name: columns[name][k] for name in read.names} for k in range(batch.num_rows)]


def table_columns(path: Path) -> list[str]:
    """A table file's column names, from its header or its schema; no records are read."""
    if TABLE_FORMATS.get(path.suffix.lower()) == "PARQUET":
        return [str(n) for n in pq.ParquetFile(path).schema_arrow.names]
    dialect = None
    if path.suffix.lower() not in TABLE_FORMATS:
        dialect = sniff(path)
        if dialect is None:
            raise ValueError(f"{path.name} is not a delimited table")
    try:
        return _csv_names(path, dialect)
    except pa.ArrowInvalid as exc:
        raise ValueError(str(exc)) from exc


CONVERT_VERSION = 1
"""Part of a converted file's name: raise it when the conversion's output changes."""


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _converted_column(raw: Any, kind: Any, rows: int) -> Any:
    if pa.types.is_null(kind):
        return pa.nulls(rows)
    if pa.types.is_floating(kind):
        return pc.cast(_nulled(raw, _TEXT_NULLS), pa.float64())
    if pa.types.is_boolean(kind):
        words = _nulled(raw, _TEXT_NULLS).to_pylist()
        return pa.array([None if v is None else v in _TRUE for v in words], pa.bool_())
    return _nulled(raw, _MARKERS)


def convert_delimited(path: Path, dialect: Dialect, data_dir: Path) -> Path:
    """Convert delimited text to Parquet once (DATA-1.4.AC2) and return the Parquet file.

    The file is named by the text's content hash, the dialect and :data:`CONVERT_VERSION`,
    so a second ingest of the same file reuses it. As for a CSV, numbers become float64,
    true/false words booleans and a column with no value nulls; the rest stays text. ``?``
    and the CSV null words are missing in a number column, ``?`` in a text column. Two
    passes over the text (rule 31), written under a temporary name and renamed.
    """
    parts = (str(CONVERT_VERSION), _file_hash(path), dialect.delimiter, str(dialect.header))
    key = "\x1f".join((*parts, *dialect.names)).encode("utf-8")
    target = data_dir / "converted" / f"{hashlib.sha256(key).hexdigest()[:32]}.parquet"
    if target.exists():
        return target
    scan = _scan_csv(path, dialect)

    def arrow_type(name: str) -> Any:
        if name in scan.empty:
            return pa.null()
        if scan.inferred[name] == "numeric":
            return pa.float64()
        return pa.bool_() if name in scan.bools else pa.string()

    schema = pa.schema([(name, arrow_type(name)) for name in scan.names])
    staging = data_dir / "tmp" / f"converted-{uuid.uuid4().hex}.parquet"
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pq.ParquetWriter(staging, schema) as writer:
            for batch in _csv_strings(path, scan.names, dialect):
                arrays = [
                    _converted_column(batch.column(i), schema.field(i).type, batch.num_rows)
                    for i in range(len(scan.names))
                ]
                writer.write_batch(pa.RecordBatch.from_arrays(arrays, schema=schema))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def _parquet_table(relpath: str, path: Path, dialect: Dialect | None = None) -> ReadTable:
    parquet = pq.ParquetFile(path)
    inferred: dict[str, Kind] = {}
    for field in parquet.schema_arrow:
        numeric = pa.types.is_integer(field.type) or pa.types.is_floating(field.type)
        inferred[str(field.name)] = "numeric" if numeric else "categorical"
    count = int(parquet.metadata.num_rows)
    return ReadTable(relpath, "PARQUET", list(inferred), inferred, count, path, dialect=dialect)


def _read_delimited(source: Path, relpath: str, data_dir: Path | None) -> ReadTable:
    path = source / relpath
    dialect = sniff(path)
    if dialect is None or data_dir is None:
        raise ValidationFailed(
            f"{relpath} is not a table: its lines do not all split into the same number of "
            "fields by ; tab , or |.",
            suggested_action="Save it as a .csv, or fix the lines that differ.",
        )
    try:
        return _parquet_table(relpath, convert_delimited(path, dialect, data_dir), dialect)
    except (OSError, pa.ArrowInvalid) as exc:
        raise ValidationFailed(
            f"Could not read {relpath} as {dialect.describe()}: {type(exc).__name__}: {exc}"[:500],
            suggested_action="Check that every line has the same number of fields.",
        ) from exc


def read_table(source: Path, relpath: str, data_dir: Path | None = None) -> ReadTable:
    """A table's columns, kinds and record count; its records stream later (rule 31).

    Delimited text is converted to Parquet under ``data_dir`` first (DATA-1.4.AC2)."""
    path = source / relpath
    fmt = TABLE_FORMATS.get(path.suffix.lower())
    if fmt is None:
        return _read_delimited(source, relpath, data_dir)
    try:
        if fmt == "PARQUET":
            return _parquet_table(relpath, path)
        scan = _scan_csv(path)
    except (OSError, pa.ArrowInvalid) as exc:
        raise ValidationFailed(
            f"Could not read {relpath} as {fmt}: {type(exc).__name__}: {exc}"[:500],
            suggested_action="Check that the file is a valid table with a header row.",
        ) from exc
    return ReadTable(
        relpath, fmt, scan.names, scan.inferred, scan.count, path, scan.bools, scan.empty
    )


def build_schema(read: ReadTable, config: TableIngest) -> TableSchema:
    if config.target not in read.inferred:
        raise ValidationFailed(
            f"The target column {config.target!r} is not in {read.relpath}; its columns are "
            f"{', '.join(read.names)}.",
            suggested_action="Set stages.ingest.table.target to one of the table's columns.",
        )
    unknown = sorted(set(config.kinds) - set(read.inferred))
    if unknown:
        raise ValidationFailed(
            f"stages.ingest.table.kinds names columns {read.relpath} does not have: "
            f"{', '.join(unknown)}.",
            suggested_action=f"Its columns are {', '.join(read.names)}.",
        )
    kinds: dict[str, Kind] = {**read.inferred, **config.kinds}
    return TableSchema(
        tuple(
            Column(name, kinds[name], "target" if name == config.target else "feature")
            for name in read.names
        )
    )


def _number(value: Any) -> Any:
    """A numeric column's value as a float; anything that is not a number is left as it is,
    so the schema check rejects it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _category(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int | float):
        return str(value)
    return value


def coerce(value: Any, kind: Kind) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return _number(value) if kind == "numeric" else _category(value)


def _finite_or_none(value: Any) -> Any:
    return value if not isinstance(value, float) or math.isfinite(value) else None


def table_rows(read: ReadTable, schema: TableSchema, *, stage: str) -> Iterator[Row]:
    """One manifest row per record. A row that breaks the schema is rejected with its reason."""
    digits = max(7, len(str(read.count)))
    target = next(c for c in schema.columns if c.role == "target")
    records = (record for batch in read.batches() for record in batch)
    for number, raw in enumerate(records, start=1):
        values = {c.name: coerce(raw.get(c.name), c.kind) for c in schema.columns}
        features = {
            c.name: _finite_or_none(values[c.name]) for c in schema.columns if c.role == "feature"
        }
        record = canonical_json(features)
        row: Row = {
            "row_key": f"{read.relpath}#{number:0{digits}d}",
            "sha256": content_hash(record),
            "size_bytes": len(record.encode("utf-8")),
            "format": read.row_format,
            "record": record,
            "status": STATUS_ACCEPTED,
        }
        problem = table_problem(values, schema)
        goal = values[target.name]
        if target.kind == "categorical" and isinstance(goal, str):
            row["label"] = goal
        elif target.kind == "numeric" and isinstance(goal, float) and math.isfinite(goal):
            row["target_value"] = goal
        if problem is not None:
            code, detail = problem
            row.update(
                status=STATUS_REJECTED,
                decided_at_stage=stage,
                reason_code=code,
                reason_detail=f"record {number}: {detail}"[:500],
            )
        yield row
