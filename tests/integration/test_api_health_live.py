"""Health endpoint against the real postgres, redis and data volume.

Run inside Compose: ``docker compose run --rm dev pytest -m integration``.
Nothing here is mocked (SPEC §8.1).
"""

import pytest
from fastapi.testclient import TestClient

from stratal.api.app import create_app

pytestmark = pytest.mark.integration


def test_API_health_against_real_services() -> None:
    response = TestClient(create_app()).get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok", "data_volume": "ok"}
