"""A tabular version as a table of its columns (DATA-7.5; ADR-0019 Addendum B rule 32).

For any tabular version (raw, transformed, cleaned or split), each column's kind,
role and encoding, its missing count, its distinct count and its distribution,
over the version's accepted rows (SCREENS.md open point 6d). The rows stream from
the manifest; per column only a count, the distinct values up to
:data:`DISTINCT_CAP` (then "more than", approved with the *Columns* tab's low-fi),
and, for a number, its values in a compact array of doubles are held.

PyArrow computes the median and the histograms; it ships without type
information, so the unknown type checks are off for this file.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import json
from array import array
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as _compute

from stratal.data.dataset_types import Column, TableSchema
from stratal.data.records import table_schema_of, table_task
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows

pc: Any = _compute

DISTINCT_CAP = 10_000
TOP = 50
SPARK_BINS = 12
DETAIL_BINS = 20

Json = dict[str, Any]


@dataclass
class _Tally:
    column: Column
    missing: int = 0
    distinct: set[Any] = field(default_factory=lambda: set[Any]())
    capped: bool = False
    counts: Counter[str] = field(default_factory=lambda: Counter[str]())
    numbers: array[float] = field(default_factory=lambda: array("d"))

    def add(self, value: Any) -> None:
        if value is None:
            self.missing += 1
            return
        if not self.capped and value not in self.distinct:
            if len(self.distinct) >= DISTINCT_CAP:
                self.capped = True
            else:
                self.distinct.add(value)
        if self.column.kind == "numeric" and isinstance(value, int | float):
            self.numbers.append(float(value))
        elif not self.capped or value in self.counts:
            self.counts[str(value)] += 1


def _histogram(values: Any, low: float, high: float, bins: int) -> list[int]:
    if high <= low:
        return [len(values)] + [0] * (bins - 1)
    index = pc.min_element_wise(
        pc.floor(pc.multiply(pc.divide(pc.subtract(values, low), high - low), bins)),
        pa.scalar(float(bins - 1)),
    )
    found = {int(d["values"]): int(d["counts"]) for d in pc.value_counts(index).to_pylist()}
    return [found.get(k, 0) for k in range(bins)]


def _numeric(tally: _Tally) -> Json:
    if not tally.numbers:
        return {"type": "numeric", "min": None, "median": None, "max": None}
    values = pa.array(tally.numbers, type=pa.float64())
    bounds = pc.min_max(values).as_py()
    low, high = float(bounds["min"]), float(bounds["max"])
    median = float(pc.quantile(values, q=0.5, interpolation="midpoint")[0].as_py())
    step = (high - low) / DETAIL_BINS if high > low else 0.0
    return {
        "type": "numeric",
        "min": low,
        "median": median,
        "max": high,
        "sparkline": _histogram(values, low, high, SPARK_BINS),
        "histogram": {
            "edges": [low + step * k for k in range(DETAIL_BINS + 1)],
            "counts": _histogram(values, low, high, DETAIL_BINS),
        },
    }


def _categorical(tally: _Tally) -> Json:
    ranked = sorted(tally.counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ranked[:TOP]
    return {
        "type": "categorical",
        "top": [{"value": v, "count": c} for v, c in shown],
        "more": max(0, len(ranked) - len(shown)),
    }


def _values(row: dict[str, Any], target: Column) -> Iterator[tuple[str, Any]]:
    record: dict[str, Any] = json.loads(row["record"]) if row.get("record") else {}
    yield from record.items()
    yield target.name, row["label"] if target.kind == "categorical" else row["target_value"]


def summarise(manifest: Path, *, version_id: str) -> Json:
    """The columns of a tabular version, over its accepted rows (rule 32)."""
    schema = table_schema_of(manifest)
    if schema is None:
        raise ValueError(f"{version_id} carries no table schema")
    return _summarise(manifest, schema, version_id)


def _summarise(manifest: Path, schema: TableSchema, version_id: str) -> Json:
    target = next(c for c in schema.columns if c.role == "target")
    tallies = {c.name: _Tally(c) for c in schema.columns}
    counted = 0
    not_counted: Counter[str] = Counter()
    wanted = ["status", "reason_code", "record", "label", "target_value"]
    for row in iter_rows(manifest, columns=wanted):
        if row["status"] != STATUS_ACCEPTED:
            not_counted[str(row["reason_code"] or row["status"])] += 1
            continue
        counted += 1
        for name, value in _values(row, target):
            tally = tallies.get(name)
            if tally is not None:
                tally.add(value)
    columns: list[Json] = []
    for column in schema.columns:
        tally = tallies[column.name]
        distribution = _numeric(tally) if column.kind == "numeric" else _categorical(tally)
        columns.append(
            {
                "name": column.name,
                "kind": column.kind,
                "role": column.role,
                "encoding": column.encoding,
                "order": list(column.order) if column.order is not None else None,
                "missing": tally.missing,
                "distinct": len(tally.distinct),
                "distinct_capped": tally.capped,
                "distribution": distribution,
            }
        )
    return {
        "version_id": version_id,
        "task": table_task(schema),
        "target": target.name,
        "counted": counted,
        "not_counted": dict(sorted(not_counted.items())),
        "distinct_cap": DISTINCT_CAP,
        "columns": columns,
    }
