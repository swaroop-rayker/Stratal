"""Dataset types and their schema validation (DATA-5).

M1 defines the image–caption pairs type (DATA-5.1); M8 adds labelled images
(DATA-5.2, delivered early for training). M9 adds the tabular and labelled-text
row schemas, and :data:`TYPE_INFO`: what each type feeds a model, the model
families it accepts and its default metrics (DATA-5.5, ADR-0017 rule 5).
Tabular and text ingestion arrive in M10 (DATA-1.4), so only labelled images
can be trained in M9.
"""

import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row


@dataclass(frozen=True)
class RowProblem:
    row_key: str
    reason_code: str
    detail: str


@dataclass
class ValidationReport:
    """The report DATA-5.1.AC1 asks for: what was checked and what failed."""

    dtype: str
    checked: int = 0
    valid: int = 0
    problems: list[RowProblem] = field(default_factory=lambda: list[RowProblem]())

    @property
    def is_valid(self) -> bool:
        return not self.problems

    def by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for problem in self.problems:
            counts[problem.reason_code] = counts.get(problem.reason_code, 0) + 1
        return counts


Validator = Callable[[Iterable[Mapping[str, Any]], Callable[[str], bool]], ValidationReport]


def validate_image_caption(
    rows: Iterable[Mapping[str, Any]], blob_exists: Callable[[str], bool]
) -> ValidationReport:
    """Check accepted rows against the image–caption schema.

    A row fails if its image file is missing from the blob store, or if it has
    no caption (absent, empty, or only whitespace). The image file is the
    original (``sha256``) and, once preprocess has run, also the normalised
    copy that training reads (``derived_sha256``). Rows that are already
    non-accepted are not part of the dataset and are not checked.
    """
    report = ValidationReport(dtype="image_caption")
    for row in rows:
        if row.get("status") != STATUS_ACCEPTED:
            continue
        report.checked += 1
        key = str(row["row_key"])
        sha256 = row.get("sha256")
        derived = row.get("derived_sha256")
        caption = row.get("caption")
        if not isinstance(sha256, str) or not blob_exists(sha256):
            report.problems.append(
                RowProblem(key, "missing_file", "image file is not in the store")
            )
        elif isinstance(derived, str) and not blob_exists(derived):
            report.problems.append(
                RowProblem(key, "missing_file", "preprocessed copy is not in the store")
            )
        elif not isinstance(caption, str) or not caption.strip():
            report.problems.append(RowProblem(key, "missing_caption", "row has no caption"))
        else:
            report.valid += 1
    return report


def validate_labelled_images(
    rows: Iterable[Mapping[str, Any]], blob_exists: Callable[[str], bool]
) -> ValidationReport:
    """Check accepted rows against the labelled-images schema (DATA-5.2).

    Like :func:`validate_image_caption`, with a class label in place of the
    caption: a row fails if its image or preprocessed copy is missing from the
    blob store, or if its ``label`` is absent or blank.
    """
    report = ValidationReport(dtype="labelled_images")
    for row in rows:
        if row.get("status") != STATUS_ACCEPTED:
            continue
        report.checked += 1
        key = str(row["row_key"])
        sha256 = row.get("sha256")
        derived = row.get("derived_sha256")
        label = row.get("label")
        if not isinstance(sha256, str) or not blob_exists(sha256):
            report.problems.append(
                RowProblem(key, "missing_file", "image file is not in the store")
            )
        elif isinstance(derived, str) and not blob_exists(derived):
            report.problems.append(
                RowProblem(key, "missing_file", "preprocessed copy is not in the store")
            )
        elif not isinstance(label, str) or not label.strip():
            report.problems.append(RowProblem(key, "missing_label", "row has no class label"))
        else:
            report.valid += 1
    return report


def apply_report(
    rows: Iterable[Mapping[str, Any]], report: ValidationReport, *, stage: str
) -> Iterator[Row]:
    """Yield ``rows`` with every row named in ``report`` marked rejected."""
    problems = {problem.row_key: problem for problem in report.problems}
    for row in rows:
        out: Row = dict(row)
        problem = problems.get(str(row["row_key"]))
        if problem is not None:
            out.update(
                status=STATUS_REJECTED,
                decided_at_stage=stage,
                reason_code=problem.reason_code,
                reason_detail=problem.detail,
            )
        yield out


DATASET_TYPES: dict[str, Validator] = {
    "image_caption": validate_image_caption,
    "labelled_images": validate_labelled_images,
}


# ---------------------------------------------------------------------------- M9: the other two


@dataclass(frozen=True)
class Column:
    """One column of a tabular dataset (DATA-5.2)."""

    name: str
    kind: Literal["numeric", "categorical"]
    role: Literal["feature", "target"] = "feature"


