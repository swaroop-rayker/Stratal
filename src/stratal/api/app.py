"""Application factory for the Stratal API."""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from stratal import __version__
from stratal.api.datasets import router as datasets_router
from stratal.api.health import router as health_router
from stratal.api.jobs import router as jobs_router
from stratal.api.models import router as models_router
from stratal.api.problem import install_error_handlers
from stratal.api.reports import router as reports_router
from stratal.api.reviews import router as reviews_router
from stratal.api.streams import router as streams_router
from stratal.api.system import router as system_router

API_PREFIX = "/api/v1"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Give every request an ID, bind it to the logs and return it in a header."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stratal",
        version=__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(streams_router, prefix=API_PREFIX)  # before jobs: /jobs/{id}/stream
    app.include_router(jobs_router, prefix=API_PREFIX)
    app.include_router(datasets_router, prefix=API_PREFIX)
    app.include_router(system_router, prefix=API_PREFIX)
    app.include_router(reviews_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(models_router, prefix=API_PREFIX)
    return app
