"""ML-1.3 for networks: a table's preprocessing is fitted on the train split only and stored
as JSON (ADR-0019 rule 13; ADR-0017 rule 6's input block). Plain Python: runs in dev."""

import math

import pytest

from stratal.data.dataset_types import Column, TableSchema
from stratal.ml.data import SplitTable, TabularData
from stratal.train.table_prep import TablePrep, block_figures, fit


def _data(regression: bool = False) -> TabularData:
    target = Column("y", "numeric" if regression else "categorical", "target")
    schema = TableSchema((Column("a", "numeric"), Column("c", "categorical"), target))
    train = SplitTable(
        {"a": [1.0, 2.0, None, 5.0], "c": ["red", "blue", "red", None]},
        [10.0, 20.0, 30.0, 40.0] if regression else ["yes", "no", "yes", "no"],
    )
    val = SplitTable({"a": [100.0], "c": ["green"]}, [99.0] if regression else ["yes"])
    return TabularData(
        schema,
        "regression" if regression else "classification",
        {"train": train, "val": val, "test": val},
    )


def test_ML_1_3_network_preprocessing_comes_from_the_train_split() -> None:
    prep = fit(_data())

    [a] = prep.numeric
    assert (a.mean, a.median) == (pytest.approx(8 / 3), 2.0), "val's 100 never enters"
    assert a.std == pytest.approx(
        math.sqrt(((1 - 8 / 3) ** 2 + (2 - 8 / 3) ** 2 + (5 - 8 / 3) ** 2) / 3)
    )
    assert prep.categorical[0].categories == ("blue", "red"), "val's green is unknown"
    assert prep.classes == ("no", "yes")
    assert prep.width == 1 + 3 and prep.outputs == 2


def test_ML_1_3_missing_and_unseen_values_take_the_median_and_the_unknown_slot() -> None:
    prep = fit(_data())

    [missing, unseen] = prep.encode({"a": [None, 100.0], "c": [None, "green"]})
    a = prep.numeric[0]
    assert missing[0] == pytest.approx((a.median - a.mean) / a.std)
    assert missing[1:] == [0.0, 0.0, 1.0] and unseen[1:] == [0.0, 0.0, 1.0]
    assert prep.encode_record({"a": 2.0, "c": "blue"})[1:] == [1.0, 0.0, 0.0]


def test_ML_1_3_a_regression_target_is_standardised_and_scaled_back() -> None:
    prep = fit(_data(regression=True))

    assert prep.task == "regression" and prep.outputs == 1 and prep.classes == ()
    scaled = prep.target([10.0, 40.0])
    assert prep.unscale(float(scaled[0])) == pytest.approx(10.0)
    assert prep.target_mean == 25.0


def test_ML_1_3_the_preprocessing_round_trips_as_json() -> None:
    prep = fit(_data())

    again = TablePrep.from_json(prep.to_json())

    assert again == prep
    assert block_figures(prep) == (prep.width, 0), "one-hot: no parameters in the block"
    with pytest.raises(ValueError):
        TablePrep.from_json('{"format": "something else"}')
