"""HPO studies (M10, HPO-1.3 to HPO-1.5, ADR-0018 rule 9; docs/API.md).

- ``GET /studies``: newest first.
- ``GET /studies/{id}``: the study, its limits and state, trial counts, the best
  trial, parameter importance and the single test evaluation.
- ``GET /studies/{id}/trials``: every trial, oldest first (SCR-10's table and chart).
- ``GET /studies/{id}/best-config``: the base run with the best values set, for
  SCR-09 to prefill.

Start one with ``POST /jobs`` and ``kind: hpo_study``; pause, resume and cancel are
its job's. Plain Python: the API reads our tables, never Optuna's.
"""

from typing import Any

from fastapi import APIRouter, Query

from stratal.api import schemas
from stratal.api.jobs import Db
from stratal.hpo import store

router = APIRouter(tags=["studies"])


@router.get("/studies", response_model=schemas.StudyList)
def list_studies(session: Db, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Studies, newest first."""
    return {"items": store.list_studies(session, limit=limit)}


@router.get("/studies/{study_id}", response_model=schemas.Study)
def get_study(study_id: str, session: Db) -> dict[str, Any]:
    """A study (HPO-1.5)."""
    return store.study_view(session, store.get_study(session, study_id))


@router.get("/studies/{study_id}/trials", response_model=schemas.StudyTrialList)
def get_trials(study_id: str, session: Db) -> dict[str, Any]:
    """Its trials with their states, values, steps and why they were pruned (HPO-1.3)."""
    store.get_study(session, study_id)
    return {"items": store.list_trials(session, study_id)}


@router.get("/studies/{study_id}/best-config")
def get_best_config(study_id: str, session: Db) -> dict[str, Any]:
    """The base run with the best trial's values (HPO-1.5); 404 before a trial completes."""
    return store.best_config(store.get_study(session, study_id))
