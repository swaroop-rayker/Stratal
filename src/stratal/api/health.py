"""``GET /api/v1/health``: liveness plus dependency checks (docs/API.md §3.1)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from stratal import __version__
from stratal.core.errors import DependencyUnavailable
from stratal.core.health import Checker, default_checkers
from stratal.core.logging import get_logger

router = APIRouter(tags=["system"])
log = get_logger(__name__)


class Health(BaseModel):
    status: Literal["ok"]
    checks: dict[str, Literal["ok"]]
    version: str


def get_checkers() -> dict[str, Checker]:
    """Dependency so tests can substitute their own checks."""
    return default_checkers()


@router.get("/health")
def health(checkers: Annotated[dict[str, Checker], Depends(get_checkers)]) -> Health:
    failed: list[str] = []
    for name, check in checkers.items():
        try:
            check()
        except Exception as exc:
            log.warning("health_check_failed", check=name, reason=str(exc))
            failed.append(name)
    if failed:
        raise DependencyUnavailable(
            f"Health check failed: {', '.join(failed)}.",
            suggested_action="Run 'docker compose ps' and check those services are healthy.",
        )
    return Health(status="ok", checks={name: "ok" for name in checkers}, version=__version__)
