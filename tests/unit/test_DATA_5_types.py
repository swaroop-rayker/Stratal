"""DATA-5.2 (tabular and labelled text) and DATA-5.5 (families and metrics per type), M9.

Labelled images' schema is tested in ``test_DATA_5_2_labelled_images.py`` (M8).
"""

import math

import pytest
from pydantic import ValidationError

from stratal.build import layers as L
from stratal.build.shapes import Shape
from stratal.build.spec import chain, validate_graph
from stratal.build.tabular import TabularColumn, default_embedding_dim, input_block
from stratal.data.dataset_types import (
    TYPE_INFO,
    Column,
    TableSchema,
    validate_labelled_text,
    validate_tabular,
)

SCHEMA = TableSchema(
    (
        Column("age", "numeric"),
        Column("city", "categorical"),
        Column("churned", "categorical", role="target"),
    )
)


def _row(key: str, **values: object) -> dict[str, object]:
    return {"row_key": key, "status": "accepted", **values}


def test_DATA_5_2_tabular_rows_validate_against_their_schema() -> None:
    rows = [
        _row("ok", age=31, city="Pune", churned="no"),
        _row("missing_feature_is_fine", age=None, city="Goa", churned="yes"),
        _row("no_city", age=40, churned="no"),
        _row("text_age", age="forty", city="Goa", churned="no"),
        _row("nan_age", age=math.nan, city="Goa", churned="no"),
        _row("numeric_city", age=3, city=7, churned="no"),
        _row("no_target", age=3, city="Goa", churned=None),
        {"row_key": "rejected", "status": "rejected"},
    ]

    report = validate_tabular(rows, SCHEMA)

    assert (report.checked, report.valid) == (7, 2)
    assert {p.row_key: p.reason_code for p in report.problems} == {
        "no_city": "missing_column",
        "text_age": "not_a_number",
        "nan_age": "not_a_number",
        "numeric_city": "not_a_category",
        "no_target": "missing_target",
    }


def test_DATA_5_2_a_table_has_one_target_and_unique_columns() -> None:
    with pytest.raises(ValueError, match="one target"):
        TableSchema((Column("a", "numeric"),))
    with pytest.raises(ValueError, match="unique"):
        TableSchema((Column("a", "numeric"), Column("a", "numeric", role="target")))


def test_DATA_5_2_labelled_text_rows_validate_against_their_schema() -> None:
    rows = [
        _row("ok", text="a cat on a mat", label="cat"),
        _row("blank", text="   ", label="cat"),
        _row("long", text="x" * 21, label="cat"),
        _row("unlabelled", text="a dog", label=""),
    ]

    report = validate_labelled_text(rows, max_chars=20)

    assert (report.checked, report.valid) == (4, 1)
    assert [(p.row_key, p.reason_code) for p in report.problems] == [
        ("blank", "empty_text"),
        ("long", "text_too_long"),
        ("unlabelled", "missing_label"),
    ]


def test_DATA_5_5_each_type_declares_families_and_default_metrics() -> None:
    assert set(TYPE_INFO) == {"image_caption", "labelled_images", "tabular", "labelled_text"}
    for info in TYPE_INFO.values():
        assert info.default_metrics, info.name
        assert bool(info.families) == (info.input_kind is not None), info.name
    assert TYPE_INFO["labelled_images"].families == ("mlp", "cnn")
    assert TYPE_INFO["tabular"].families == ("mlp",)
    assert TYPE_INFO["labelled_text"].families == ("mlp", "rnn", "transformer")
    assert [t.name for t in TYPE_INFO.values() if t.trainable] == ["labelled_images"]


def test_DATA_5_5_an_incompatible_family_is_a_problem() -> None:
    text = TYPE_INFO["labelled_text"]
    cnn_on_text = chain("c", L.Embedding(), L.GlobalAvgPool())
    rnn = chain("r", L.Embedding(), L.LSTM())
    tokens = Shape("tokens", (16,), vocab=100)
    tabular = TYPE_INFO["tabular"]

    assert validate_graph(rnn, input_shape=tokens, output_size=2, families=text.families).valid
    assert validate_graph(
        cnn_on_text, input_shape=tokens, output_size=2, families=text.families
    ).valid
    report = validate_graph(rnn, input_shape=tokens, output_size=2, families=tabular.families)
    assert [p.code for p in report.problems] == ["incompatible_family"]
    assert "rnn" in report.problems[0].message and "mlp" in report.problems[0].message


def test_DATA_5_2_the_tabular_input_width_counts_encodings() -> None:
    columns = [
        TabularColumn(name="age", kind="numeric"),
        TabularColumn(name="city", kind="categorical", categories=9),
        TabularColumn(name="shop", kind="categorical", categories=300, encoding="embedding"),
        TabularColumn(
            name="day", kind="categorical", categories=7, encoding="embedding", embedding_dim=3
        ),
    ]

    width, parameters = input_block(columns)

    assert width == 1 + (9 + 1) + 50 + 3, "one-hot keeps a slot for unknown; embeddings their width"
    assert parameters == 301 * 50 + 8 * 3
    assert default_embedding_dim(9) == 5 and default_embedding_dim(1000) == 50


def test_DATA_5_2_tabular_columns_reject_mixed_settings() -> None:
    with pytest.raises(ValidationError, match="numeric"):
        TabularColumn(name="age", kind="numeric", categories=3)
    with pytest.raises(ValidationError, match="no categories"):
        TabularColumn(name="city", kind="categorical")
