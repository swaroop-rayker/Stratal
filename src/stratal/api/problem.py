"""RFC 9457 problem details for every error response (ADR-0011, docs/API.md §2)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from stratal.core.errors import FieldProblem, StratalError
from stratal.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"
_TYPE_BASE = "https://stratal.local/errors/"

log = get_logger(__name__)


def request_id_of(request: Request) -> str:
    request_id: object = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else "req_unknown"


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    suggested_action: str | None = None,
    errors: list[FieldProblem] | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_JSON,
        content={
            **(extensions or {}),
            "type": f"{_TYPE_BASE}{code}",
            "title": title,
            "status": status,
            "code": code,
            "detail": detail,
            "suggested_action": suggested_action,
            "request_id": request_id_of(request),
            "errors": errors if errors is not None else [],
        },
    )


async def _handle_stratal_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StratalError)
    return problem_response(
        request,
        status=exc.http_status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        suggested_action=exc.suggested_action,
        errors=exc.errors,
        extensions=exc.extensions,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    problems: list[FieldProblem] = [
        FieldProblem(
            loc=[part for part in error.get("loc", ()) if isinstance(part, str | int)],
            msg=str(error.get("msg", "")),
            code=str(error.get("type", "invalid")),
        )
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        code="validation_error",
        title="Validation failed",
        detail="The request did not pass validation.",
        suggested_action="Fix the fields listed in 'errors' and send the request again.",
        errors=problems,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = "not_found" if exc.status_code == 404 else "http_error"
    title = "Not found" if exc.status_code == 404 else "HTTP error"
    return problem_response(
        request, status=exc.status_code, code=code, title=title, detail=str(exc.detail)
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_exception", path=request.url.path, exc_info=exc)
    return problem_response(
        request,
        status=500,
        code="internal_error",
        title="Internal error",
        detail="An unexpected error occurred. Quote the request ID when reporting it.",
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StratalError, _handle_stratal_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)
