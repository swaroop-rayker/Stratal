"""Dataset types and their schema validation (DATA-5).

M1 defines the image–caption pairs type (DATA-5.1). Other types, and the
compatible model families each declares (DATA-5.5), arrive in phase 2.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

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


DATASET_TYPES: dict[str, Validator] = {"image_caption": validate_image_caption}
