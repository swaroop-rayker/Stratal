"""DATA-7.3: each feature's encoding reaches the network's preprocessing (ADR-0019 rule 29).

Plain Python (``stratal.train.table_prep`` and ``stratal.ml.data``), so these run in
dev. The classical pipeline's side is ``test_DATA_7_3_classical.py`` (marker ``ml``).
"""

from stratal.data.dataset_types import Column, TableSchema
from stratal.ml.data import SplitTable, TabularData
from stratal.train.table_prep import TablePrep, fit

SCHEMA = TableSchema(
    (
        Column("alcohol", "numeric"),
        Column("ash", "numeric", encoding="ignore"),
        Column("quality", "categorical", encoding="ordinal", order=("low", "mid", "high")),
        Column("colour", "categorical"),
        Column("y", "categorical", "target"),
    )
)


def _data() -> TabularData:
    train = SplitTable(
        {
            "alcohol": [12.0, 13.0, 14.0, 15.0],
            "quality": ["low", "high", "mid", "high"],
            "colour": ["red", "white", "red", "red"],
        },
        ["a", "b", "a", "b"],
    )
    return TabularData(
        SCHEMA, "classification", {"train": train, "val": SplitTable(), "test": SplitTable()}
    )


def test_DATA_7_3_an_ignored_column_is_absent_from_the_model_input() -> None:
    data = _data()
    prep = fit(data)
    assert "ash" not in data.features
    assert "ash" not in prep.names
    assert prep.width == 1 + 1 + (2 + 1)  # alcohol, quality, colour one-hot with unknown
    assert (
        len(prep.encode_record({"alcohol": 13.0, "ash": 99.0, "quality": "mid", "colour": "red"}))
        == prep.width
    )


def test_DATA_7_3_ordinal_keeps_the_order_given() -> None:
    prep = fit(_data())
    [quality] = prep.ordinal
    assert quality.order == ("low", "mid", "high")
    low, mid, high = (
        prep.encode_record({"alcohol": 13.0, "quality": q, "colour": "red"})[1]
        for q in ("low", "mid", "high")
    )
    assert low < mid < high
    unknown = prep.encode_record({"alcohol": 13.0, "quality": "great", "colour": "red"})[1]
    missing = prep.encode_record({"alcohol": 13.0, "quality": None, "colour": "red"})[1]
    assert unknown == missing == (quality.median - quality.mean) / quality.std


def test_DATA_7_3_the_preprocessing_round_trips_with_ordinal_columns() -> None:
    prep = fit(_data())
    assert TablePrep.from_json(prep.to_json()) == prep


def test_DATA_7_3_classical_columns_follow_the_encodings() -> None:
    schema = TableSchema(
        (
            *SCHEMA.columns[:3],
            Column("colour", "categorical", encoding="embedding"),
            SCHEMA.columns[4],
        )
    )
    data = TabularData(schema, "classification", _data().splits)
    assert data.numeric == ["alcohol"]
    assert data.categorical == ["colour"]  # embedding is one-hot for a classical model
    assert data.ordinal == [("quality", ("low", "mid", "high"))]
    assert data.text_columns == ["colour", "quality"]


def test_DATA_7_3_an_embedding_column_reaches_the_input_block() -> None:
    schema = TableSchema(
        (
            Column("alcohol", "numeric"),
            Column("colour", "categorical", encoding="embedding"),
            Column("y", "categorical", "target"),
        )
    )
    data = TabularData(schema, "classification", _data().splits)
    prep = fit(data)
    [colour] = prep.embedded
    assert colour.categories == ("red", "white") and colour.slots == 3
    assert colour.dim == 1  # min(50, ceil(2 / 2))
    assert prep.categorical == ()
    assert (prep.lead_width, prep.raw_width, prep.width) == (1, 2, 2)
    block = prep.columns()[-1]
    assert (block.encoding, block.categories, block.embedding_dim) == ("embedding", 2, 1)
    assert prep.encode_record({"alcohol": 13.0, "colour": "white"})[-1] == 1.0
    assert prep.encode_record({"alcohol": 13.0, "colour": "pink"})[-1] == 2.0
    assert prep.encode_record({"alcohol": 13.0, "colour": None})[-1] == 2.0
    assert TablePrep.from_json(prep.to_json()) == prep
