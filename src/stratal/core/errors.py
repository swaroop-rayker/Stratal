"""Typed errors with stable codes (ADR-0011).

Raise these from anywhere. The API turns them into RFC 9457 problem details;
failed jobs store the same ``code`` with a reason and a suggested action.
Codes are part of the public contract: add new ones, never rename.
"""

from typing import ClassVar, TypedDict


class FieldProblem(TypedDict):
    """One field-level problem inside a validation failure."""

    loc: list[str | int]
    msg: str
    code: str


class StratalError(Exception):
    """Base class for every expected failure."""

    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal error"
    http_status: ClassVar[int] = 500

    def __init__(
        self,
        detail: str,
        *,
        suggested_action: str | None = None,
        errors: list[FieldProblem] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.suggested_action = suggested_action
        self.errors: list[FieldProblem] = errors if errors is not None else []


class ValidationFailed(StratalError):
    code = "validation_error"
    title = "Validation failed"
    http_status = 422


class NotFound(StratalError):
    code = "not_found"
    title = "Not found"
    http_status = 404


class Conflict(StratalError):
    code = "conflict"
    title = "Conflict"
    http_status = 409


class InvalidStateTransition(StratalError):
    code = "invalid_state_transition"
    title = "Invalid state transition"
    http_status = 409


class SourceNotFound(StratalError):
    code = "source_not_found"
    title = "Source not found"
    http_status = 422


class DependencyUnavailable(StratalError):
    code = "dependency_unavailable"
    title = "Dependency unavailable"
    http_status = 503
