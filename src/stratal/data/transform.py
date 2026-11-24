"""Tabular transforms: the operations, their checks and the row step (DATA-7.1 to DATA-7.4).

ADR-0019 Addendum B rules 25 to 32. Plain Python, shared by the ``transform``
stage and the API's preview, so the preview equals the stage's output
(DATA-7.5.AC1).

* :class:`TransformConfig` is the stage's config: an ordered list of operations,
  each an object with an ``op`` (a discriminated union, so SCR-15's form is
  generated from its schema, UI-2.3). Operations fitted on the data are refused
  by name with a pointer to the model's preprocessing (DATA-7.1.AC2).
* :func:`plan` checks the operations against the input's schema, one by one,
  before any row is read, and gives the schema after each step. Every problem
  names its column and carries ``loc`` = ``["stages", "transform",
  "operations", i, field]`` (DATA-7.2.AC1).
* :class:`RowStep` applies a plan to one manifest row: it rebuilds the row's
  values from ``record`` and the old target, applies the operations, and writes
  ``record``, ``label`` or ``target_value``, ``sha256`` and ``size_bytes`` again
  (rule 27). It counts what each operation did.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    WrapValidator,
)
from pydantic_core import PydanticCustomError

from stratal.core.errors import FieldProblem, ValidationFailed
from stratal.data import expressions
from stratal.data.dataset_types import Column, Encoding, TableSchema, table_problem
from stratal.data.records import canonical_json, content_hash, row_values
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_FILTERED, STATUS_REJECTED, Row

STAGE = "transform"
ALGORITHM_VERSION = 1
MAX_OPERATIONS = 100
LOC = ("stages", "transform", "operations")

Kind = Literal["numeric", "categorical"]

REASON_CAST_FAILED = "cast_failed"
REASON_FILTER = "transform_filter"
REASON_MISSING_TARGET = "missing_target"

FITTED = (
    "scale",
    "standardize",
    "normalize",
    "impute",
    "fill_mean",
    "fill_median",
    "target_encode",
    "bin",
)
"""Operations that learn from the data. They belong in the model's preprocessing, fitted on
the train split (ML-1.3), so the transform refuses them by name (DATA-7.1.AC2)."""


# ---------------------------------------------------------------------------- the operations


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


_Name = Annotated[str, Field(min_length=1, max_length=128)]


class Drop(_Op):
    """Removes columns. The target cannot be dropped."""

    op: Literal["drop"]
    columns: list[_Name] = Field(min_length=1, description="The columns to remove.")


class Rename(_Op):
    """Gives a column a new name, which must not exist yet."""

    op: Literal["rename"]
    from_: _Name = Field(alias="from", description="The column's current name.")
    to: _Name = Field(description="Its new name.")


class Reorder(_Op):
    """Sets the order of every column."""

    op: Literal["reorder"]
    columns: list[_Name] = Field(min_length=1, description="Every column, once, in order.")


class SetKind(_Op):
    """Makes a column numeric or categorical. A value that will not parse as a number is
    rejected as cast_failed; a number becomes its text (7.0 → "7")."""

    op: Literal["set_kind"]
    column: _Name
    kind: Kind


class SetTarget(_Op):
    """Chooses the target. The old target becomes a feature."""

    op: Literal["set_target"]
    column: _Name


class SetEncoding(_Op):
    """How a feature reaches a model (DATA-7.3): a category one_hot, embedding or ordinal
    (with every category in order); any feature ignore."""

    op: Literal["set_encoding"]
    column: _Name
    encoding: Encoding
    order: list[Annotated[str, Field(max_length=256)]] | None = Field(
        default=None,
        max_length=10_000,
        description="ordinal only: every category, first to last. A value not listed, or "
        "missing, is treated as unknown.",
    )


class Derive(_Op):
    """A new column computed from the row's columns (DATA-7.4)."""

    op: Literal["derive"]
    name: _Name
    expression: str = Field(min_length=1, max_length=expressions.MAX_CHARS)
    kind: Kind | None = Field(
        default=None,
        description="Leave empty to infer: numeric for arithmetic, categorical for a string.",
    )


