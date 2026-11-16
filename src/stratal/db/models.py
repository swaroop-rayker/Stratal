"""Table models. The authoritative description is docs/DATA_MODEL.md §4."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL
from sqlalchemy.orm import Mapped, mapped_column

from stratal.db.base import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        Index("ix_dataset_versions_dataset_created", "dataset_id", "created_at"),
        Index("ix_dataset_versions_memo", "stage", "parent_version_id", "config_hash"),
        Index("ix_dataset_versions_parent", "parent_version_id"),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("datasets.id"))
    stage: Mapped[str] = mapped_column(Text)
    parent_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    content_hash: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dtype: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str] = mapped_column(Text)
    manifest_bytes: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_blob_bytes: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer)
    code_version: Mapped[str | None] = mapped_column(Text)
    created_by_job_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("jobs.id", name="fk_dataset_versions_created_by_job")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


JOB_STATES = ("queued", "running", "paused", "completed", "failed", "cancelled", "stalled")
STAGE_STATUSES = ("pending", "queued", "running", "completed", "failed", "skipped", "cancelled")
EVENT_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_one_of("state", JOB_STATES), name="ck_jobs_state"),
        CheckConstraint(
            "desired_state IS NULL OR desired_state IN ('paused', 'cancelled')",
            name="ck_jobs_desired_state",
        ),
        Index("ix_jobs_state", "state"),
        Index("ix_jobs_created", "created_at"),
        Index(
            "ix_jobs_heartbeat_running", "heartbeat_at", postgresql_where=text("state = 'running'")
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    desired_state: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dataset_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("datasets.id"))
    input_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    output_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    current_stage_idx: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    worker: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobStage(Base):
    __tablename__ = "job_stages"
    __table_args__ = (
        CheckConstraint(_one_of("status", STAGE_STATUSES), name="ck_job_stages_status"),
        CheckConstraint("queue IN ('cpu', 'gpu')", name="ck_job_stages_queue"),
    )

    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    queue: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(Text)
    input_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    output_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, server_default="false")
    step: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int | None] = mapped_column(BigInteger)
    throughput: Mapped[float | None] = mapped_column(Float)
    eta_seconds: Mapped[float | None] = mapped_column(Float)
    activity: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    peaks: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class JobTransition(Base):
    __tablename__ = "job_transitions"
    __table_args__ = (Index("ix_job_transitions_job_at", "job_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"))
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(_one_of("level", EVENT_LEVELS), name="ck_events_level"),
        # MON-1.3 / INV-10: a warning or error always says why and what to do.
        CheckConstraint(
            "level NOT IN ('WARN', 'ERROR') "
            "OR (reason IS NOT NULL AND suggested_action IS NOT NULL)",
            name="ck_events_reason_and_action",
        ),
        Index("ix_events_job_id", "job_id", "id"),
        Index("ix_events_warn_error", "id", postgresql_where=text("level IN ('WARN', 'ERROR')")),
        Index("ix_events_ts_brin", "ts", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_uid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # No foreign key: an event must be storable even if its job row is gone or late.
    job_id: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    substage: Mapped[str | None] = mapped_column(Text)
    version_id: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text)
    step: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int | None] = mapped_column(BigInteger)
    message: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int | None] = mapped_column(Integer)


class ResourceSample(Base):
    __tablename__ = "resource_samples"
    __table_args__ = (
        Index("ix_resource_samples_ts_brin", "ts", postgresql_using="brin"),
        Index("ix_resource_samples_job_ts", "job_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    active_jobs: Mapped[list[Any] | None] = mapped_column(JSONB)
    interval_s: Mapped[float] = mapped_column(REAL)
    cpu_percent: Mapped[float] = mapped_column(REAL)
    cpu_per_core: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    ram_used_mb: Mapped[float] = mapped_column(REAL)
    ram_available_mb: Mapped[float] = mapped_column(REAL)
    ram_total_mb: Mapped[float] = mapped_column(REAL)
    swap_used_mb: Mapped[float] = mapped_column(REAL)
    gpu_util_percent: Mapped[float | None] = mapped_column(REAL)
    gpu_mem_util_percent: Mapped[float | None] = mapped_column(REAL)
    vram_used_mb: Mapped[float | None] = mapped_column(REAL)
    vram_total_mb: Mapped[float | None] = mapped_column(REAL)
    gpu_temp_c: Mapped[float | None] = mapped_column(REAL)
    gpu_power_w: Mapped[float | None] = mapped_column(REAL)
    disk_read_mb_s: Mapped[float] = mapped_column(REAL)
    disk_write_mb_s: Mapped[float] = mapped_column(REAL)


class ProcessSample(Base):
    __tablename__ = "process_samples"
    __table_args__ = (Index("ix_process_samples_job_ts", "job_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # No foreign key, for the same reason as events.job_id.
    job_id: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    rss_mb: Mapped[float] = mapped_column(REAL)
    torch_allocated_mb: Mapped[float | None] = mapped_column(REAL)
    torch_reserved_mb: Mapped[float | None] = mapped_column(REAL)
    torch_max_allocated_mb: Mapped[float | None] = mapped_column(REAL)


class Derivation(Base):
    """Content-addressed cache of expensive per-item results (ADR-0004, INV-11)."""

    __tablename__ = "derivations"

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    input_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    params_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    # No foreign key: a cached value outlives the job that made it.
    created_by_job_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


REVIEW_STATUSES = ("open", "closed")
REVIEW_DECISIONS = ("accept", "reject")


class ReviewSession(Base):
    """A sample of one version's rows for manual review (LABEL-1.5, DATA-4.7)."""

    __tablename__ = "review_sessions"
    __table_args__ = (
        CheckConstraint(_one_of("status", REVIEW_STATUSES), name="ck_review_sessions_status"),
        CheckConstraint("sample_size > 0", name="ck_review_sessions_sample_size"),
        Index("ix_review_sessions_version", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version_id: Mapped[str] = mapped_column(Text, ForeignKey("dataset_versions.version_id"))
    sample_size: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base):
    """One sampled row and its decision. Decisions change no manifest (DATA-4.7)."""

    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint(
            f"decision IS NULL OR {_one_of('decision', REVIEW_DECISIONS)}",
            name="ck_review_items_decision",
        ),
        CheckConstraint("position > 0", name="ck_review_items_position"),
        Index("uq_review_items_row", "session_id", "row_key", unique=True),
    )

    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("review_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    row_key: Mapped[str] = mapped_column(Text)
    split: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
