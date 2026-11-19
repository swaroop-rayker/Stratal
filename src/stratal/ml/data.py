"""A tabular split as columns, for classical models and their checks (ADR-0019 rules 13 and 17).

PyArrow only (a core dependency), so ``api`` can compute :class:`DataFacts` for
the rules that depend on the data, and the workers can hand scikit-learn a
table with named columns (scikit-learn 1.9 takes PyArrow tables; pandas is not
installed).
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from stratal.core.errors import StratalError
from stratal.data.dataset_types import TableSchema
from stratal.data.records import table_schema_of, table_task
from stratal.ml.params import DataFacts, SupervisedTask
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows


@dataclass
class SplitTable:
    """The accepted rows of one split: the features as columns, and the targets."""

    columns: dict[str, list[Any]] = field(default_factory=lambda: dict[str, list[Any]]())
    target: list[Any] = field(default_factory=lambda: list[Any]())

    def __len__(self) -> int:
        return len(self.target)


@dataclass(frozen=True)
class TabularData:
    schema: TableSchema
    task: SupervisedTask
    splits: dict[str, SplitTable]

    @property
    def features(self) -> list[str]:
        return [c.name for c in self.schema.columns if c.role == "feature"]

    @property
    def numeric(self) -> list[str]:
        return [c.name for c in self.schema.columns if c.role == "feature" and c.kind == "numeric"]

    @property
    def categorical(self) -> list[str]:
        return [
            c.name for c in self.schema.columns if c.role == "feature" and c.kind == "categorical"
        ]

    @property
    def classes(self) -> list[str]:
        """The train split's classes, sorted; empty for regression."""
        if self.task != "classification":
            return []
        return sorted({str(v) for v in self.splits["train"].target})

    def arrow(self, split: str) -> Any:
        """The split's features as a PyArrow table: numbers as float64, categories as strings."""
        return to_arrow(self.splits[split].columns, self.numeric, self.categorical)

    def facts(self) -> DataFacts:
        train = self.splits["train"]
        width = len(self.numeric) + sum(
            len({v for v in train.columns[name] if v is not None}) + 1 for name in self.categorical
        )
        return DataFacts(train_rows=len(train), encoded_width=width)


def to_arrow(
    columns: Mapping[str, Sequence[Any]], numeric: Sequence[str], categorical: Sequence[str]
) -> Any:
    arrays = [pa.array(list(columns[n]), type=pa.float64()) for n in numeric]
    arrays += [pa.array(list(columns[n]), type=pa.string()) for n in categorical]
    return pa.Table.from_arrays(arrays, names=[*numeric, *categorical])


def records_table(
    records: Iterable[Mapping[str, Any]], numeric: Sequence[str], categorical: Sequence[str]
) -> Any:
    """Records (values by column name) as a table, e.g. the one record ``/predict`` scores."""
    rows = list(records)
    columns = {n: [r.get(n) for r in rows] for n in [*numeric, *categorical]}
    return to_arrow(columns, numeric, categorical)


def load_tabular(manifest: Path) -> TabularData:
    """A tabular split version's accepted rows, by split."""
    schema = table_schema_of(manifest)
    if schema is None:
        raise StratalError(
            f"{manifest.name} carries no table schema; it is not a tabular version.",
            suggested_action="Train classical models on a split of a tabular dataset.",
        )
    task = table_task(schema)
    features = [c.name for c in schema.columns if c.role == "feature"]
    splits: dict[str, SplitTable] = {
        s: SplitTable({n: [] for n in features}) for s in ("train", "val", "test")
    }
    columns = ["status", "split", "record", "label", "target_value"]
    for row in iter_rows(manifest, columns=columns):
        if row["status"] != STATUS_ACCEPTED or row["split"] not in splits:
            continue
        values: dict[str, Any] = json.loads(row["record"]) if row["record"] else {}
        part = splits[str(row["split"])]
        for name in features:
            part.columns[name].append(values.get(name))
        part.target.append(row["label"] if task == "classification" else row["target_value"])
    if not splits["train"]:
        raise StratalError(
            f"{manifest.name} has no accepted train rows.",
            suggested_action="Check the split's proportions and the clean stage's rejections.",
        )
    return TabularData(schema, task, splits)
