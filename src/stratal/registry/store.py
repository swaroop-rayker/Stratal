"""Registry rows: lineages, versions and in-run checkpoints (REG-1.1, REG-1.2, ADR-0015).

A lineage is one model spec trained on one dataset's inputs and classes. Its ID
is derived from that key, so every run of the same model on the same data finds
the same lineage. It has one base version (the initial weights, never deleted by
retention, REG-2.1) and one version per training run: ``partial`` while the run
is paused, ``trained`` when it completes (REG-1.2).

Weights live under ``registry/<lineage>/<version>/`` in the data folder, as
``model.safetensors`` with ``model.json`` beside it (NFR-8). This module records
where they are; it never reads or writes them.
"""

import hashlib
import json
import secrets
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict, NotFound
from stratal.db.models import (
    Checkpoint,
    Dataset,
    Job,
    ModelLineage,
    ModelVersion,
    Pin,
)
from stratal.mon.events import utc_now

REGISTRY_DIR = "registry"
WEIGHTS_FILE = "model.safetensors"
META_FILE = "model.json"
MAIN_METRIC = "val_accuracy"
"""The metric lists show until EVAL-1.2's task metrics exist."""


def lineage_key(
    *,
    model_spec: dict[str, Any],
    dataset_slug: str,
    input_shape: Sequence[int],
    classes: Sequence[str],
) -> tuple[str, str]:
    """``(lineage_id, key_hash)`` for a model spec on a dataset's inputs and classes.

    Any difference in the four parts is a different lineage: weights for 10
    classes cannot start a run with 12, nor a 32-px model one on 64-px images.
    """
    key = {
        "model": model_spec,
        "dataset": dataset_slug,
        "input_shape": list(input_shape),
        "classes": list(classes),
    }
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"ml_{digest[:12]}", digest


def new_version_id() -> str:
    """``mv_`` and 12 random hex characters. Weights are not content-addressed (ADR-0015)."""
    return f"mv_{secrets.token_hex(6)}"


def version_folder(lineage_id: str, version_id: str) -> str:
    """Where a version's files go, relative to the data folder."""
    return f"{REGISTRY_DIR}/{lineage_id}/{version_id}"


_LOCK = "SELECT pg_advisory_{kind}(hashtextextended(:key, 0))"


def _lock_key(lineage_id: str) -> str:
    return f"stratal:lineage:{lineage_id}"


def lock_lineage(session: Session, lineage_id: str) -> None:
    """Hold the lineage's lock until the transaction ends. Writers of registry files take
    it, so retention never sweeps a folder whose row is not committed yet (NFR-15)."""
    session.execute(text(_LOCK.format(kind="xact_lock")), {"key": _lock_key(lineage_id)})


@contextmanager
def holding_lineage(engine: Engine, lineage_id: str) -> Generator[None]:
    """The same lock for a whole block that commits several times: retention marks,
    deletes and records across transactions (ADR-0015 rule 5)."""
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        connection.execute(text(_LOCK.format(kind="lock")), {"key": _lock_key(lineage_id)})
        try:
            yield
        finally:
            connection.execute(text(_LOCK.format(kind="unlock")), {"key": _lock_key(lineage_id)})
    finally:
        connection.close()


