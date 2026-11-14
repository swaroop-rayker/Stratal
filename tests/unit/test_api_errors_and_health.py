"""Health endpoint (docs/API.md §3.1) and the problem-details error shape (ADR-0011)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stratal.api.app import create_app
from stratal.api.health import get_checkers
from stratal.core.errors import InvalidStateTransition
from stratal.core.health import Checker

PROBLEM_KEYS = {
    "type",
    "title",
    "status",
    "code",
    "detail",
    "suggested_action",
    "request_id",
    "errors",
}


def _ok() -> None:
    return None


def _down() -> None:
    raise ConnectionError("connection refused")


def _boom() -> None:
    raise RuntimeError("secret internal detail")


def _typed_failure() -> None:
    raise InvalidStateTransition(
        "Job job_0142 is completed and cannot be cancelled.",
        suggested_action="Start a new run instead.",
    )


def _needs_int(count: int) -> dict[str, int]:
    return {"count": count}


def _app(checkers: dict[str, Checker]) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_checkers] = lambda: checkers
    app.add_api_route("/api/v1/_test/boom", _boom)
    app.add_api_route("/api/v1/_test/typed", _typed_failure)
    app.add_api_route("/api/v1/_test/needs-int", _needs_int)
    return app


def _client(checkers: dict[str, Checker]) -> TestClient:
    return TestClient(_app(checkers), raise_server_exceptions=False)


def test_API_health_reports_ok_when_every_check_passes() -> None:
    response = _client({"postgres": _ok, "redis": _ok, "data_volume": _ok}).get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": "ok", "redis": "ok", "data_volume": "ok"}
    assert response.headers["X-Request-ID"].startswith("req_")


def test_ADR_0011_failed_dependency_is_problem_details() -> None:
    response = _client({"postgres": _ok, "redis": _down}).get("/api/v1/health")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body.keys() == PROBLEM_KEYS
    assert body["code"] == "dependency_unavailable"
    assert body["status"] == 503
    assert "redis" in body["detail"]
    assert body["suggested_action"]
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_ADR_0011_typed_error_carries_code_and_suggested_action() -> None:
    response = _client({}).get("/api/v1/_test/typed")

    assert response.status_code == 409
    body = response.json()
    assert body.keys() == PROBLEM_KEYS
    assert body["code"] == "invalid_state_transition"
    assert body["type"].endswith("/invalid_state_transition")
    assert body["suggested_action"] == "Start a new run instead."


def test_ADR_0011_unknown_route_is_problem_details() -> None:
    response = _client({}).get("/api/v1/no-such-thing")

    assert response.status_code == 404
    assert response.json().keys() == PROBLEM_KEYS
    assert response.json()["code"] == "not_found"


def test_ADR_0011_validation_failure_lists_field_problems() -> None:
    response = _client({}).get("/api/v1/_test/needs-int", params={"count": "many"})

    assert response.status_code == 422
    body = response.json()
    assert body.keys() == PROBLEM_KEYS
    assert body["code"] == "validation_error"
    assert body["errors"][0]["loc"] == ["query", "count"]
    assert body["errors"][0]["msg"]


def test_ADR_0011_unexpected_error_does_not_leak_details() -> None:
    response = _client({}).get("/api/v1/_test/boom")

    assert response.status_code == 500
    body = response.json()
    assert body.keys() == PROBLEM_KEYS
    assert body["code"] == "internal_error"
    assert "secret" not in response.text
    assert body["request_id"].startswith("req_")