@dataclass(frozen=True)
class TableSchema:
    columns: tuple[Column, ...]

    def __post_init__(self) -> None:
        targets = [c for c in self.columns if c.role == "target"]
        if len(targets) != 1:
            raise ValueError(f"a table has exactly one target column, got {len(targets)}")
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("column names must be unique")


def validate_tabular(rows: Iterable[Mapping[str, Any]], schema: TableSchema) -> ValidationReport:
    """Check accepted rows against a table's schema (DATA-5.2).

    Every column must be present. A numeric value is a finite number; a categorical
    one a string. A feature may be empty (``None``): filling it in is preprocessing,
    fitted on the train split (ML-1.3). The target may not be empty.
    """
    report = ValidationReport(dtype="tabular")
    for row in rows:
        if row.get("status") != STATUS_ACCEPTED:
            continue
        report.checked += 1
        problem = _table_problem(row, schema)
        if problem is None:
            report.valid += 1
        else:
            report.problems.append(RowProblem(str(row["row_key"]), *problem))
    return report


def table_problem(row: Mapping[str, Any], schema: TableSchema) -> tuple[str, str] | None:
    """The first way ``row`` (values by column name) breaks ``schema``: ``(code, detail)``,
    or ``None``. Tabular ingest rejects the row with it (DATA-1.4)."""
    return _table_problem(row, schema)


def _table_problem(row: Mapping[str, Any], schema: TableSchema) -> tuple[str, str] | None:
    for column in schema.columns:
        if column.name not in row:
            return "missing_column", f"no column '{column.name}'"
        value = row[column.name]
        if value is None:
            if column.role == "target":
                return "missing_target", f"the target '{column.name}' is empty"
            continue
        if column.kind == "numeric":
            number = isinstance(value, int | float) and not isinstance(value, bool)
            if not number or not math.isfinite(value):
                return "not_a_number", f"'{column.name}' is {value!r}, not a finite number"
        elif not isinstance(value, str):
            return "not_a_category", f"'{column.name}' is {value!r}, not a category name"
    return None


MAX_TEXT_CHARS = 10_000
"""Longest labelled text kept; DATA-2.4's cleaning drops and logs longer rows (M10)."""


def validate_labelled_text(
    rows: Iterable[Mapping[str, Any]], *, max_chars: int = MAX_TEXT_CHARS
) -> ValidationReport:
    """Check accepted rows against the labelled-text schema (DATA-5.2): a ``text`` that
    is not blank and at most ``max_chars`` long, and a ``label``."""
    report = ValidationReport(dtype="labelled_text")
    for row in rows:
        if row.get("status") != STATUS_ACCEPTED:
            continue
        report.checked += 1
        key = str(row["row_key"])
        text, label = row.get("text"), row.get("label")
        if not isinstance(text, str) or not text.strip():
            report.problems.append(RowProblem(key, "empty_text", "row has no text"))
        elif len(text) > max_chars:
            report.problems.append(
                RowProblem(key, "text_too_long", f"{len(text):,} characters; at most {max_chars:,}")
            )
        elif not isinstance(label, str) or not label.strip():
            report.problems.append(RowProblem(key, "missing_label", "row has no class label"))
        else:
            report.valid += 1
    return report


Family = Literal["mlp", "cnn", "rnn", "transformer"]
InputKind = Literal["image", "vector", "tokens"]


@dataclass(frozen=True)
class TypeInfo:
    """What a dataset type gives a model (DATA-5.5, ADR-0017 rule 5)."""

    name: str
    title: str
    input_kind: InputKind | None
    """``None``: not a training input for the builder."""
    families: tuple[Family, ...]
    default_metrics: tuple[str, ...]
    trainable: bool
    """Whether the training runtime accepts it (tabular and text since M10)."""
    note: str


TYPE_INFO: dict[str, TypeInfo] = {
    "image_caption": TypeInfo(
        "image_caption",
        "Image–caption pairs",
        None,
        (),
        ("clip_score", "cider_d"),
        False,
        "Captioned by the label stage; evaluated, not trained by the builder.",
    ),
    "labelled_images": TypeInfo(
        "labelled_images",
        "Labelled images",
        "image",
        ("mlp", "cnn"),
        ("accuracy", "macro_f1"),
        True,
        "Transformers need a layer that cuts images into patches, which the builder does not have.",
    ),
    "tabular": TypeInfo(
        "tabular",
        "Tabular",
        "vector",
        ("mlp",),
        ("accuracy", "macro_f1"),
        True,
        "Classification or regression, by the target's kind; trains on a split version.",
    ),
    "labelled_text": TypeInfo(
        "labelled_text",
        "Labelled text",
        "tokens",
        ("mlp", "rnn", "transformer"),
        ("accuracy", "macro_f1"),
        True,
        "Trains on a tokenize version: its tokens and vocabulary fix the input.",
    ),
}
