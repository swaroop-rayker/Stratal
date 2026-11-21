"""Versions side by side on one test split (EVAL-1.3; SCR-11 v2, approved 2026-11-20).

Only versions scored on the same split version are compared (the owner's K8): their
test metrics come from EVAL-1.2's scoring at the end of a run, kept with the version
even when retention deletes its weights (REG-2.7). Accuracy carries its Wilson 95 %
interval, with EVAL-1.1's function, so a small test split shows how little it can
tell apart.

A version trained before M10 has no test metrics. ``candidates`` says which can be
scored once (``POST /model-versions/{id}/score-test``, K8's exception) and why the
others cannot.
"""

import itertools
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import FieldProblem, NotFound, ValidationFailed
from stratal.data import registry as data_registry
from stratal.db.models import Dataset, Job, ModelLineage, ModelVersion
from stratal.eval.report_stats import wilson_interval
from stratal.registry import store

ACTIVE = ("queued", "running", "paused", "stalled")
HIGHER_IS_BETTER = {
    "accuracy": True,
    "macro_f1": True,
    "r2": True,
    "mse": False,
    "rmse": False,
    "mae": False,
}
ORDER = ("accuracy", "macro_f1", "mse", "rmse", "mae", "r2")
MIN_VERSIONS, MAX_VERSIONS = 2, 6


def test_metrics(version: ModelVersion) -> dict[str, float]:
    """The version's test metrics without the prefix, e.g. ``{"accuracy": 0.944}``."""
    metrics: dict[str, Any] = dict(version.metrics or {})
    return {
        k.removeprefix("test_"): float(v)
        for k, v in metrics.items()
        if k.startswith("test_") and isinstance(v, int | float) and not isinstance(v, bool)
    }


def _detail(version: ModelVersion) -> dict[str, Any]:
    metrics: dict[str, Any] = dict(version.metrics or {})
    detail: dict[str, Any] = dict(metrics.get("detail") or {})
    return dict(detail.get("test") or {})


def is_classical(version: ModelVersion) -> bool:
    model: dict[str, Any] = dict((version.config or {}).get("model") or {})
    return model.get("kind") == "sklearn"


def split_facts(session: Session, engine: Engine, data_dir: Path, split: str) -> dict[str, Any]:
    from stratal.build.io import version_io

    try:
        version = data_registry.get_version(session, split)
    except NotFound as exc:
        raise ValidationFailed(
            f"split {split!r} is not a known dataset version.",
            suggested_action="Pick a split version on SCR-02 or SCR-03.",
            errors=[FieldProblem(loc=["split"], msg="unknown version", code="not_found")],
        ) from exc
    dataset = session.get(Dataset, version.dataset_id)
    counts: dict[str, Any] = dict(version.counts or {})
    split_counts: dict[str, Any] = dict(counts.get("split") or {})
    splits: dict[str, Any] = dict(split_counts.get("splits") or {})
    test_part: dict[str, Any] = dict(splits.get("test") or {})
    test_rows: Any = test_part.get("rows")
    io = version_io(engine, data_dir, split)
    task = None
    if io.output_size is not None:
        task = "regression" if not io.classes and io.output_size == 1 else "classification"
    return {
        "version_id": split,
        "dataset": dataset.slug if dataset is not None else "?",
        "dataset_type": version.dtype,
        "test_rows": int(test_rows) if test_rows is not None else None,
        "task": task,
        "classes": [str(c) for c in io.classes],
    }


def _scoring(session: Session) -> dict[str, str]:
    """Versions with a Score on test job queued or running: version → job."""
    found: dict[str, str] = {}
    for job_id, spec in session.execute(
        select(Job.id, Job.spec).where(Job.kind == "score_test", Job.state.in_(ACTIVE))
    ):
        version_id = (spec or {}).get("model_version_id")
        if version_id:
            found[str(version_id)] = str(job_id)
    return found


def candidates(session: Session, engine: Engine, data_dir: Path, split: str) -> dict[str, Any]:
    """Every trained version on ``split``, newest first, with its main test metric or why
    it has none (the picker, SCR-11 v2 frame B)."""
    facts = split_facts(session, engine, data_dir, split)
    scoring = _scoring(session)
    rows = session.execute(
        select(ModelVersion, ModelLineage.name)
        .join(ModelLineage, ModelLineage.id == ModelVersion.lineage_id)
        .where(ModelVersion.dataset_version_id == split, ModelVersion.state == "trained")
        .order_by(ModelVersion.trained_at.desc().nulls_last(), ModelVersion.id)
    ).all()
    items: list[dict[str, Any]] = []
    for version, lineage_name in rows:
        tested = test_metrics(version)
        name = "accuracy" if "accuracy" in tested else "r2" if "r2" in tested else None
        present = version.weights_status == "present"
        why = None
        if not tested:
            why = (
                "weights deleted: cannot be scored"
                if not present
                else "a classical model is always scored at the end of its run"
                if is_classical(version)
                else None
            )
        items.append(
            {
                "id": version.id,
                "lineage_id": version.lineage_id,
                "lineage_name": lineage_name,
                "test_metric": name,
                "test_value": tested.get(name) if name else None,
                "weights_status": version.weights_status,
                "trained_at": store.iso(version.trained_at),
                "study_id": version.study_id,
                "scoring_job_id": scoring.get(version.id),
                "can_score": not tested and present and not is_classical(version),
                "why_not": why,
            }
        )
    return {"split": facts, "items": items}


