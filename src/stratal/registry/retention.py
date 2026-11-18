"""Weight retention: settings, the plan, the run, recovery and the disk audit.

REG-2.2 to REG-2.11, NFR-14, NFR-15, BUG7; ADR-0015 rules 4 to 8. Torch-free.

- **Settings** live in ``retention_settings`` (OC-9): a global ``keep_intermediate``
  and per-lineage overrides; "all" is stored as null.
- **The plan** is :func:`stratal.registry.policy.plan_lineage` over facts read
  here: pins, children, and what is in use (versions a queued, running or paused
  run starts from, and the version the inference service reports holding).
  If Redis cannot be read, nothing beyond what the rule keeps anyway is deleted.
- **A run** takes each lineage's lock, finishes deletes a crash interrupted,
  deletes what the plan says (one version at a time: mark ``deleting``, commit,
  remove the folder, mark ``weights_deleted``, commit), removes checkpoints of
  completed and cancelled runs, and sweeps folders no row accounts for.
"""

import hashlib
import json
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import ValidationFailed
from stratal.db.models import Checkpoint, Job, ModelLineage, ModelVersion, Pin, RetentionSetting
from stratal.mon.events import MetricValue, utc_now
from stratal.registry import store
from stratal.registry.policy import Decision, LineagePlan, VersionFacts, plan_lineage

ACTIVE_JOB_STATES = ("queued", "running", "paused", "stalled")
STALE_CHECKPOINT_JOB_STATES = ("completed", "cancelled")
"""A failed run keeps its checkpoint: a retry resumes from it (RUN-1.3; the owner's
choice, 2026-11-18)."""
MB = 2**20

Keep = Annotated[int, Field(ge=0, le=100)] | Literal["all"]


class RetentionValues(BaseModel):
    """The retention section of the settings (REG-2.3, REG-2.4; SCR-14)."""

    model_config = ConfigDict(extra="forbid")

    keep_intermediate: Keep = Field(
        default=0,
        description="Intermediate versions each lineage keeps besides base and latest. "
        '0–100, or "all"; default 0.',
        json_schema_extra={"x-applies": "at the next cleanup"},
    )
    overrides: dict[str, Keep] = Field(
        default_factory=lambda: dict[str, int | Literal["all"]](),
        description="keep_intermediate for single lineages, by lineage ID (REG-2.4).",
        json_schema_extra={"x-applies": "at the next cleanup"},
    )


def _to_db(keep: int | Literal["all"]) -> int | None:
    return None if keep == "all" else keep


def _from_db(keep: int | None) -> int | Literal["all"]:
    return "all" if keep is None else keep


def read_values(session: Session) -> RetentionValues:
    rows = session.scalars(select(RetentionSetting)).all()
    global_row = next((r for r in rows if r.lineage_id is None), None)
    return RetentionValues(
        keep_intermediate=_from_db(global_row.keep_intermediate) if global_row else 0,
        overrides={
            r.lineage_id: _from_db(r.keep_intermediate) for r in rows if r.lineage_id is not None
        },
    )


