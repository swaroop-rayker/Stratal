"""Read-only views of datasets and versions for the API (DATA-3.1 to 3.4, UI-2.1).

Plumbing: everything here reads what the registry and the manifests already
hold. Nothing changes a version.
"""

from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.core.errors import NotFound
from stratal.data import registry
from stratal.db.models import Dataset, DatasetVersion, Job, JobStage
from stratal.eval.report import DEFAULT_EDGES
from stratal.eval.report_stats import score_distribution
from stratal.runtime.states import ACTIVE
from stratal.storage.manifest import STATUSES, iter_rows

Json = dict[str, Any]

SIZE_EDGES: tuple[float, ...] = tuple(float(2**k) for k in range(10, 27))
"""File-size histogram edges in bytes: 1 KiB to 64 MiB, doubling. Fixed, so versions compare."""

ROW_FILTERS = ("status", "reason_code", "split", "dup_group_id")


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def get_dataset(session: Session, slug: str) -> Dataset:
    dataset = session.scalar(select(Dataset).where(Dataset.slug == slug))
    if dataset is None:
        raise NotFound(
            f"No dataset {slug!r}.",
            suggested_action="List datasets with GET /datasets, or ingest a folder to create it.",
        )
    return dataset


def version_view(session: Session, version: DatasetVersion, slug: str | None = None) -> Json:
    """A version as API.md §3.2 describes it, with the creating job's state."""
    if slug is None:
        dataset = session.get(Dataset, version.dataset_id)
        slug = dataset.slug if dataset is not None else ""
    job_state = None
    if version.created_by_job_id is not None:
        job = session.get(Job, version.created_by_job_id)
        job_state = job.state if job is not None else None
    return {
        "version_id": version.version_id,
        "dataset": slug,
        "stage": version.stage,
        "parent_version_id": version.parent_version_id,
        "content_hash": version.content_hash,
        "config_hash": version.config_hash,
        "config": version.config,
        "dtype": version.dtype,
        "row_count": version.row_count,
        "counts": version.counts,
        "manifest_path": version.manifest_path,
        "manifest_bytes": version.manifest_bytes,
        "new_blob_bytes": version.new_blob_bytes,
        "schema_version": version.schema_version,
        "code_version": version.code_version,
        "created_by_job_id": version.created_by_job_id,
        "created_by_job_state": job_state,
        "created_at": iso(version.created_at),
    }


def dataset_view(session: Session, dataset: Dataset) -> Json:
    """A dataset with its latest version and totals over all its versions."""
    versions = list(
        session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.created_at.desc(), DatasetVersion.version_id)
        )
    )
    latest = versions[0] if versions else None
    return {
        "slug": dataset.slug,
        "display_name": dataset.display_name,
        "modality": dataset.modality,
        "source_uri": dataset.source_uri,
        "created_at": iso(dataset.created_at),
        "updated_at": iso(latest.created_at if latest is not None else dataset.created_at),
        "version_count": len(versions),
        "stored_bytes": sum(v.manifest_bytes + v.new_blob_bytes for v in versions),
        "latest_version": version_view(session, latest, dataset.slug) if latest else None,
    }


def list_datasets(session: Session) -> list[Json]:
    """Every dataset, most recently updated first."""
    views = [dataset_view(session, d) for d in session.scalars(select(Dataset))]
    return sorted(views, key=lambda v: (v["updated_at"] or "", v["slug"]), reverse=True)


def descendants(session: Session, version_id: str) -> list[DatasetVersion]:
    """Every version made from ``version_id``, directly or not, breadth first (DATA-3.3)."""
    found: list[DatasetVersion] = []
    seen = {version_id}
    queue = deque([version_id])
    while queue:
        parent = queue.popleft()
        children = session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.parent_version_id == parent)
            .order_by(DatasetVersion.created_at, DatasetVersion.version_id)
        )
        for child in children:
            if child.version_id not in seen:
                seen.add(child.version_id)
                found.append(child)
                queue.append(child.version_id)
    return found


def jobs_extending(session: Session, version_id: str) -> list[Json]:
    """Active jobs whose next version will be a child of ``version_id``.

    A job extends the output of its last completed stage, or its input version
    when no stage has completed yet. SCR-03 draws these as a running child.
    """
    extending: list[Json] = []
    for job in session.scalars(select(Job).where(Job.state.in_(ACTIVE)).order_by(Job.id)):
        stages = list(
            session.scalars(
                select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.idx)
            )
        )
        base = job.input_version_id
        for stage in stages:
            if stage.status == "completed" and stage.output_version_id is not None:
                base = stage.output_version_id
        current = next((s for s in stages if s.status not in ("completed", "skipped")), None)
        if base == version_id and current is not None:
            extending.append(
                {
                    "job_id": job.id,
                    "state": job.state,
                    "stage": current.name,
                    "step": current.step,
                    "total": current.total,
                }
            )
    return extending


def lineage_view(session: Session, version_id: str) -> Json:
    """Ancestors back to raw (nearest first), descendants, and jobs about to add a child."""
    chain = registry.lineage(session, version_id)
    return {
        "version_id": version_id,
        "ancestors": [version_view(session, v) for v in chain[1:]],
        "descendants": [version_view(session, v) for v in descendants(session, version_id)],
        "in_progress": jobs_extending(session, version_id),
    }


@dataclass(frozen=True)
class _ReasonKey:
    reason_code: str
    decided_at_stage: str
    status: str


def version_stats(path: Path, row_count: int) -> Json:
    """Counts by status, reason and deciding stage, rows per split, and the size and
    CLIP-score distributions of the accepted rows (API.md §3.2)."""
    by_status: Counter[str] = Counter()
    by_reason: Counter[_ReasonKey] = Counter()
    by_split: Counter[str] = Counter()
    sizes: list[float] = []
    scores: list[float] = []
    columns = ["status", "decided_at_stage", "reason_code", "size_bytes", "clip_score", "split"]
    for row in iter_rows(path, columns=columns):
        status = str(row["status"])
        by_status[status] += 1
        if status != "accepted":
            key = _ReasonKey(
                str(row["reason_code"] or ""), str(row["decided_at_stage"] or ""), status
            )
            by_reason[key] += 1
            continue
        if row["size_bytes"] is not None:
            sizes.append(float(row["size_bytes"]))
        if row["clip_score"] is not None:
            scores.append(float(row["clip_score"]))
        if row["split"] is not None:
            by_split[str(row["split"])] += 1
    reasons = [
        {
            "reason_code": key.reason_code,
            "decided_at_stage": key.decided_at_stage,
            "status": key.status,
            "rows": count,
            "share": count / row_count if row_count else 0.0,
        }
        for key, count in sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0].reason_code))
    ]
    by_stage: dict[str, dict[str, int]] = {}
    for key, count in by_reason.items():
        stage = by_stage.setdefault(key.decided_at_stage, {})
        stage[key.reason_code] = stage.get(key.reason_code, 0) + count
    return {
        "row_count": row_count,
        "by_status": {status: by_status.get(status, 0) for status in STATUSES},
        "reasons": reasons,
        "by_stage": by_stage,
        "by_split": dict(sorted(by_split.items())) if by_split else None,
        "size_bytes": asdict(score_distribution(sizes, edges=SIZE_EDGES)),
        "clip_score": asdict(score_distribution(scores, edges=DEFAULT_EDGES)) if scores else None,
    }
