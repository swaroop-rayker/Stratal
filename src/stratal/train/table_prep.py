"""A network's fitted preprocessing for a table (ADR-0019 rule 13, ADR-0017 rule 6; ML-1.3).

Plain Python, so the API can work out the builder's locked input width from the
train split (BUILD-1.3), the train stage can encode every split, and
``/predict`` can encode one record, all with the same code and numbers.

* Numeric column: the train split's mean, standard deviation and median. A
  missing value becomes the median; every value is then standardised.
* Categorical column: the train split's categories in sorted order, one-hot,
  plus one slot for a category train never had, which a missing value also
  takes. Its width is categories + 1, as :class:`stratal.build.tabular.TabularColumn`
  counts it.
* Ordinal column (DATA-7.3): the category's position in the schema's order, as one
  number; a category outside the order, or missing, takes the train split's median
  position; then standardised like a number (ADR-0019 rule 29).
* Embedding column (DATA-7.3): the category's index among the train split's sorted
  categories, one more for unknown and missing, as one number; the network's
  input layer (:mod:`stratal.train.table_input`) looks it up in an ``Embedding``
  of ``min(50, ⌈n / 2⌉)`` features (ADR-0017 rule 6; ADR-0019 Addendum B, as built).
* A column set to ``ignore`` is left out.
* Target: for classification, the sorted classes; for regression, its mean and
  standard deviation, so the network learns a standardised target and its
  predictions are scaled back.

Fitted on the train split only. Stored as ``preprocess.json`` beside the
weights: numbers and names, nothing to unpickle (NFR-8).
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from stratal.build.tabular import TabularColumn, default_embedding_dim, input_block
from stratal.ml.data import TabularData

PREPROCESS_FILE = "preprocess.json"
FORMAT = "stratal.table_prep/1"


@dataclass(frozen=True)
class NumericColumn:
    name: str
    mean: float
    std: float
    median: float


@dataclass(frozen=True)
class OrdinalColumn:
    name: str
    order: tuple[str, ...]
    mean: float
    std: float
    median: float

    def position(self, value: Any) -> float:
        if value is None:
            return self.median
        try:
            return float(self.order.index(str(value)))
        except ValueError:
            return self.median


@dataclass(frozen=True)
class EmbeddedColumn:
    name: str
    categories: tuple[str, ...]
    dim: int

    @property
    def slots(self) -> int:
        """Rows of its ``Embedding``: the categories and one for unknown and missing."""
        return len(self.categories) + 1


@dataclass(frozen=True)
class CategoricalColumn:
    name: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class TablePrep:
    numeric: tuple[NumericColumn, ...]
    categorical: tuple[CategoricalColumn, ...]
    task: Literal["classification", "regression"]
    classes: tuple[str, ...]
    """Classification: the sorted classes, index order. Regression: empty."""
    target_mean: float = 0.0
    target_std: float = 1.0
    ordinal: tuple[OrdinalColumn, ...] = ()
    embedded: tuple[EmbeddedColumn, ...] = ()

    # ------------------------------------------------------------------ shape
    @property
    def width(self) -> int:
        """The network body's input: after the input layer's embeddings (BUILD-1.3)."""
        return self.lead_width + sum(c.dim for c in self.embedded)

    @property
    def lead_width(self) -> int:
        """Numbers, ordinal positions and one-hot slots: what reaches the body unchanged."""
        return (
            len(self.numeric)
            + len(self.ordinal)
            + sum(len(c.categories) + 1 for c in self.categorical)
        )

    @property
    def raw_width(self) -> int:
        """What :meth:`encode` gives: the lead, then one index per embedding column."""
        return self.lead_width + len(self.embedded)

    @property
    def names(self) -> list[str]:
        """The feature columns in the order they are encoded."""
        return [
            *(c.name for c in self.numeric),
            *(c.name for c in self.ordinal),
            *(c.name for c in self.categorical),
            *(c.name for c in self.embedded),
        ]

    @property
    def outputs(self) -> int:
        """The locked head's width: the classes, or 1 for regression."""
        return len(self.classes) if self.task == "classification" else 1

    def columns(self) -> list[TabularColumn]:
        """The input block's columns (one-hot), for the builder's figures."""
        return (
            [TabularColumn(name=c.name, kind="numeric") for c in [*self.numeric, *self.ordinal]]
            + [
                TabularColumn(name=c.name, kind="categorical", categories=max(1, len(c.categories)))
                for c in self.categorical
            ]
            + [
                TabularColumn(
                    name=c.name,
                    kind="categorical",
                    categories=max(1, len(c.categories)),
                    encoding="embedding",
                    embedding_dim=c.dim,
                )
                for c in self.embedded
            ]
        )

    # ------------------------------------------------------------------ encoding
    def encode(self, columns: Mapping[str, Sequence[Any]]) -> list[list[float]]:
        """Rows of features (values by column name, as lists) as encoded vectors."""
        names = self.names
        count = len(columns[names[0]]) if names else 0
        rows: list[list[float]] = [[] for _ in range(count)]
        for column in self.numeric:
            for i, value in enumerate(columns[column.name]):
                number = column.median if value is None else float(value)
                if not math.isfinite(number):
                    number = column.median
                rows[i].append((number - column.mean) / column.std)
        for column in self.ordinal:
            for i, value in enumerate(columns[column.name]):
                rows[i].append((column.position(value) - column.mean) / column.std)
        for column in self.categorical:
            index = {category: k for k, category in enumerate(column.categories)}
            unknown = len(column.categories)
            for i, value in enumerate(columns[column.name]):
                slot = index.get(str(value), unknown) if value is not None else unknown
                one_hot = [0.0] * (unknown + 1)
                one_hot[slot] = 1.0
                rows[i].extend(one_hot)
        for column in self.embedded:
            index = {category: k for k, category in enumerate(column.categories)}
            unknown = len(column.categories)
            for i, value in enumerate(columns[column.name]):
                slot = index.get(str(value), unknown) if value is not None else unknown
                rows[i].append(float(slot))
        return rows

    def encode_record(self, record: Mapping[str, Any]) -> list[float]:
        return self.encode({n: [record.get(n)] for n in self.names})[0]

    def target(self, values: Sequence[Any]) -> list[int] | list[float]:
        """Training targets: class indices, or the standardised value."""
        if self.task == "classification":
            index = {name: k for k, name in enumerate(self.classes)}
            return [index[str(v)] for v in values]
        return [(float(v) - self.target_mean) / self.target_std for v in values]

    def unscale(self, value: float) -> float:
        """A regression output on the target's own scale."""
        return value * self.target_std + self.target_mean

    # ------------------------------------------------------------------ storage
    def to_json(self) -> str:
        return json.dumps(
            {
                "format": FORMAT,
                "numeric": [
                    {"name": c.name, "mean": c.mean, "std": c.std, "median": c.median}
                    for c in self.numeric
                ],
                "categorical": [
                    {"name": c.name, "categories": list(c.categories)} for c in self.categorical
                ],
                "ordinal": [
                    {
                        "name": c.name,
                        "order": list(c.order),
                        "mean": c.mean,
                        "std": c.std,
                        "median": c.median,
                    }
                    for c in self.ordinal
                ],
                "embedded": [
                    {"name": c.name, "categories": list(c.categories), "dim": c.dim}
                    for c in self.embedded
                ],
                "task": self.task,
                "classes": list(self.classes),
                "target_mean": self.target_mean,
                "target_std": self.target_std,
                "width": self.width,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "TablePrep":
        raw: dict[str, Any] = json.loads(text)
        if raw.get("format") != FORMAT:
            raise ValueError(f"not a Stratal table preprocessing file: {raw.get('format')!r}")
        return cls(
            numeric=tuple(
                NumericColumn(str(c["name"]), float(c["mean"]), float(c["std"]), float(c["median"]))
                for c in raw["numeric"]
            ),
            categorical=tuple(
                CategoricalColumn(str(c["name"]), tuple(str(x) for x in c["categories"]))
                for c in raw["categorical"]
            ),
            task=raw["task"],
            classes=tuple(str(c) for c in raw["classes"]),
            target_mean=float(raw["target_mean"]),
            target_std=float(raw["target_std"]),
            ordinal=tuple(
                OrdinalColumn(
                    str(c["name"]),
                    tuple(str(x) for x in c["order"]),
                    float(c["mean"]),
                    float(c["std"]),
                    float(c["median"]),
                )
                for c in raw.get("ordinal", [])
            ),
            embedded=tuple(
                EmbeddedColumn(
                    str(c["name"]), tuple(str(x) for x in c["categories"]), int(c["dim"])
                )
                for c in raw.get("embedded", [])
            ),
        )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if not ordered:
        return 0.0
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _spread(values: list[float]) -> tuple[float, float]:
    """Mean and population standard deviation; a constant column gets 1, not 0."""
    if not values:
        return 0.0, 1.0
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return mean, std if std > 0 else 1.0


def fit(data: TabularData) -> TablePrep:
    """Fit on the train split only (ML-1.3)."""
    train = data.splits["train"]
    numeric: list[NumericColumn] = []
    for name in data.numeric:
        present = [
            float(v) for v in train.columns[name] if v is not None and math.isfinite(float(v))
        ]
        median = _median(present)
        mean, std = _spread(present)
        numeric.append(NumericColumn(name, mean, std, median))
    ordinal: list[OrdinalColumn] = []
    for name, order in data.ordinal:
        index = {category: float(k) for k, category in enumerate(order)}
        known = [index[str(v)] for v in train.columns[name] if v is not None and str(v) in index]
        median = _median(known) if known else (len(order) - 1) / 2
        positions = [
            index.get(str(v), median) if v is not None else median for v in train.columns[name]
        ]
        mean, std = _spread(positions)
        ordinal.append(OrdinalColumn(name, tuple(order), mean, std, median))

    def seen(name: str) -> tuple[str, ...]:
        return tuple(sorted({str(v) for v in train.columns[name] if v is not None}))

    categorical = [CategoricalColumn(name, seen(name)) for name in data.one_hot]
    embedded = [
        EmbeddedColumn(name, seen(name), default_embedding_dim(max(1, len(seen(name)))))
        for name in data.embedded
    ]
    if data.task == "classification":
        return TablePrep(
            tuple(numeric),
            tuple(categorical),
            "classification",
            tuple(data.classes),
            ordinal=tuple(ordinal),
            embedded=tuple(embedded),
        )
    target_mean, target_std = _spread([float(v) for v in train.target])
    return TablePrep(
        tuple(numeric),
        tuple(categorical),
        "regression",
        (),
        target_mean,
        target_std,
        ordinal=tuple(ordinal),
        embedded=tuple(embedded),
    )


def block_figures(prep: TablePrep) -> tuple[int, int]:
    """``(width, parameters)`` of the input block, as the builder counts it."""
    return input_block(prep.columns())
