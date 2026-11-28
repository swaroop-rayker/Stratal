"""NFR-2 (E-025): the builder's ends of a table come from one streamed pass, not the table.

SCR-08's spec check on a 2-million-row table killed the api: it fitted the training
preprocessing on every train row to learn two numbers. ``table_shape`` keeps only the
distinct categories and classes; these tests hold its shape equal to ``fit``'s, as
ADR-0017 rule 5 requires of the builder and training.
"""

import json
from pathlib import Path
from typing import Literal

import pytest

from stratal.build.io import table_shape
from stratal.data.dataset_types import Column, TableSchema
from stratal.data.records import META_TABLE_SCHEMA, schema_to_json
from stratal.ml.data import load_tabular
from stratal.storage.manifest import Row, write_manifest
from stratal.train.table_prep import fit

COLOURS = ("red", "white", "rose")
CITIES = tuple(f"city{i}" for i in range(40))


def _schema(target_kind: Literal["numeric", "categorical"]) -> TableSchema:
    return TableSchema(
        (
            Column("alcohol", "numeric"),
            Column("ash", "numeric", encoding="ignore"),
            Column("quality", "categorical", encoding="ordinal", order=("low", "mid", "high")),
            Column("colour", "categorical"),
            Column("city", "categorical", encoding="embedding"),
            Column("y", target_kind, "target"),
        )
    )


def _manifest(tmp_path: Path, *, regression: bool) -> Path:
    def rows() -> list[Row]:
        out: list[Row] = []
        for i in range(300):
            split = ("train", "train", "val", "test")[i % 4]
            record = {
                "alcohol": 10.0 + i % 7,
                "ash": float(i),
                "quality": ("low", "mid", "high", None)[i % 4],
                "colour": COLOURS[i % 3] if i % 11 else None,
                "city": CITIES[i % 40],
            }
            if split != "train":
                record["colour"] = "green"  # seen only outside train: not a category
            rejected = i % 13 == 0
            out.append(
                {
                    "row_key": f"t.csv#{i:07d}",
                    "sha256": "a" * 64,
                    "status": "rejected" if rejected else "accepted",
                    "decided_at_stage": "clean" if rejected else None,
                    "reason_code": "missing_value" if rejected else None,
                    "split": None if rejected else split,
                    "record": json.dumps(record),
                    "label": None if regression else ("a", "b", "c")[i % 3],
                    "target_value": float(i % 9) if regression else None,
                }
            )
        return out

    path = tmp_path / "split.parquet"
    schema = _schema("numeric" if regression else "categorical")
    write_manifest(path, rows(), metadata={META_TABLE_SCHEMA: schema_to_json(schema)})
    return path


@pytest.mark.parametrize("regression", [False, True])
def test_NFR_2_table_shape_equals_fit(tmp_path: Path, regression: bool) -> None:
    path = _manifest(tmp_path, regression=regression)

    shape, fitted = table_shape(path), fit(load_tabular(path))

    assert shape.width == fitted.width and shape.width > 0
    assert shape.outputs == fitted.outputs
    assert shape.classes == fitted.classes
    assert shape.task == fitted.task
    assert shape.names == fitted.names
    assert shape.categorical == fitted.categorical
    assert shape.embedded == fitted.embedded
    assert [c.order for c in shape.ordinal] == [c.order for c in fitted.ordinal]
    assert shape.columns() == fitted.columns()


def test_NFR_2_table_shape_skips_rows_outside_train(tmp_path: Path) -> None:
    shape = table_shape(_manifest(tmp_path, regression=False))

    [colour] = shape.categorical
    assert colour.categories == ("red", "rose", "white")
    assert shape.classes == ("a", "b", "c")
