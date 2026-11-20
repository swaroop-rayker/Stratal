"""Studies and trials in PostgreSQL: what SCR-10 reads (HPO-1.3, HPO-1.5, ADR-0018 rule 3).

Plain Python. Optuna's tables (schema ``optuna``) are the sampler's memory; the
workers copy each trial here as it starts and ends, and the API reads only these.
"""

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import NotFound
from stratal.db.models import Study, StudyTrial
from stratal.mon.events import utc_now


def new_study_id() -> str:
    return f"st_{secrets.token_hex(6)}"


def create_study(session: Session, *, job_id: str, spec: dict[str, Any]) -> str:
    study = Study(
        id=new_study_id(),
        job_id=job_id,
        kind=spec["study_kind"],
        dataset_version_id=spec["dataset_version"],
        base=spec["base"],
        space=spec["space"],
        objective=spec["objective"],
        direction=spec["direction"],
        limits={"n_trials": spec["n_trials"], "timeout_s": spec["timeout_s"]},
        pruning=spec["pruning"],
        cv_folds=spec["cv_folds"] if spec["study_kind"] == "classical" else None,
        seed=spec["seed"],
        state="running",
    )
    session.add(study)
    session.flush()
    return study.id


def get_study(session: Session, study_id: str) -> Study:
    study = session.get(Study, study_id)
    if study is None:
        raise NotFound(f"No study {study_id!r}.")
    return study


# ------------------------------------------------------------------ trials, from the workers


def trial_started(engine: Engine, study_id: str, number: int, params: dict[str, Any]) -> None:
    with Session(engine) as session, session.begin():
        row = session.get(StudyTrial, (study_id, number))
        if row is None:
            session.add(
                StudyTrial(study_id=study_id, number=number, state="running", params=params)
            )
        else:  # a trial number Optuna gives again after a crash
            row.state, row.params, row.started_at = "running", params, utc_now()


def trial_step(engine: Engine, study_id: str, number: int, step: int, value: float) -> None:
    with Session(engine) as session, session.begin():
        row = session.get(StudyTrial, (study_id, number))
        if row is not None:
            row.step = step
            row.intermediate = [*row.intermediate, [step, value]]


def trial_finished(
    engine: Engine,
    study_id: str,
    number: int,
    *,
    state: str,
    value: float | None = None,
    prune_reason: str | None = None,
    code: str | None = None,
) -> None:
    with Session(engine) as session, session.begin():
        row = session.get(StudyTrial, (study_id, number))
        if row is None:
            return
        now = utc_now()
        row.state, row.value, row.prune_reason, row.code = state, value, prune_reason, code
        row.finished_at = now
        row.duration_s = round((now - row.started_at).total_seconds(), 3)


def counts(session: Session, study_id: str) -> dict[str, int]:
    """Trials by state; ``started`` counts what the trial limit counts (interrupted excluded)."""
    rows = session.execute(
        select(StudyTrial.state, StudyTrial.code, func.count())
        .where(StudyTrial.study_id == study_id)
        .group_by(StudyTrial.state, StudyTrial.code)
    ).all()
    found = {"running": 0, "complete": 0, "pruned": 0, "failed": 0, "interrupted": 0}
    for state, code, n in rows:
        found["interrupted" if code == "interrupted" else state] += int(n)
    found["started"] = sum(v for k, v in found.items() if k != "interrupted")
    return found


def update_study(engine: Engine, study_id: str, **values: Any) -> None:
    with Session(engine) as session, session.begin():
        session.execute(update(Study).where(Study.id == study_id).values(**values))


def record_test_once(engine: Engine, study_id: str, metrics: dict[str, Any]) -> bool:
    """Store the study's test metrics if none are stored yet (HPO-1.4): one conditional
    update, so a second evaluation can never overwrite the first. Returns whether it wrote."""
    with Session(engine) as session, session.begin():
        result = session.execute(
            update(Study)
            .where(Study.id == study_id, Study.test_evaluated_at.is_(None))
            .values(test_metrics=metrics, test_evaluated_at=utc_now())
        )
        changed: Any = getattr(result, "rowcount", 0)
        return bool(changed)


# ------------------------------------------------------------------ views, for the API


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def trial_view(row: StudyTrial) -> dict[str, Any]:
    return {
        "number": row.number,
        "state": row.state,
        "params": row.params,
        "value": row.value,
        "step": row.step,
        "prune_reason": row.prune_reason,
        "code": row.code,
        "intermediate": row.intermediate,
        "duration_s": row.duration_s,
        "job_id": row.job_id,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def study_view(session: Session, study: Study) -> dict[str, Any]:
    return {
        "id": study.id,
        "job_id": study.job_id,
        "kind": study.kind,
        "dataset_version": study.dataset_version_id,
        "objective": study.objective,
        "direction": study.direction,
        "limits": study.limits,
        "pruning": study.pruning,
        "cv_folds": study.cv_folds,
        "seed": study.seed,
        "space": study.space,
        "base": study.base,
        "state": study.state,
        "ended_by": study.ended_by,
        "elapsed_s": study.elapsed_s,
        "trials": counts(session, study.id),
        "best": None
        if study.best_trial is None
        else {"trial": study.best_trial, "value": study.best_value, "params": study.best_params},
        "importance": study.importance,
        "best_version_id": study.best_version_id,
        "test_metrics": study.test_metrics,
        "test_evaluated_at": _iso(study.test_evaluated_at),
        "created_at": _iso(study.created_at),
        "finished_at": _iso(study.finished_at),
    }


def list_trials(session: Session, study_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(StudyTrial).where(StudyTrial.study_id == study_id).order_by(StudyTrial.number)
    ).all()
    return [trial_view(r) for r in rows]


def list_studies(session: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.scalars(select(Study).order_by(Study.created_at.desc()).limit(limit)).all()
    return [study_view(session, s) for s in rows]


def best_config(study: Study) -> dict[str, Any]:
    """The base run with the best trial's values set: what SCR-09 prefills (HPO-1.5)."""
    from stratal.hpo.space import apply

    if study.best_params is None:
        raise NotFound(f"{study.id} has no complete trial yet.")
    return apply(study.base, study.best_params)
