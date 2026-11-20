"""Tabular ingestion: one CSV or Parquet table into manifest rows (DATA-1.4, ADR-0019 rule 1).

The source folder holds one table. Each record becomes one row keyed
``<file>#<record number>``. The ingest config names the target column and may
set any column's kind; the other kinds are inferred: a column whose every
non-empty value is a number is numeric, anything else categorical. The schema
that results is stored with the version (``stratal.table_schema``), and the
target's kind decides the task.

PyArrow reads both formats; it ships without type information, so the unknown
type checks are off for this file, and no PyArrow object leaves it.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import ValidationFailed
from stratal.data.dataset_types import Column, TableSchema, table_problem
from stratal.data.records import canonical_json, content_hash
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row

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


@dataclass(frozen=True)
class ReadTable:
    relpath: str
    format: str
    names: list[str]
    inferred: dict[str, Kind]
    records: list[dict[str, Any]]


def find_table(source: Path, relpaths: list[str]) -> str:
    """The one table file of a tabular source."""
    tables = [p for p in relpaths if Path(p).suffix.lower() in TABLE_FORMATS]
    if len(tables) != 1:
        found = ", ".join(tables[:5]) or "none"
        raise ValidationFailed(
            f"A tabular source holds exactly one .csv or .parquet file; {source} has "
            f"{len(tables)} ({found}).",
            suggested_action="Put one table in the folder, or one folder per table.",
        )
    return tables[0]


def read_table(source: Path, relpath: str) -> ReadTable:
    """Read the table whole; tabular datasets here are small (scikit-learn's, NFR-2 noted)."""
    path = source / relpath
    fmt = TABLE_FORMATS[path.suffix.lower()]
    try:
        table: Any = pa_csv.read_csv(path) if fmt == "CSV" else pq.read_table(path)
    except (OSError, pa.ArrowInvalid) as exc:
        raise ValidationFailed(
            f"Could not read {relpath} as {fmt}: {type(exc).__name__}: {exc}"[:500],
            suggested_action="Check that the file is a valid table with a header row.",
        ) from exc
    inferred: dict[str, Kind] = {}
    for field in table.schema:
        numeric = pa.types.is_integer(field.type) or pa.types.is_floating(field.type)
        inferred[str(field.name)] = "numeric" if numeric else "categorical"
    return ReadTable(relpath, fmt, list(inferred), inferred, table.to_pylist())


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
    digits = max(7, len(str(len(read.records))))
    target = next(c for c in schema.columns if c.role == "target")
    for number, raw in enumerate(read.records, start=1):
        values = {c.name: coerce(raw.get(c.name), c.kind) for c in schema.columns}
        features = {
            c.name: _finite_or_none(values[c.name]) for c in schema.columns if c.role == "feature"
        }
        record = canonical_json(features)
        row: Row = {
            "row_key": f"{read.relpath}#{number:0{digits}d}",
            "sha256": content_hash(record),
            "size_bytes": len(record.encode("utf-8")),
            "format": read.format,
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
