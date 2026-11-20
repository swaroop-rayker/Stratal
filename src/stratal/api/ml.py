"""Classical models' API (M10, ADR-0019 rule 16; docs/API.md).

- ``GET /ml/estimators/schema``: every estimator's title, the tasks it serves and its
  parameters' JSON Schema, from which SCR-09 generates its classical form (UI-2.3).

Plain Python: the API image has no scikit-learn (ADR-0019 rule 16).
"""

from typing import Any

from fastapi import APIRouter

from stratal.ml import params

router = APIRouter(tags=["ml"])


@router.get("/ml/estimators/schema")
def estimators_schema() -> dict[str, dict[str, Any]]:
    """Each estimator's title, tasks and parameter schema (ML-1.1, ML-1.2, UI-2.3)."""
    return params.schema()