def values_version(values: RetentionValues) -> str:
    """A short fingerprint of the settings, sent back with a save so a stale form is
    refused instead of overwriting someone else's change."""
    canonical = json.dumps(values.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def write_values(session: Session, values: RetentionValues) -> None:
    unknown = sorted(
        set(values.overrides)
        - set(
            session.scalars(
                select(ModelLineage.id).where(ModelLineage.id.in_(list(values.overrides)))
            )
        )
    )
    if unknown:
        raise ValidationFailed(
            f"No model lineage {unknown[0]!r}.",
            suggested_action="Override lineages listed by GET /models.",
            errors=[
                {"loc": ["values", "overrides", unknown[0]], "msg": "unknown", "code": "not_found"}
            ],
        )
    session.execute(delete(RetentionSetting))
    now = utc_now()
    session.add(
        RetentionSetting(
            lineage_id=None, keep_intermediate=_to_db(values.keep_intermediate), updated_at=now
        )
    )
    for lineage_id, keep in values.overrides.items():
        session.add(
            RetentionSetting(lineage_id=lineage_id, keep_intermediate=_to_db(keep), updated_at=now)
        )
    session.flush()


def effective_keep(values: RetentionValues, lineage_id: str) -> tuple[int | None, str]:
    """``(keep_intermediate, source)``; ``None`` means all, source is ``lineage`` or ``global``."""
    if lineage_id in values.overrides:
        return _to_db(values.overrides[lineage_id]), "lineage"
    return _to_db(values.keep_intermediate), "global"


# -- what is in use (REG-2.8) ------------------------------------------------------


@dataclass(frozen=True)
class InUse:
    reasons: dict[str, str]
    unknown: str | None = None
    """Set when it cannot be known what inference holds: every candidate is then blocked."""


def in_use(session: Session, loaded: dict[str, Any] | None, *, redis_ok: bool) -> InUse:
    reasons: dict[str, str] = {}
    if loaded and loaded.get("version_id"):
        device = loaded.get("device") or "?"
        reasons[str(loaded["version_id"])] = f"loaded for inference on the {device}"
    active = session.execute(
        select(ModelVersion.parent_version_id, Job.id)
        .join(Job, Job.id == ModelVersion.job_id)
        .where(
            Job.state.in_(ACTIVE_JOB_STATES),
            ModelVersion.state != "base",
            ModelVersion.parent_version_id.is_not(None),
        )
    ).all()
    for parent, job_id in active:
        reasons.setdefault(str(parent), f"{job_id} started from it")
    unknown = (
        None
        if redis_ok
        else "cannot tell which version the inference service holds: Redis is unreachable"
    )
    return InUse(reasons, unknown)


def loaded_from(read: Callable[[], dict[str, Any] | None]) -> tuple[dict[str, Any] | None, bool]:
    """What the inference service reports holding, and whether Redis answered."""
    try:
        return read(), True
    except Exception:
        return None, False


# -- facts and plans ---------------------------------------------------------------


def _facts(session: Session, lineage_id: str, use: InUse) -> list[VersionFacts]:
    versions = session.scalars(
        select(ModelVersion).where(ModelVersion.lineage_id == lineage_id)
    ).all()
    ids = [v.id for v in versions]
    pinned = set(session.scalars(select(Pin.version_id).where(Pin.version_id.in_(ids))))
    children: dict[str, list[str]] = {}
    for child, parent in session.execute(
        select(ModelVersion.id, ModelVersion.parent_version_id).where(
            ModelVersion.parent_version_id.in_(ids)
        )
    ):
        children.setdefault(str(parent), []).append(child)
    facts: list[VersionFacts] = []
    for v in versions:
        reason = use.reasons.get(v.id)
        if reason is None and v.state == "trained":
            reason = use.unknown
        facts.append(
            VersionFacts(
                id=v.id,
                state=v.state,
                weights_status=v.weights_status,
                order=v.trained_at or v.created_at,
                weights_bytes=v.weights_bytes or 0,
                pinned=v.id in pinned,
                in_use=reason,
                children=tuple(children.get(v.id, [])),
            )
        )
    return facts


def lineage_plan(
    session: Session, lineage_id: str, values: RetentionValues, use: InUse
) -> tuple[LineagePlan, int | None, str]:
    keep, source = effective_keep(values, lineage_id)
    return plan_lineage(_facts(session, lineage_id, use), keep), keep, source


@dataclass(frozen=True)
class StaleCheckpoint:
    job_id: str
    lineage_id: str
    path: str
    bytes: int
    job_state: str


def stale_checkpoints(session: Session, lineage_ids: Sequence[str]) -> list[StaleCheckpoint]:
    """Checkpoints of runs that completed or were cancelled (REG-2.6)."""
    rows = session.execute(
        select(Checkpoint, Job.state)
        .join(Job, Job.id == Checkpoint.job_id)
        .where(
            Checkpoint.lineage_id.in_(lineage_ids),
            Job.state.in_(STALE_CHECKPOINT_JOB_STATES),
        )
        .order_by(Checkpoint.job_id)
    ).all()
    return [StaleCheckpoint(c.job_id, c.lineage_id, c.path, c.bytes, state) for c, state in rows]


def _lineages(session: Session, lineage_ids: Iterable[str] | None) -> list[ModelLineage]:
    query = select(ModelLineage).order_by(ModelLineage.id)
    if lineage_ids is not None:
        query = query.where(ModelLineage.id.in_(list(lineage_ids)))
    return list(session.scalars(query))


def _decision_view(d: Decision, versions: dict[str, ModelVersion]) -> dict[str, Any]:
    v = versions.get(d.version_id)
    metric = (v.metrics or {}).get(store.MAIN_METRIC) if v is not None else None
    return {
        "version_id": d.version_id,
        "reason": d.reason,
        "weights_bytes": d.weights_bytes,
        "main_metric": float(metric) if isinstance(metric, int | float) else None,
        "created_at": store.iso(v.created_at) if v is not None else None,
    }


def plan_token(values: RetentionValues, deletions: Sequence[tuple[str, int]]) -> str:
    """Names one exact plan: the settings and every version it deletes with its size.
    A confirmation carrying another token is refused (ADR-0015 rule 7)."""
    body = json.dumps(
        {"values": values.model_dump(mode="json"), "delete": sorted(deletions)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def build_plan(
    session: Session,
    *,
    use: InUse,
    lineage_ids: Iterable[str] | None = None,
    values: RetentionValues | None = None,
) -> dict[str, Any]:
    """The dry run (REG-2.9): what a run with ``values`` (default: the saved settings)
    would delete, keep and refuse, lineage by lineage. Changes nothing."""
    values = values or read_values(session)
    lineages = _lineages(session, lineage_ids)
    items: list[dict[str, Any]] = []
    deletions: list[tuple[str, int]] = []
    stale = {c.job_id: c for c in stale_checkpoints(session, [lin.id for lin in lineages])}
    for lineage in lineages:
        plan, keep, source = lineage_plan(session, lineage.id, values, use)
        versions = {
            v.id: v
            for v in session.scalars(
                select(ModelVersion).where(ModelVersion.lineage_id == lineage.id)
            )
        }
        deletions += [(d.version_id, d.weights_bytes) for d in plan.delete]
        checkpoints: list[dict[str, Any]] = [
            {
                "job_id": c.job_id,
                "bytes": c.bytes,
                "reason": f"run {c.job_state}: its checkpoint is no longer needed (REG-2.6)",
            }
            for c in stale.values()
            if c.lineage_id == lineage.id
        ]
        items.append(
            {
                "lineage_id": lineage.id,
                "name": lineage.name,
                "keep_intermediate": _from_db(keep),
                "source": source,
                "latest_version_id": plan.latest,
                "delete": [_decision_view(d, versions) for d in plan.delete],
                "keep": [_decision_view(d, versions) for d in plan.keep],
                "blocked": [_decision_view(d, versions) for d in plan.blocked],
                "checkpoints": checkpoints,
                "bytes_to_free": plan.bytes_to_free + sum(c["bytes"] for c in checkpoints),
            }
        )
    return {
        "lineages": items,
        "versions_to_delete": len(deletions),
        "bytes_to_free": sum(item["bytes_to_free"] for item in items),
        "token": plan_token(values, deletions),
        "settings_version": values_version(values),
    }


def decisions(session: Session, lineage_id: str, use: InUse) -> dict[str, tuple[str, str]]:
    """Per version: ``(action, reason)`` under the saved settings, for the registry views.
    ``action`` is ``keep``, ``delete`` (at the next cleanup) or ``blocked``."""
    plan, _, _ = lineage_plan(session, lineage_id, read_values(session), use)
    found: dict[str, tuple[str, str]] = {}
    for action, items in (("keep", plan.keep), ("delete", plan.delete), ("blocked", plan.blocked)):
        for d in items:
            found[d.version_id] = (action, d.reason)
    return found


# -- running it --------------------------------------------------------------------


Emit = Callable[..., None]
"""``emit(substage=, message=, level=, metrics=, reason=, suggested_action=)``."""


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


@dataclass
class Totals:
    versions_deleted: int = 0
    bytes_freed: int = 0
    checkpoints_removed: int = 0
    blocked: int = 0
    orphans_removed: int = 0
    recovered: int = 0
    would_delete: int = 0
    bytes_to_free: int = 0

    def metrics(self, *, dry_run: bool, lineages: int) -> dict[str, MetricValue]:
        out: dict[str, MetricValue] = {"lineages": lineages, "dry_run": int(dry_run)}
        if dry_run:
            out |= {"would_delete": self.would_delete, "bytes_to_free": self.bytes_to_free}
        else:
            out |= {
                "versions_deleted": self.versions_deleted,
                "bytes_freed": self.bytes_freed,
                "checkpoints_removed": self.checkpoints_removed,
                "blocked": self.blocked,
                "orphans_removed": self.orphans_removed,
                "recovered": self.recovered,
            }
        return out


def _finish_delete(
    engine: Engine, data_dir: Path, version_id: str, job_id: str, emit: Emit, totals: Totals
) -> None:
    """Steps 2 and 3 of an atomic delete: remove the files, then record it (NFR-15)."""
    with Session(engine) as session:
        version = session.get(ModelVersion, version_id)
        assert version is not None
        folder = data_dir / (
            version.weights_path or store.version_folder(version.lineage_id, version.id)
        )
        lineage_id = version.lineage_id
    freed = _tree_bytes(folder)
    shutil.rmtree(folder, ignore_errors=True)
    with Session(engine) as session, session.begin():
        version = session.get(ModelVersion, version_id, with_for_update=True)
        assert version is not None
        version.weights_status = "weights_deleted"
        version.weights_deleted_at = utc_now()
        version.weights_deleted_by_job_id = job_id
    totals.versions_deleted += 1
    totals.bytes_freed += freed
    emit(
        substage="delete",
        message=f"Deleted the weights of {version_id} ({freed / MB:.1f} MB freed); "
        "its metadata is kept",
        metrics={"version_id": version_id, "lineage_id": lineage_id, "bytes_freed": freed},
    )


def _delete_version(
    engine: Engine, data_dir: Path, version_id: str, job_id: str, emit: Emit, totals: Totals
) -> None:
    with Session(engine) as session, session.begin():
        version = session.get(ModelVersion, version_id, with_for_update=True)
        if version is None or version.weights_status != "present":
            return
        version.weights_status = "deleting"
    emit(
        substage="deleting",
        message=f"Deleting the weights of {version_id}",
        metrics={"version_id": version_id},
    )
    _finish_delete(engine, data_dir, version_id, job_id, emit, totals)


def _recover(
    engine: Engine, data_dir: Path, lineage_id: str, job_id: str, emit: Emit, totals: Totals
) -> None:
    """Finish deletes a crash left ``deleting`` (ADR-0015 rule 5)."""
    with Session(engine) as session:
        stuck = list(
            session.scalars(
                select(ModelVersion.id).where(
                    ModelVersion.lineage_id == lineage_id,
                    ModelVersion.weights_status == "deleting",
                )
            )
        )
    for version_id in stuck:
        emit(
            substage="recover",
            level="WARN",
            message=f"Finishing an interrupted delete of {version_id}",
            reason="A previous cleanup stopped between marking the version and recording it.",
            suggested_action="None: this run completes it.",
            metrics={"version_id": version_id},
        )
        totals.recovered += 1
        _finish_delete(engine, data_dir, version_id, job_id, emit, totals)


def _remove_checkpoint(
    engine: Engine, data_dir: Path, stale: StaleCheckpoint, job_id: str, emit: Emit, totals: Totals
) -> None:
    folder = (data_dir / stale.path).parent
    freed = 0
    for path in folder.glob("ckpt-*"):
        freed += path.stat().st_size
        path.unlink(missing_ok=True)
    with Session(engine) as session, session.begin():
        store.drop_checkpoint(session, stale.job_id)
        version = store.run_version(session, stale.job_id)
        if version is not None and version.state == "partial" and stale.job_state == "cancelled":
            version.weights_status = "weights_deleted"
            version.weights_deleted_at = utc_now()
            version.weights_deleted_by_job_id = job_id
    totals.checkpoints_removed += 1
    totals.bytes_freed += freed
    emit(
        substage="checkpoint",
        message=f"Removed the checkpoint of {stale.job_id}, {stale.job_state} "
        f"({freed / MB:.1f} MB freed)",
        metrics={"checkpoint_job_id": stale.job_id, "bytes_freed": freed},
    )


def _sweep(engine: Engine, data_dir: Path, lineage_id: str, emit: Emit, totals: Totals) -> None:
    """Remove folders under ``registry/<lineage>/`` that no row accounts for: leftovers of
    a crash before a commit, ``.part`` folders, and weights a row says are deleted."""
    folder = data_dir / store.REGISTRY_DIR / lineage_id
    if not folder.is_dir():
        return
    with Session(engine) as session:
        expected = {
            Path(v.weights_path).name
            for v in session.scalars(
                select(ModelVersion).where(
                    ModelVersion.lineage_id == lineage_id,
                    ModelVersion.weights_status == "present",
                    ModelVersion.weights_path.is_not(None),
                )
            )
            if v.weights_path
        }
    for child in sorted(folder.iterdir()):
        if child.name in expected:
            continue
        size = _tree_bytes(child)
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        totals.orphans_removed += 1
        totals.bytes_freed += size
        emit(
            substage="orphan",
            level="WARN",
            message=f"Removed {store.REGISTRY_DIR}/{lineage_id}/{child.name}: no version "
            f"has it ({size / MB:.1f} MB)",
            reason="A run stopped after writing files and before recording them.",
            suggested_action="None: the registry and the disk agree again.",
            metrics={"bytes_freed": size},
        )
    if not any(folder.iterdir()):
        folder.rmdir()


def run(
    engine: Engine,
    data_dir: Path,
    emit: Emit,
    *,
    job_id: str,
    lineage_ids: Sequence[str] | None,
    dry_run: bool,
    confirmed: Sequence[str] | None,
    loaded: dict[str, Any] | None,
    redis_ok: bool,
) -> dict[str, MetricValue]:
    """One retention run (REG-2.9). ``confirmed`` limits deletions to a plan the owner
    confirmed (REG-2.10); ``None`` applies the policy as it stands (REG-2.2)."""
    totals = Totals()
    allowed = set(confirmed) if confirmed is not None else None
    with Session(engine) as session:
        lineages = [lin.id for lin in _lineages(session, lineage_ids)]
        known = set(session.scalars(select(ModelLineage.id)))
    registry = data_dir / store.REGISTRY_DIR
    unknown_dirs = (
        sorted(p.name for p in registry.iterdir() if p.is_dir() and p.name not in known)
        if lineage_ids is None and registry.is_dir()
        else []
    )
    for lineage_id in lineages:
        with store.holding_lineage(engine, lineage_id):
            if not dry_run:
                _recover(engine, data_dir, lineage_id, job_id, emit, totals)
            with Session(engine) as session:
                use = in_use(session, loaded, redis_ok=redis_ok)
                plan, keep, source = lineage_plan(session, lineage_id, read_values(session), use)
                stale = stale_checkpoints(session, [lineage_id])
                name = session.get(ModelLineage, lineage_id).name  # type: ignore[union-attr]
            n = "all" if keep is None else keep
            emit(
                substage="plan",
                message=f"{name}: keep_intermediate = {n} ({source}); "
                f"{len(plan.delete)} to delete, {len(plan.blocked)} refused, "
                f"{len(stale)} stale checkpoints",
                metrics={"lineage_id": lineage_id, "would_delete": len(plan.delete)},
            )
            for d in plan.blocked:
                totals.blocked += 1
                emit(
                    substage="blocked",
                    level="WARN",
                    message=f"Kept {d.version_id}, which the policy would delete: {d.reason}",
                    reason=d.reason,
                    suggested_action="It is deleted by a later cleanup once it is no longer "
                    "in use.",
                    metrics={"version_id": d.version_id},
                )
            if dry_run:
                for d in plan.delete:
                    totals.would_delete += 1
                    totals.bytes_to_free += d.weights_bytes
                    emit(
                        substage="plan",
                        message=f"Would delete the weights of {d.version_id} "
                        f"({d.weights_bytes / MB:.1f} MB)",
                        metrics={"version_id": d.version_id, "bytes": d.weights_bytes},
                    )
                for c in stale:
                    totals.bytes_to_free += c.bytes
                    emit(
                        substage="plan",
                        message=f"Would remove the checkpoint of {c.job_id} "
                        f"({c.bytes / MB:.1f} MB)",
                        metrics={"checkpoint_job_id": c.job_id, "bytes": c.bytes},
                    )
                continue
            for d in plan.delete:
                if allowed is not None and d.version_id not in allowed:
                    emit(
                        substage="plan",
                        message=f"Kept {d.version_id}: it was not in the confirmed plan",
                        metrics={"version_id": d.version_id},
                    )
                    continue
                _delete_version(engine, data_dir, d.version_id, job_id, emit, totals)
            for c in stale:
                _remove_checkpoint(engine, data_dir, c, job_id, emit, totals)
            _sweep(engine, data_dir, lineage_id, emit, totals)
    if not dry_run:
        for name in unknown_dirs:
            with store.holding_lineage(engine, name):
                with Session(engine) as session:
                    if session.get(ModelLineage, name) is not None:
                        continue  # created while we were looking
                _sweep(engine, data_dir, name, emit, totals)
    return totals.metrics(dry_run=dry_run, lineages=len(lineages))


# -- disk audit (REG-2.11, NFR-14) -------------------------------------------------


def disk_report(session: Session, data_dir: Path, lineage_id: str) -> dict[str, Any]:
    """Disk use per lineage measured on disk, by part, against the registry's figures."""
    store.get_lineage(session, lineage_id)
    versions = {
        v.id: v
        for v in session.scalars(select(ModelVersion).where(ModelVersion.lineage_id == lineage_id))
    }
    latest = store.latest_of(list(versions.values()))
    pinned = set(session.scalars(select(Pin.version_id).where(Pin.version_id.in_(list(versions)))))
    parts = {"base": 0, "latest": 0, "intermediate": 0, "pinned": 0, "checkpoints": 0}
    orphans: list[dict[str, Any]] = []
    missing: list[str] = []
    folder = data_dir / store.REGISTRY_DIR / lineage_id
    seen: set[str] = set()
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            size = _tree_bytes(child)
            version = versions.get(child.name)
            if version is None or version.weights_status != "present" or version.state == "partial":
                orphans.append(
                    {"path": f"{store.REGISTRY_DIR}/{lineage_id}/{child.name}", "bytes": size}
                )
                continue
            seen.add(child.name)
            if version.state == "base":
                parts["base"] += size
            elif latest is not None and version.id == latest.id:
                parts["latest"] += size
            elif version.id in pinned:
                parts["pinned"] += size
            else:
                parts["intermediate"] += size
    registry_bytes = 0
    for v in versions.values():
        if v.weights_status == "present" and v.state != "partial":
            registry_bytes += v.weights_bytes or 0
            if v.id not in seen:
                missing.append(v.id)
    for c in session.scalars(select(Checkpoint).where(Checkpoint.lineage_id == lineage_id)):
        registry_bytes += c.bytes
        parts["checkpoints"] += sum(
            p.stat().st_size for p in (data_dir / c.path).parent.glob("ckpt-*") if p.is_file()
        )
    disk_bytes = sum(parts.values()) + sum(o["bytes"] for o in orphans)
    return {
        "lineage_id": lineage_id,
        "parts": parts,
        "disk_bytes": disk_bytes,
        "registry_bytes": registry_bytes,
        "matches": disk_bytes == registry_bytes and not orphans and not missing,
        "orphans": orphans,
        "missing": missing,
    }


def last_run(session: Session, lineage_id: str) -> dict[str, Any] | None:
    """The newest completed real retention run that covered the lineage, and what it freed."""
    jobs = session.scalars(
        select(Job)
        .where(Job.kind == "retention", Job.state == "completed")
        .order_by(Job.finished_at.desc())
        .limit(50)
    ).all()
    for job in jobs:
        scope = job.spec.get("lineage_ids")
        if job.spec.get("dry_run") or (scope is not None and lineage_id not in scope):
            continue
        deleted = session.scalars(
            select(ModelVersion).where(
                ModelVersion.lineage_id == lineage_id,
                ModelVersion.weights_deleted_by_job_id == job.id,
            )
        ).all()
        return {
            "job_id": job.id,
            "finished_at": store.iso(job.finished_at),
            "versions_deleted": len(deleted),
            "bytes_freed": sum(v.weights_bytes or 0 for v in deleted if v.state != "partial"),
        }
    return None