def compare(
    session: Session, engine: Engine, data_dir: Path, split: str, ids: list[str]
) -> dict[str, Any]:
    """Two to six versions scored on ``split``, as columns (EVAL-1.3). A version that is
    unknown, tested elsewhere or not tested is a ``422`` at ``["v", i]``."""
    facts = split_facts(session, engine, data_dir, split)
    if len(set(ids)) != len(ids) or not MIN_VERSIONS <= len(ids) <= MAX_VERSIONS:
        raise ValidationFailed(
            f"Compare {MIN_VERSIONS} to {MAX_VERSIONS} different versions; got {len(ids)}.",
            suggested_action="Pick the versions again in Add or remove versions.",
            errors=[FieldProblem(loc=["v"], msg="2 to 6 different versions", code="invalid")],
        )
    problems: list[FieldProblem] = []
    chosen: list[ModelVersion] = []
    for i, version_id in enumerate(ids):
        version = session.get(ModelVersion, version_id)
        if version is None:
            problems.append(
                FieldProblem(loc=["v", i], msg=f"no version {version_id}", code="not_found")
            )
        elif version.dataset_version_id != split:
            problems.append(
                FieldProblem(
                    loc=["v", i],
                    msg=f"{version_id} was tested on {version.dataset_version_id}, not {split}",
                    code="invalid",
                )
            )
        elif not test_metrics(version):
            problems.append(
                FieldProblem(
                    loc=["v", i],
                    msg=f"{version_id} has no test metrics: score it on test first",
                    code="invalid",
                )
            )
        else:
            chosen.append(version)
    if problems:
        raise ValidationFailed(
            "These versions cannot be compared on one split: "
            + "; ".join(p["msg"] for p in problems)
            + ".",
            suggested_action="Remove them, or pick versions tested on this split.",
            errors=problems,
        )

    columns: list[dict[str, Any]] = []
    for version in chosen:
        lineage = session.get(ModelLineage, version.lineage_id)
        parent = (
            session.get(ModelVersion, version.parent_version_id)
            if version.parent_version_id
            else None
        )
        tested = test_metrics(version)
        detail = _detail(version)
        rows = detail.get("rows")
        correct = low = high = None
        if "accuracy" in tested and isinstance(rows, int) and rows > 0:
            correct = round(tested["accuracy"] * rows)
            interval = wilson_interval(correct, rows)
            low, high = round(interval.low, 6), round(interval.high, 6)
        model: dict[str, Any] = dict((version.config or {}).get("model") or {})
        graph: dict[str, Any] = dict(model.get("graph") or {})
        name = str(model.get("estimator") or graph.get("label") or model.get("name") or "model")
        columns.append(
            {
                "id": version.id,
                "lineage_id": version.lineage_id,
                "lineage_name": lineage.name if lineage is not None else "?",
                "kind": "classical" if is_classical(version) else "network",
                "model": name,
                "parameters": lineage.parameters
                if lineage is not None and lineage.parameters
                else None,
                "trained_at": store.iso(version.trained_at),
                "job_id": version.job_id,
                "weights_status": version.weights_status,
                "parent_version_id": version.parent_version_id,
                "parent_is_base": parent is not None and parent.state == "base",
                "study_id": version.study_id,
                "metrics": {k: tested[k] for k in ORDER if k in tested},
                "correct": correct,
                "accuracy_low": low,
                "accuracy_high": high,
                "per_class_f1": {
                    str(k): float(v) for k, v in dict(detail.get("per_class_f1") or {}).items()
                },
            }
        )

    best: dict[str, list[str]] = {}
    for metric in ORDER:
        values = {c["id"]: c["metrics"][metric] for c in columns if metric in c["metrics"]}
        if len(values) < 2 or len(set(values.values())) == 1:
            continue
        top = max(values.values()) if HIGHER_IS_BETTER[metric] else min(values.values())
        best[metric] = [i for i, v in values.items() if v == top]
    intervals = [
        (c["accuracy_low"], c["accuracy_high"]) for c in columns if c["accuracy_low"] is not None
    ]
    overlap = any(a[0] <= b[1] and b[0] <= a[1] for a, b in itertools.combinations(intervals, 2))
    return {"split": facts, "versions": columns, "best": best, "overlap": overlap}