class Filter(_Op):
    """Keeps the rows where the condition is true; the others are marked filtered
    (transform_filter). A row where it cannot be computed is dropped too."""

    op: Literal["filter"]
    expression: str = Field(min_length=1, max_length=expressions.MAX_CHARS)


Operation = Annotated[
    Drop | Rename | Reorder | SetKind | SetTarget | SetEncoding | Derive | Filter,
    Field(discriminator="op"),
]


def _refuse_fitted(value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> Any:
    if isinstance(value, Mapping) and value.get("op") in FITTED:  # pyright: ignore[reportUnknownMemberType]
        name = str(value.get("op"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        raise PydanticCustomError(
            "fitted_operation",
            "{name} is fitted on the data, so it belongs in the model's preprocessing, fitted "
            "on the train split (ML-1.3)",
            {"name": name},
        )
    return handler(value)


class TransformConfig(BaseModel):
    """The ``transform`` stage's config (ADR-0019 rule 26). Part of the version's identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: list[Annotated[Operation, WrapValidator(_refuse_fitted)]] = Field(
        min_length=1,
        max_length=MAX_OPERATIONS,
        description="Row-local operations applied in order to a table's raw version.",
    )
    algorithm_version: int = Field(
        default=ALGORITHM_VERSION,
        ge=1,
        json_schema_extra={"readOnly": True},
        description="Version of the transform code; a new value gives a new version ID.",
    )


# ---------------------------------------------------------------------------- the plan


@dataclass(frozen=True)
class Step:
    """One checked operation: the schema after it, and what it does in words."""

    index: int
    op: str
    schema: TableSchema
    summary: str
    expression: expressions.Expression | None = None


@dataclass(frozen=True)
class Plan:
    input: TableSchema
    steps: tuple[Step, ...]
    operations: tuple[Any, ...]

    @property
    def output(self) -> TableSchema:
        return self.steps[-1].schema if self.steps else self.input


class TransformRefused(ValidationFailed):
    """One or more operations do not fit the input's schema (DATA-7.2.AC1)."""


def _problem(i: int, field_name: str, msg: str, code: str = "invalid") -> FieldProblem:
    return FieldProblem(loc=[*LOC, i, field_name], msg=msg, code=code)


def _kinds(columns: Sequence[Column]) -> dict[str, Kind]:
    return {c.name: c.kind for c in columns}


def _index(columns: list[Column], name: str) -> int:
    return next(k for k, c in enumerate(columns) if c.name == name)


def _check(
    i: int, op: Any, columns: list[Column]
) -> tuple[list[Column], str, expressions.Expression | None, list[FieldProblem]]:
    """The columns after ``op``, a sentence on what it does, its parsed expression, and its
    problems (none if it fits)."""
    names = {c.name for c in columns}
    target = next(c for c in columns if c.role == "target")
    problems: list[FieldProblem] = []

    def unknown(name: str, field_name: str) -> bool:
        if name in names:
            return False
        problems.append(_problem(i, field_name, f"{name} is not a column here", "unknown_column"))
        return True

    match op:
        case Drop(columns=dropped):
            seen: set[str] = set()
            for name in dropped:
                if unknown(name, "columns"):
                    continue
                if name in seen:
                    problems.append(_problem(i, "columns", f"{name} is listed twice"))
                if name == target.name:
                    problems.append(
                        _problem(
                            i,
                            "columns",
                            f"{name} is the target and cannot be dropped; choose another "
                            "target first (set_target)",
                            "target_dropped",
                        )
                    )
                seen.add(name)
            after = [c for c in columns if c.name not in seen]
            if not problems and not any(c.role == "feature" for c in after):
                problems.append(_problem(i, "columns", "this would leave no feature"))
            features = sum(1 for c in columns if c.role == "feature")
            left = sum(1 for c in after if c.role == "feature")
            return (
                after,
                f"removes {', '.join(dropped)} · {features} → {left} features",
                None,
                problems,
            )
        case Rename(from_=old, to=new):
            if not unknown(old, "from") and new in names:
                problems.append(_problem(i, "to", f"{new} already exists", "duplicate_column"))
            after = [replace(c, name=new) if c.name == old else c for c in columns]
            return after, f"{old} → {new}", None, problems
        case Reorder(columns=order):
            missing = [c.name for c in columns if c.name not in order]
            extra = [n for n in order if n not in names]
            twice = sorted({n for n in order if order.count(n) > 1})
            if extra:
                problems.append(
                    _problem(
                        i, "columns", f"{', '.join(extra)}: not columns here", "unknown_column"
                    )
                )
            if twice:
                problems.append(_problem(i, "columns", f"{', '.join(twice)}: listed twice"))
            if missing:
                problems.append(
                    _problem(i, "columns", f"every column, once: {', '.join(missing)} missing")
                )
            if problems:
                return columns, "reorders the columns", None, problems
            by_name = {c.name: c for c in columns}
            return [by_name[n] for n in order], "reorders the columns", None, problems
        case SetKind(column=name, kind=kind):
            if unknown(name, "column"):
                return columns, f"{name} → {kind}", None, problems
            k = _index(columns, name)
            old = columns[k]
            changed = replace(old, kind=kind, encoding=None, order=None)
            after = [*columns[:k], changed, *columns[k + 1 :]]
            note = " (its encoding goes back to the default)" if old.encoding is not None else ""
            return after, f"{name}: {old.kind} → {kind}{note}", None, problems
        case SetTarget(column=name):
            if unknown(name, "column"):
                return columns, f"target → {name}", None, problems
            if name == target.name:
                problems.append(_problem(i, "column", f"{name} is already the target"))
            after = [
                replace(c, role="target", encoding=None, order=None)
                if c.name == name
                else replace(c, role="feature")
                if c.role == "target"
                else c
                for c in columns
            ]
            return (
                after,
                f"target {target.name} → {name}; {target.name} becomes a feature",
                None,
                problems,
            )
        case SetEncoding(column=name, encoding=encoding, order=order):
            if unknown(name, "column"):
                return columns, f"{name} → {encoding}", None, problems
            k = _index(columns, name)
            col = columns[k]
            if col.role == "target":
                problems.append(
                    _problem(i, "column", f"{name} is the target; only features have an encoding")
                )
            elif col.kind == "numeric" and encoding != "ignore":
                problems.append(
                    _problem(
                        i,
                        "encoding",
                        f"{name} is numeric; a number takes ignore only (set_kind to categorical "
                        f"first for {encoding})",
                        "encoding_not_allowed",
                    )
                )
            if encoding == "ordinal":
                if not order:
                    problems.append(
                        _problem(i, "order", f"ordinal needs every category of {name}, in order")
                    )
                elif len(set(order)) != len(order):
                    problems.append(_problem(i, "order", "a category is listed twice"))
            elif order is not None:
                problems.append(_problem(i, "order", "order goes with ordinal only"))
            changed = replace(
                col,
                encoding=encoding,
                order=tuple(order) if encoding == "ordinal" and order else None,
            )
            after = [*columns[:k], changed, *columns[k + 1 :]]
            return after, f"{name} → {encoding}", None, problems
        case Derive(name=name, expression=text, kind=kind):
            parsed: expressions.Expression | None = None
            if name in names:
                problems.append(_problem(i, "name", f"{name} already exists", "duplicate_column"))
            try:
                parsed = expressions.parse(text, _kinds(columns))
            except expressions.ExpressionRefused as exc:
                problems.append(_problem(i, "expression", str(exc), "expression_refused"))
            out_kind: Kind = kind or "numeric"
            if parsed is not None:
                if parsed.type == "categorical" and kind == "numeric":
                    problems.append(
                        _problem(i, "kind", "the expression gives a string, not a number")
                    )
                if kind is None:
                    out_kind = "numeric" if parsed.type == "numeric" else "categorical"
            after = [*columns, Column(name, out_kind, "feature")]
            return after, f"adds {name} ({out_kind}) = {text}", parsed, problems
        case Filter(expression=text):
            parsed = None
            try:
                parsed = expressions.parse(text, _kinds(columns))
                if parsed.type != "bool":
                    problems.append(
                        _problem(
                            i,
                            "expression",
                            "a filter needs a condition (a comparison, isnull, and, or, not)",
                        )
                    )
            except expressions.ExpressionRefused as exc:
                problems.append(_problem(i, "expression", str(exc), "expression_refused"))
            return columns, f"keeps rows where {text}", parsed, problems
        case _:
            raise TypeError(f"not an operation: {op!r}")


def plan(schema: TableSchema, operations: Sequence[Any]) -> Plan:
    """Check ``operations`` against ``schema`` in order (rule 26). Raises
    :class:`TransformRefused` listing every problem, each naming its column."""
    columns = list(schema.columns)
    steps: list[Step] = []
    problems: list[FieldProblem] = []
    for i, op in enumerate(operations):
        after, summary, parsed, found = _check(i, op, columns)
        problems.extend(found)
        if not found:
            columns = after
        steps.append(Step(i, op.op, TableSchema(tuple(columns)), summary, parsed))
    if problems:
        first = problems[0]["msg"]
        raise TransformRefused(
            f"{len(problems)} operation problem(s); the first: {first}.",
            suggested_action="Fix the operations listed in 'errors', then run again.",
            errors=problems,
        )
    return Plan(schema, tuple(steps), tuple(operations))


# ---------------------------------------------------------------------------- the rows


def _canonical_text(value: float) -> str:
    """A number as a category: 7.0 → "7", 2.43 → "2.43" (rule 27, K4)."""
    return str(int(value)) if value.is_integer() else repr(value)


def _to_number(value: Any) -> tuple[float | None, bool]:
    """``(number, ok)``. Empty stays empty; text that is not a finite number fails."""
    if value is None:
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, int | float):
        return (float(value), True) if math.isfinite(float(value)) else (None, False)
    text = str(value).strip()
    if text == "":
        return None, True
    try:
        number = float(text)
    except ValueError:
        return None, False
    return (number, True) if math.isfinite(number) else (None, False)


def _to_category(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _canonical_text(value)
    if isinstance(value, int):
        return str(value)
    return str(value)


@dataclass
class StepCounts:
    """What one operation did, over the rows it saw."""

    cast_failed: int = 0
    filtered: int = 0
    filtered_null: int = 0
    derived_null: int = 0
    examples: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class Counts:
    rows: int = 0
    readmitted: int = 0
    missing_target: int = 0
    steps: list[StepCounts] = field(default_factory=lambda: list[StepCounts]())


class RowStep:
    """Applies a :class:`Plan` to manifest rows, one at a time (rule 27)."""

    def __init__(self, checked: Plan, *, stage: str = STAGE) -> None:
        self.plan = checked
        self.stage = stage
        self.counts = Counts(steps=[StepCounts() for _ in checked.steps])

    def __call__(self, row: Row) -> Row:
        out: Row = dict(row)
        values = row_values(row, self.plan.input)
        status = row.get("status")
        readmit = (
            status == STATUS_REJECTED
            and row.get("reason_code") == REASON_MISSING_TARGET
            and row.get("decided_at_stage") in (None, "ingest")
        )
        verdict: tuple[str, str, str] | None = None
        """(status, reason code, detail) decided by an operation."""
        live = status == STATUS_ACCEPTED or readmit
        for step, op in zip(self.plan.steps, self.plan.operations, strict=True):
            counts = self.counts.steps[step.index]
            match op:
                case Drop(columns=dropped):
                    for name in dropped:
                        values.pop(name, None)
                case Rename(from_=old, to=new):
                    values[new] = values.pop(old, None)
                case SetKind(column=name, kind="numeric"):
                    number, ok = _to_number(values.get(name))
                    if ok:
                        values[name] = number
                    elif live and verdict is None:
                        counts.cast_failed += 1
                        if len(counts.examples) < 3:
                            counts.examples.append(str(row.get("row_key")))
                        verdict = (
                            STATUS_REJECTED,
                            REASON_CAST_FAILED,
                            f"'{name}' is {values.get(name)!r}, not a number "
                            f"(operation {step.index + 1})",
                        )
                case SetKind(column=name, kind="categorical"):
                    values[name] = _to_category(values.get(name))
                case Derive(name=name, kind=_):
                    kind = next(c.kind for c in step.schema.columns if c.name == name)
                    assert step.expression is not None
                    result = expressions.evaluate(step.expression, values)
                    if result is None:
                        counts.derived_null += 1 if live else 0
                        values[name] = None
                    elif kind == "numeric":
                        values[name] = float(result) if not isinstance(result, str) else None
                    else:
                        values[name] = _to_category(result)
                case Filter():
                    assert step.expression is not None
                    keep = expressions.evaluate(step.expression, values)
                    if keep is not True and live and verdict is None:
                        counts.filtered += 1
                        why = "the condition is false"
                        if keep is None:
                            counts.filtered_null += 1
                            why = "the condition cannot be computed for this row"
                        verdict = (
                            STATUS_FILTERED,
                            REASON_FILTER,
                            f"{why}: {step.expression.text} (operation {step.index + 1})",
                        )
                case _:
                    pass  # reorder, set_target, set_encoding change the schema only
        schema = self.plan.output
        self.counts.rows += 1
        features = {
            c.name: _finite_or_none(values.get(c.name))
            for c in schema.columns
            if c.role == "feature"
        }
        target = next(c for c in schema.columns if c.role == "target")
        goal = values.get(target.name)
        record = canonical_json(features)
        out.update(
            record=record,
            sha256=content_hash(record),
            size_bytes=len(record.encode("utf-8")),
            label=None,
            target_value=None,
        )
        if target.kind == "categorical":
            category = _to_category(goal)
            out["label"] = category
            goal = category
        else:
            number, ok = _to_number(goal)
            out["target_value"] = number if ok else None
        if verdict is not None:
            out.update(
                status=verdict[0],
                decided_at_stage=self.stage,
                reason_code=verdict[1],
                reason_detail=verdict[2][:500],
            )
            return out
        if not live:
            return out
        check = {
            c.name: (goal if c.role == "target" else values.get(c.name)) for c in schema.columns
        }
        if target.kind == "numeric":
            check[target.name] = out["target_value"]
        problem = table_problem(check, schema)
        if problem is None:
            if readmit:
                self.counts.readmitted += 1
                out.update(
                    status=STATUS_ACCEPTED,
                    decided_at_stage=None,
                    reason_code=None,
                    reason_detail=None,
                )
            return out
        code, detail = problem
        if code == REASON_MISSING_TARGET:
            self.counts.missing_target += 1
        if readmit and code == REASON_MISSING_TARGET:
            out.update(
                decided_at_stage=self.stage,
                reason_detail=f"{detail} (the target chosen by the transform)"[:500],
            )
            return out
        out.update(
            status=STATUS_REJECTED,
            decided_at_stage=self.stage,
            reason_code=code,
            reason_detail=detail[:500],
        )
        return out


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def step_summaries(checked: Plan, counts: Counts) -> list[dict[str, Any]]:
    """Per operation: the columns after it and what it did on the rows seen (rule 28; the
    preview's ``steps``, SCREENS.md open point 6c)."""
    out: list[dict[str, Any]] = []
    for step, c in zip(checked.steps, counts.steps, strict=True):
        done = step.summary
        if step.op == "set_kind" and step.schema.columns:
            done += f" · {c.cast_failed} cast_failed"
        if step.op == "filter":
            done += f" · {c.filtered} filtered ({c.filtered_null} where it cannot be computed)"
        if step.op == "derive" and c.derived_null:
            done += f" · {c.derived_null} missing"
        out.append(
            {
                "index": step.index,
                "op": step.op,
                "summary": done,
                "columns": [
                    {
                        "name": col.name,
                        "kind": col.kind,
                        "role": col.role,
                        "encoding": col.encoding,
                        "order": list(col.order) if col.order is not None else None,
                    }
                    for col in step.schema.columns
                ],
                "cast_failed": c.cast_failed,
                "filtered": c.filtered,
                "filtered_null": c.filtered_null,
                "examples": list(c.examples),
            }
        )
    return out
