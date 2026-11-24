"""DATA-7.1, DATA-7.2, DATA-7.4: the transform's operations, checks and row step.

ADR-0019 Addendum B rules 26, 27 and 30. The pure module only; the stage, its
version IDs and the raw manifest staying byte-identical are tested with the
stage (test_DATA_7_1_* in tests/integration).
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stratal.data import expressions, transform
from stratal.data.dataset_types import Column, TableSchema
from stratal.data.records import canonical_json, content_hash, schema_from_json, schema_to_json
from stratal.data.transform import RowStep, TransformConfig, TransformRefused, plan
from stratal.storage.manifest import Row

WINE = TableSchema(
    (
        Column("alcohol", "numeric"),
        Column("ash", "numeric"),
        Column("magnesium", "numeric"),
        Column("od280/od315_of_diluted_wines", "numeric"),
        Column("cultivar", "categorical", "target"),
    )
)


def _row(key: int, features: dict[str, Any], label: str | None, **extra: Any) -> Row:
    record = canonical_json(features)
    row: Row = {
        "row_key": f"wine.csv#{key:07d}",
        "record": record,
        "sha256": content_hash(record),
        "size_bytes": len(record),
        "label": label,
        "target_value": None,
        "status": "accepted",
        "decided_at_stage": None,
        "reason_code": None,
        "reason_detail": None,
    }
    row.update(extra)
    return row


ROW1 = _row(
    1,
    {"alcohol": 14.23, "ash": 2.43, "magnesium": 127.0, "od280/od315_of_diluted_wines": 3.92},
    "class_0",
)


def _ops(*raw: dict[str, Any]) -> list[Any]:
    return list(TransformConfig.model_validate({"operations": list(raw)}).operations)


def _run(schema: TableSchema, ops: list[Any], rows: list[Row]) -> tuple[list[Row], RowStep]:
    step = RowStep(plan(schema, ops))
    return [step(r) for r in rows], step


def _refused(schema: TableSchema, *raw: dict[str, Any]) -> list[Any]:
    with pytest.raises(TransformRefused) as caught:
        plan(schema, _ops(*raw))
    return caught.value.errors


# ---------------------------------------------------------------------------- DATA-7.1


@pytest.mark.parametrize("name", transform.FITTED)
def test_DATA_7_1_fitted_operations_refused_with_a_pointer(name: str) -> None:
    with pytest.raises(ValidationError) as caught:
        TransformConfig.model_validate({"operations": [{"op": name, "columns": ["ash"]}]})
    error = caught.value.errors()[0]
    assert error["type"] == "fitted_operation"
    assert error["loc"] == ("operations", 0)
    assert f"{name} is fitted on the data" in error["msg"]
    assert "model's preprocessing" in error["msg"] and "ML-1.3" in error["msg"]


def test_DATA_7_1_same_operations_same_config_one_change_another() -> None:
    a = TransformConfig.model_validate({"operations": [{"op": "drop", "columns": ["ash"]}]})
    b = TransformConfig.model_validate({"operations": [{"op": "drop", "columns": ["ash"]}]})
    c = TransformConfig.model_validate({"operations": [{"op": "drop", "columns": ["alcohol"]}]})
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert a.model_dump(mode="json") != c.model_dump(mode="json")


def test_DATA_7_1_an_empty_list_is_refused() -> None:
    with pytest.raises(ValidationError):
        TransformConfig.model_validate({"operations": []})


def test_DATA_7_1_the_row_step_does_not_change_its_input() -> None:
    before = dict(ROW1)
    _run(WINE, _ops({"op": "drop", "columns": ["ash"]}), [ROW1])
    assert before == ROW1


# ---------------------------------------------------------------------------- DATA-7.2


def test_DATA_7_2_drop() -> None:
    [out], _ = _run(WINE, _ops({"op": "drop", "columns": ["ash"]}), [ROW1])
    assert "ash" not in json.loads(out["record"])
    assert out["status"] == "accepted"


def test_DATA_7_2_rename() -> None:
    ops = _ops({"op": "rename", "from": "od280/od315_of_diluted_wines", "to": "od280_od315"})
    [out], step = _run(WINE, ops, [ROW1])
    assert json.loads(out["record"])["od280_od315"] == 3.92
    assert [c.name for c in step.plan.output.columns][3] == "od280_od315"


def test_DATA_7_2_reorder() -> None:
    order = ["cultivar", "magnesium", "alcohol", "ash", "od280/od315_of_diluted_wines"]
    _, step = _run(WINE, _ops({"op": "reorder", "columns": order}), [ROW1])
    assert [c.name for c in step.plan.output.columns] == order


def test_DATA_7_2_set_kind_to_categorical_gives_the_numbers_text() -> None:
    [out], step = _run(
        WINE, _ops({"op": "set_kind", "column": "magnesium", "kind": "categorical"}), [ROW1]
    )
    assert json.loads(out["record"])["magnesium"] == "127"
    assert next(c for c in step.plan.output.columns if c.name == "magnesium").kind == "categorical"
    ash = _run(WINE, _ops({"op": "set_kind", "column": "ash", "kind": "categorical"}), [ROW1])[0][0]
    assert json.loads(ash["record"])["ash"] == "2.43"


def test_DATA_7_2_set_kind_to_numeric_rejects_cast_failed_keeps_and_counts() -> None:
    schema = TableSchema((Column("code", "categorical"), Column("y", "categorical", "target")))
    rows = [
        _row(1, {"code": "12"}, "a"),
        _row(2, {"code": "x7"}, "b"),
        _row(3, {"code": None}, "a"),
    ]
    out, step = _run(schema, _ops({"op": "set_kind", "column": "code", "kind": "numeric"}), rows)
    assert [r["status"] for r in out] == ["accepted", "rejected", "accepted"]
    assert json.loads(out[0]["record"])["code"] == 12.0
    assert out[1]["reason_code"] == "cast_failed" and out[1]["decided_at_stage"] == "transform"
    assert "'x7'" in out[1]["reason_detail"] and json.loads(out[1]["record"])["code"] == "x7"
    assert step.counts.steps[0].cast_failed == 1
    assert len(out) == len(rows)


def test_DATA_7_2_set_target_makes_the_old_target_a_feature() -> None:
    [out], step = _run(WINE, _ops({"op": "set_target", "column": "alcohol"}), [ROW1])
    target = next(c for c in step.plan.output.columns if c.role == "target")
    assert target.name == "alcohol" and target.kind == "numeric"
    assert out["target_value"] == 14.23 and out["label"] is None
    assert json.loads(out["record"])["cultivar"] == "class_0"


def test_DATA_7_2_a_new_target_readmits_rows_rejected_only_for_the_old_one() -> None:
    empty_old = _row(
        2,
        {"alcohol": 13.2, "ash": 2.14, "magnesium": 100.0, "od280/od315_of_diluted_wines": 3.4},
        None,
        status="rejected",
        decided_at_stage="ingest",
        reason_code="missing_target",
        reason_detail="record 2: the target 'cultivar' is empty",
    )
    other = dict(empty_old, row_key="wine.csv#0000003", reason_code="not_a_number")
    empty_new = _row(
        4,
        {"alcohol": None, "ash": 2.0, "magnesium": 90.0, "od280/od315_of_diluted_wines": 3.0},
        "class_1",
    )
    out, step = _run(
        WINE, _ops({"op": "set_target", "column": "alcohol"}), [empty_old, other, empty_new]
    )
    assert out[0]["status"] == "accepted" and out[0]["reason_code"] is None
    assert out[0]["target_value"] == 13.2
    assert out[1]["status"] == "rejected" and out[1]["reason_code"] == "not_a_number"
    assert out[2]["status"] == "rejected" and out[2]["reason_code"] == "missing_target"
    assert step.counts.readmitted == 1


def test_DATA_7_2_set_encoding_writes_the_schema() -> None:
    ops = _ops(
        {"op": "set_kind", "column": "magnesium", "kind": "categorical"},
        {
            "op": "set_encoding",
            "column": "magnesium",
            "encoding": "ordinal",
            "order": ["127", "100"],
        },
        {"op": "set_encoding", "column": "ash", "encoding": "ignore"},
    )
    out = plan(WINE, ops).output
    mg = next(c for c in out.columns if c.name == "magnesium")
    assert mg.encoding == "ordinal" and mg.order == ("127", "100")
    assert next(c for c in out.columns if c.name == "ash").ignored


def test_DATA_7_2_sha256_recomputed_so_a_new_duplicate_shows() -> None:
    twin = _row(
        2,
        {"alcohol": 14.23, "ash": 9.99, "magnesium": 127.0, "od280/od315_of_diluted_wines": 3.92},
        "class_0",
    )
    assert twin["sha256"] != ROW1["sha256"]
    out, _ = _run(WINE, _ops({"op": "drop", "columns": ["ash"]}), [ROW1, twin])
    assert out[0]["sha256"] == out[1]["sha256"] == content_hash(out[0]["record"])
    assert out[0]["size_bytes"] == len(out[0]["record"].encode())


@pytest.mark.parametrize(
    ("raw", "field", "words"),
    [
        ({"op": "drop", "columns": ["acidity"]}, "columns", "acidity is not a column"),
        ({"op": "drop", "columns": ["cultivar"]}, "columns", "cultivar is the target"),
        ({"op": "rename", "from": "ash", "to": "alcohol"}, "to", "alcohol already exists"),
        ({"op": "rename", "from": "acidity", "to": "x"}, "from", "acidity is not a column"),
        ({"op": "reorder", "columns": ["ash", "alcohol"]}, "columns", "magnesium"),
        ({"op": "set_target", "column": "cultivar"}, "column", "cultivar is already the target"),
        ({"op": "set_kind", "column": "acidity", "kind": "numeric"}, "column", "acidity"),
        (
            {"op": "set_encoding", "column": "ash", "encoding": "one_hot"},
            "encoding",
            "ash is numeric",
        ),
        (
            {"op": "set_encoding", "column": "cultivar", "encoding": "ignore"},
            "column",
            "cultivar is the target",
        ),
    ],
)
def test_DATA_7_2_invalid_operations_refused_naming_the_column(
    raw: dict[str, Any], field: str, words: str
) -> None:
    [problem] = _refused(WINE, {"op": "drop", "columns": ["od280/od315_of_diluted_wines"]}, raw)
    assert problem["loc"] == ["stages", "transform", "operations", 1, field]
    assert words in problem["msg"]


def test_DATA_7_2_ordinal_needs_its_order() -> None:
    problems = _refused(
        WINE,
        {"op": "set_kind", "column": "magnesium", "kind": "categorical"},
        {"op": "set_encoding", "column": "magnesium", "encoding": "ordinal"},
    )
    assert problems[0]["loc"][-1] == "order"


def test_DATA_7_2_columns_follow_the_steps() -> None:
    checked = plan(
        WINE,
        _ops(
            {"op": "rename", "from": "ash", "to": "ash2"},
            {"op": "drop", "columns": ["ash2"]},
        ),
    )
    assert [c.name for c in checked.steps[0].schema.columns][1] == "ash2"
    assert "ash2" not in {c.name for c in checked.output.columns}


def test_DATA_7_2_encodings_round_trip_and_defaults_write_nothing() -> None:
    assert '"encoding"' not in schema_to_json(WINE)
    out = plan(WINE, _ops({"op": "set_encoding", "column": "ash", "encoding": "ignore"})).output
    assert schema_from_json(schema_to_json(out)) == out


# ---------------------------------------------------------------------------- DATA-7.4

COLS: dict[str, Any] = {"alcohol": "numeric", "ash": "numeric", "colour": "categorical"}


@pytest.mark.parametrize(
    ("text", "row", "value"),
    [
        ("alcohol * 2 + ash", {"alcohol": 14.0, "ash": 2.5}, 30.5),
        ("alcohol / ash", {"alcohol": 1.0, "ash": 0.0}, None),
        ("log(ash)", {"ash": -1.0}, None),
        ("sqrt(ash)", {"ash": 9.0}, 3.0),
        ("round(alcohol / 3, 2)", {"alcohol": 10.0}, 3.33),
        ("max(alcohol, ash, 3)", {"alcohol": 1.0, "ash": 2.0}, 3.0),
        ("abs(-ash)", {"ash": 2.0}, 2.0),
        ("alcohol + 1", {"alcohol": None}, None),
        ("colour == 'red' and alcohol > 13", {"colour": "red", "alcohol": 14.0}, True),
        ("isnull(alcohol) or alcohol > 13", {"alcohol": None}, True),
        ("not (ash <= 2)", {"ash": 2.5}, True),
        ("12 < alcohol < 14", {"alcohol": 13.0}, True),
        ('col("ash") > 2', {"ash": 2.5}, True),
    ],
)
def test_DATA_7_4_expressions_compute_as_written(
    text: str, row: dict[str, Any], value: Any
) -> None:
    assert expressions.evaluate(expressions.parse(text, COLS), row) == value


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("__import__('os')", "calls to __import__()"),
        ("alcohol.real", "attribute access"),
        ("alcohol[0]", "subscripts"),
        ("[a for a in ash]", "comprehensions"),
        ("(lambda: 1)()", "calls to anything"),
        ("(x := 1)", "assignments"),
        ("eval('1')", "calls to eval()"),
        ("acidity > 1", "'acidity' is not a column"),
        ("colour + 1", "needs a number, not a string"),
        ("colour < 'b'", "compare strings with == or !="),
        ("alcohol is None", "'is'"),
        ("a" * 501, "at most 500"),
        ("+".join(["ash"] * 60), "at most 100"),
    ],
)
def test_DATA_7_4_anything_outside_the_language_is_refused(text: str, words: str) -> None:
    with pytest.raises(expressions.ExpressionRefused) as caught:
        expressions.parse(text, COLS)
    assert words in str(caught.value)


def test_DATA_7_4_no_expression_reaches_eval() -> None:
    """A scan of the module, as NFR-8's no-unpickling scan: no call to eval, exec, compile or
    __import__, and no import beyond the ones it needs."""
    tree = ast.parse(Path(expressions.__file__).read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "getattr", "globals", "locals"}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & banned
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert imports <= {"ast", "math", "collections", "dataclasses", "typing"}


def test_DATA_7_4_derive_and_filter_rows() -> None:
    rows = [
        _row(
            k,
            {"alcohol": a, "ash": 2.0, "magnesium": 100.0, "od280/od315_of_diluted_wines": 3.0},
            "c",
        )
        for k, a in [(1, 14.0), (2, 12.5), (3, None)]
    ]
    out, step = _run(
        WINE,
        _ops(
            {"op": "derive", "name": "ratio", "expression": "alcohol / ash"},
            {"op": "filter", "expression": "alcohol > 13"},
        ),
        rows,
    )
    assert [json.loads(r["record"])["ratio"] for r in out] == [7.0, 6.25, None]
    assert [r["status"] for r in out] == ["accepted", "filtered", "filtered"]
    assert out[1]["reason_code"] == out[2]["reason_code"] == "transform_filter"
    assert step.counts.steps[1].filtered == 2 and step.counts.steps[1].filtered_null == 1
    assert next(c for c in step.plan.output.columns if c.name == "ratio").kind == "numeric"


def test_DATA_7_4_filter_needs_a_condition_and_derive_a_new_name() -> None:
    problems = _refused(
        WINE,
        {"op": "filter", "expression": "alcohol + 1"},
        {"op": "derive", "name": "ash", "expression": "alcohol"},
    )
    assert [p["loc"][-2:] for p in problems] == [[0, "expression"], [1, "name"]]