def ensure_lineage(
    session: Session,
    *,
    model_spec: dict[str, Any],
    dataset_id: int,
    dataset_slug: str,
    input_shape: Sequence[int],
    classes: Sequence[str],
    parameters: int,
    task: str,
    name: str | None = None,
) -> ModelLineage:
    """The lineage for this key, created if new, and locked until the transaction ends,
    so two runs starting at once create one base between them."""
    lineage_id, key_hash = lineage_key(
        model_spec=model_spec,
        dataset_slug=dataset_slug,
        input_shape=input_shape,
        classes=classes,
    )
    lock_lineage(session, lineage_id)
    session.execute(
        insert(ModelLineage)
        .values(
            id=lineage_id,
            key_hash=key_hash,
            name=f"{name or model_spec.get('name', 'model')} · {dataset_slug}",
            task=task,
            model_spec=model_spec,
            dataset_id=dataset_id,
            input_shape=list(input_shape),
            classes=list(classes),
            parameters=parameters,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    lineage = session.get(ModelLineage, lineage_id, with_for_update=True, populate_existing=True)
    assert lineage is not None
    return lineage


def base_of(session: Session, lineage_id: str) -> ModelVersion | None:
    return session.scalar(
        select(ModelVersion).where(
            ModelVersion.lineage_id == lineage_id, ModelVersion.state == "base"
        )
    )


def run_version(session: Session, job_id: str) -> ModelVersion | None:
    """The version a training job made or is making, if any (not the base it created)."""
    return session.scalar(
        select(ModelVersion).where(ModelVersion.job_id == job_id, ModelVersion.state != "base")
    )


def add_base(
    session: Session,
    *,
    lineage: ModelLineage,
    version_id: str,
    job_id: str | None,
    dataset_version_id: str | None,
    config: dict[str, Any],
    config_hash: str,
    weights_bytes: int,
    note: str | None = None,
    created_at: datetime | None = None,
) -> ModelVersion:
    """Record a lineage's base: its weights are already on disk (REG-2.1). A base is not
    trained, so it has no ``trained_at``."""
    base = ModelVersion(
        id=version_id,
        lineage_id=lineage.id,
        state="base",
        weights_status="present",
        job_id=job_id,
        dataset_version_id=dataset_version_id,
        config=config,
        config_hash=config_hash,
        weights_path=version_folder(lineage.id, version_id),
        weights_bytes=weights_bytes,
        note=note,
        created_at=created_at or utc_now(),
    )
    session.add(base)
    session.flush()
    return base


def record_run(
    session: Session,
    *,
    lineage_id: str,
    job_id: str,
    state: str,
    parent_version_id: str | None,
    dataset_version_id: str | None,
    config: dict[str, Any],
    config_hash: str,
    history: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
    version_id: str | None = None,
    weights_bytes: int | None = None,
    note: str | None = None,
    trained_at: datetime | None = None,
) -> ModelVersion:
    """Create or update a run's version: ``partial`` at a pause, ``trained`` at the end.

    A partial version's weights are its run's checkpoint, so it has no folder of
    its own until the run completes. ``version_id`` is used only when the row is new.
    """
    version = run_version(session, job_id)
    if version is None:
        version = ModelVersion(
            id=version_id or new_version_id(),
            lineage_id=lineage_id,
            job_id=job_id,
            parent_version_id=parent_version_id,
            dataset_version_id=dataset_version_id,
            config=config,
            config_hash=config_hash,
            weights_status="present",
            created_at=utc_now(),
        )
        session.add(version)
    version.state = state
    version.history = history
    if metrics is not None:
        version.metrics = metrics
    if note is not None:
        version.note = note
    if state == "trained":
        version.weights_status = "present"
        version.weights_path = version_folder(lineage_id, version.id)
        version.weights_bytes = weights_bytes
        version.trained_at = trained_at or utc_now()
    session.flush()
    return version


def record_checkpoint(
    session: Session,
    *,
    job_id: str,
    lineage_id: str | None,
    path: str,
    size: int,
    global_step: int,
    epoch: int,
) -> None:
    """Note a run's latest checkpoint; there is one per job (REG-2.6)."""
    values = {
        "job_id": job_id,
        "lineage_id": lineage_id,
        "path": path,
        "bytes": size,
        "global_step": global_step,
        "epoch": epoch,
        "updated_at": utc_now(),
    }
    session.execute(
        insert(Checkpoint)
        .values(**values)
        .on_conflict_do_update(index_elements=["job_id"], set_=values)
    )


def drop_checkpoint(session: Session, job_id: str) -> None:
    session.execute(delete(Checkpoint).where(Checkpoint.job_id == job_id))


def model_version_for_job(session: Session, job_id: str) -> str | None:
    version = run_version(session, job_id)
    return version.id if version is not None else None


# -- views (docs/API.md §3.9) ------------------------------------------------------


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def latest_of(versions: Sequence[ModelVersion]) -> ModelVersion | None:
    """The newest trained version with its weights; the base when there is none (REG-2.2).
    Retention never deletes the latest, so this is the policy's latest too."""
    trained = [v for v in versions if v.state == "trained" and v.weights_status == "present"]
    if trained:
        return max(trained, key=lambda v: (v.trained_at or v.created_at, v.id))
    return next((v for v in versions if v.state == "base"), None)


def _on_disk(version: ModelVersion) -> int:
    if version.weights_status == "weights_deleted" or version.state == "partial":
        return 0
    return version.weights_bytes or 0


def _main_metric(version: ModelVersion) -> float | None:
    if version.metrics is None:
        return None
    value = version.metrics.get(MAIN_METRIC)
    return float(value) if isinstance(value, int | float) else None


def _versions_by_lineage(
    session: Session, lineage_ids: Sequence[str]
) -> dict[str, list[ModelVersion]]:
    grouped: dict[str, list[ModelVersion]] = {i: [] for i in lineage_ids}
    rows = session.scalars(
        select(ModelVersion)
        .where(ModelVersion.lineage_id.in_(lineage_ids))
        .order_by(ModelVersion.created_at.desc(), ModelVersion.id)
    )
    for version in rows:
        grouped[version.lineage_id].append(version)
    return grouped


def _checkpoint_bytes(session: Session, lineage_ids: Sequence[str]) -> dict[str, int]:
    rows = session.execute(
        select(Checkpoint.lineage_id, func.sum(Checkpoint.bytes))
        .where(Checkpoint.lineage_id.in_(lineage_ids))
        .group_by(Checkpoint.lineage_id)
    )
    return {str(lineage_id): int(total or 0) for lineage_id, total in rows}


def _version_row(
    version: ModelVersion,
    *,
    latest_id: str | None,
    pinned: set[str],
    job_states: dict[str, str],
) -> dict[str, Any]:
    return {
        "id": version.id,
        "lineage_id": version.lineage_id,
        "state": version.state,
        "is_latest": version.id == latest_id,
        "weights_status": version.weights_status,
        "weights_bytes": version.weights_bytes,
        "pinned": version.id in pinned,
        "parent_version_id": version.parent_version_id,
        "job_id": version.job_id,
        "job_state": job_states.get(version.job_id) if version.job_id else None,
        "dataset_version_id": version.dataset_version_id,
        "config_hash": version.config_hash,
        "main_metric": _main_metric(version),
        "note": version.note,
        "created_at": iso(version.created_at),
        "trained_at": iso(version.trained_at),
        "weights_deleted_by_job_id": version.weights_deleted_by_job_id,
    }


def _context(session: Session, versions: Sequence[ModelVersion]) -> tuple[set[str], dict[str, str]]:
    ids = [v.id for v in versions]
    pinned = set(session.scalars(select(Pin.version_id).where(Pin.version_id.in_(ids))))
    job_ids = [v.job_id for v in versions if v.job_id]
    job_states = dict(session.execute(select(Job.id, Job.state).where(Job.id.in_(job_ids))).all())
    return pinned, job_states


def _lineage_summary(
    lineage: ModelLineage,
    dataset_slug: str,
    versions: Sequence[ModelVersion],
    checkpoint_bytes: int,
) -> dict[str, Any]:
    latest = latest_of(versions)
    counts = {
        state: sum(v.state == state for v in versions) for state in ("base", "partial", "trained")
    }
    updated = max((v.trained_at or v.created_at for v in versions), default=lineage.created_at)
    return {
        "id": lineage.id,
        "name": lineage.name,
        "task": lineage.task,
        "model": lineage.model_spec,
        "dataset": dataset_slug,
        "input_shape": lineage.input_shape,
        "classes": lineage.classes,
        "parameters": lineage.parameters,
        "counts": counts,
        "latest_version_id": latest.id if latest else None,
        "latest_state": latest.state if latest else None,
        "main_metric": _main_metric(latest) if latest else None,
        "weights_bytes": sum(_on_disk(v) for v in versions),
        "checkpoint_bytes": checkpoint_bytes,
        "created_at": iso(lineage.created_at),
        "updated_at": iso(updated),
    }


def list_lineages(
    session: Session,
    *,
    state: str | None = None,
    task: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Lineages, most recently updated first. ``state`` keeps lineages with at least one
    version in that state (REG-1.2), so ``partial`` finds every lineage with a paused
    run; ``q`` matches the name."""
    query = select(ModelLineage, Dataset.slug).join(Dataset, Dataset.id == ModelLineage.dataset_id)
    if task is not None:
        query = query.where(ModelLineage.task == task)
    if q:
        query = query.where(ModelLineage.name.ilike(f"%{q}%"))
    rows = session.execute(query).all()
    ids = [lineage.id for lineage, _ in rows]
    versions = _versions_by_lineage(session, ids)
    checkpoints = _checkpoint_bytes(session, ids)
    views = [
        _lineage_summary(lineage, slug, versions[lineage.id], checkpoints.get(lineage.id, 0))
        for lineage, slug in rows
    ]
    if state is not None:
        views = [v for v in views if v["counts"][state] > 0]
    views.sort(key=lambda v: (v["updated_at"] or "", v["id"]), reverse=True)
    return views


def get_lineage(session: Session, lineage_id: str) -> ModelLineage:
    lineage = session.get(ModelLineage, lineage_id)
    if lineage is None:
        raise NotFound(f"No model lineage {lineage_id!r}.")
    return lineage


def lineage_detail(
    session: Session,
    lineage_id: str,
    *,
    state: str | None = None,
    weights: str | None = None,
) -> dict[str, Any]:
    """A lineage and its versions, newest first. ``state`` filters by version state;
    ``weights`` is ``present`` or ``deleted``."""
    lineage = get_lineage(session, lineage_id)
    slug = session.scalar(select(Dataset.slug).where(Dataset.id == lineage.dataset_id)) or ""
    versions = _versions_by_lineage(session, [lineage_id])[lineage_id]
    summary = _lineage_summary(
        lineage, slug, versions, _checkpoint_bytes(session, [lineage_id]).get(lineage_id, 0)
    )
    pinned, job_states = _context(session, versions)
    shown = [
        v
        for v in versions
        if (state is None or v.state == state)
        and (weights is None or (weights == "deleted") == (v.weights_status == "weights_deleted"))
    ]
    latest_id = summary["latest_version_id"]
    return {
        **summary,
        "versions": [
            _version_row(v, latest_id=latest_id, pinned=pinned, job_states=job_states)
            for v in shown
        ],
    }


def get_version(session: Session, version_id: str) -> ModelVersion:
    version = session.get(ModelVersion, version_id)
    if version is None:
        raise NotFound(f"No model version {version_id!r}.")
    return version


def version_detail(session: Session, version_id: str) -> dict[str, Any]:
    """Everything that made a version (REG-1.1): config, dataset version, job, parent,
    metrics and the per-epoch history."""
    version = get_version(session, version_id)
    lineage = get_lineage(session, version.lineage_id)
    siblings = _versions_by_lineage(session, [lineage.id])[lineage.id]
    latest = latest_of(siblings)
    pinned, job_states = _context(session, [version])
    return {
        **_version_row(
            version,
            latest_id=latest.id if latest else None,
            pinned=pinned,
            job_states=job_states,
        ),
        "lineage_name": lineage.name,
        "config": version.config,
        "metrics": version.metrics,
        "history": version.history or [],
        "weights_path": version.weights_path,
        "weights_deleted_at": iso(version.weights_deleted_at),
    }


# -- pins (REG-2.5) ------------------------------------------------------------------


def pin(session: Session, version_id: str) -> ModelVersion:
    """Exempt a version from retention until it is unpinned. Only a trained version with
    its weights on disk can be pinned: there is nothing else to keep."""
    version = get_version(session, version_id)
    if version.state != "trained" or version.weights_status != "present":
        raise Conflict(
            f"{version_id} is a {version.state} version with weights {version.weights_status}; "
            "only trained versions with weights on disk are pinned.",
            suggested_action="Base and partial versions are never deleted by retention; a "
            "version whose weights were deleted can be re-trained.",
        )
    session.execute(insert(Pin).values(version_id=version_id).on_conflict_do_nothing())
    session.flush()
    return version


def unpin(session: Session, version_id: str) -> ModelVersion:
    """Unpin. Nothing is deleted now; the next cleanup applies the policy (REG-2.5)."""
    version = get_version(session, version_id)
    session.execute(delete(Pin).where(Pin.version_id == version_id))
    session.flush()
    return version
