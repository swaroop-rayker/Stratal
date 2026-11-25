"""DATA-7.3 in the classical pipeline and /predict (ADR-0019 rule 29; marker ``ml``: worker-cpu).

``ignore`` drops a column, ``ordinal`` is encoded in the order given, ``embedding`` is
one-hot; the fitted pipeline round-trips through skops with the allow-list; /predict
takes a record without its ignored columns.
"""
# pyright: reportMissingImports=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import json
import random
from pathlib import Path

import pytest

from stratal.data.dataset_types import Column, TableSchema
from stratal.data.records import column_to_json
from stratal.ml.data import SplitTable, TabularData
from stratal.ml.params import SklearnSpec

pytestmark = pytest.mark.ml
pytest.importorskip("sklearn")
pytest.importorskip("skops")

from stratal.inference.inputs import check_record  # noqa: E402
from stratal.ml import persist  # noqa: E402
from stratal.ml.pipeline import build_pipeline, fit  # noqa: E402

SCHEMA = TableSchema(
    (
        Column("alcohol", "numeric"),
        Column("ash", "numeric", encoding="ignore"),
        Column("quality", "categorical", encoding="ordinal", order=("low", "mid", "high")),
        Column("colour", "categorical", encoding="embedding"),
        Column("y", "categorical", "target"),
    )
)


def _data() -> TabularData:
    rng = random.Random(0)
    part = SplitTable({"alcohol": [], "quality": [], "colour": []})
    for _ in range(90):
        q = rng.choice(["low", "mid", "high", None])
        part.columns["alcohol"].append(rng.gauss(13, 1))
        part.columns["quality"].append(q)
        part.columns["colour"].append(rng.choice(["red", "white"]))
        part.target.append("good" if q == "high" else "plain")
    return TabularData(SCHEMA, "classification", {"train": part, "val": part, "test": part})


def test_DATA_7_3_ignore_is_absent_and_ordinal_keeps_its_order_in_the_pipeline() -> None:
    data = _data()
    spec = SklearnSpec(kind="sklearn", estimator="logistic_regression", params={})
    pipeline = fit(build_pipeline(spec, data, 0), data, spec)
    transformer = pipeline.named_steps["preprocess"]
    seen = [c for _, _, cols in transformer.transformers_ for c in cols]
    assert "ash" not in seen
    assert sorted(seen) == ["alcohol", "colour", "quality"]
    encoder = transformer.named_transformers_["ordinal:quality"].named_steps["encode"]
    assert [list(c) for c in encoder.categories_] == [["low", "mid", "high"]]
    onehot = transformer.named_transformers_["categorical"].named_steps["encode"]
    assert [list(c) for c in onehot.categories_] == [["red", "white"]]  # embedding as one-hot


def test_DATA_7_3_a_pipeline_with_ordinal_round_trips_through_skops(tmp_path: Path) -> None:
    data = _data()
    spec = SklearnSpec(kind="sklearn", estimator="logistic_regression", params={})
    pipeline = fit(build_pipeline(spec, data, 0), data, spec)
    persist.save(pipeline, tmp_path)
    assert not persist.untrusted_types(tmp_path / persist.MODEL_FILE)
    loaded = persist.load(tmp_path / persist.MODEL_FILE)
    table = data.arrow("test")
    assert list(loaded.predict(table)) == list(pipeline.predict(table))


def test_DATA_7_3_predict_takes_a_record_without_its_ignored_columns() -> None:
    schema = {"columns": [column_to_json(c) for c in SCHEMA.columns]}
    values = check_record({"alcohol": 13.1, "quality": "mid", "colour": "red"}, schema)
    assert "ash" not in values
    also = check_record({"alcohol": 13.1, "ash": 2.4, "quality": "mid", "colour": "red"}, schema)
    assert "ash" not in also
    assert json.dumps(values)
