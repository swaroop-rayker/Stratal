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
STAGE_STATUSES = (
    "pending",
    "queued",
    "running",
    "paused",
    "waiting",
    "completed",
    "failed",
    "skipped",
    "cancelled",
)
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
        CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ('user', 'thermal')",
            name="ck_jobs_pause_reason",
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
    pause_reason: Mapped[str | None] = mapped_column(Text)
    """Why a paused job is paused: 'user' or 'thermal' (RUN-1.6). Only thermal pauses are
    resumed by the watchdog."""
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
        Index("ix_events_caused_by", "caused_by", postgresql_where=text("caused_by IS NOT NULL")),
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
    # The event_uid of the alert that caused this event (ADR-0020 rule 4). No foreign key:
    # the writer knows the alert's uid before its row exists.
    caused_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


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
    # CPU time of the job's process tree per wall second, in cores (MON-3.2, OC-12).
    cpu_cores: Mapped[float | None] = mapped_column(REAL)


class ResourceSample1m(Base):
    """One-minute averages of ``resource_samples`` older than the raw retention (NFR-12).

    One row per minute, job and stage. ``samples`` is how many raw rows it averages;
    the per-core list and the active-job list are not kept.
    """

    __tablename__ = "resource_samples_1m"
    __table_args__ = (
        Index("ix_resource_samples_1m_ts_brin", "ts", postgresql_using="brin"),
        Index("ix_resource_samples_1m_job_ts", "job_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    samples: Mapped[int] = mapped_column(Integer)
    interval_s: Mapped[float] = mapped_column(REAL)
    cpu_percent: Mapped[float] = mapped_column(REAL)
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


class ProcessSample1m(Base):
    """One-minute averages of ``process_samples`` (NFR-12); the allocator peak is a maximum."""

    __tablename__ = "process_samples_1m"
    __table_args__ = (Index("ix_process_samples_1m_job_ts", "job_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    samples: Mapped[int] = mapped_column(Integer)
    rss_mb: Mapped[float] = mapped_column(REAL)
    torch_allocated_mb: Mapped[float | None] = mapped_column(REAL)
    torch_reserved_mb: Mapped[float | None] = mapped_column(REAL)
    torch_max_allocated_mb: Mapped[float | None] = mapped_column(REAL)
    cpu_cores: Mapped[float | None] = mapped_column(REAL)


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


REPORT_KINDS = ("phase1_dataset",)


class EvalReport(Base):
    """A report describing one dataset version (EVAL-1.1)."""

    __tablename__ = "eval_reports"
    __table_args__ = (
        CheckConstraint(_one_of("kind", REPORT_KINDS), name="ck_eval_reports_kind"),
        Index("ix_eval_reports_version_created", "version_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    version_id: Mapped[str] = mapped_column(Text, ForeignKey("dataset_versions.version_id"))
    kind: Mapped[str] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    review_session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("review_sessions.id"))
    job_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("jobs.id", name="fk_eval_reports_job")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# -- model registry (M8, ADR-0015, DATA_MODEL §4.12) ---------------------------------

VERSION_STATES = ("base", "partial", "trained")
WEIGHTS_STATUSES = ("present", "deleting", "weights_deleted")


class ModelLineage(Base):
    """One model on one task: a model spec trained on one dataset's inputs and classes
    (ADR-0015 rule 1). Its ID is derived from that key, so the same key finds it again."""

    __tablename__ = "model_lineages"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)
    model_spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dataset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("datasets.id"))
    input_shape: Mapped[list[int]] = mapped_column(JSONB)
    classes: Mapped[list[str]] = mapped_column(JSONB)
    parameters: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelVersion(Base):
    """A lineage's base weights, or one training run's result (REG-1.1, REG-1.2)."""

    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint(_one_of("state", VERSION_STATES), name="ck_model_versions_state"),
        CheckConstraint(
            _one_of("weights_status", WEIGHTS_STATUSES), name="ck_model_versions_weights_status"
        ),
        CheckConstraint(
            "weights_status <> 'present' OR (weights_path IS NOT NULL "
            "AND weights_bytes IS NOT NULL) OR state = 'partial'",
            name="ck_model_versions_present_has_files",
        ),
        Index("ix_model_versions_lineage_created", "lineage_id", "created_at"),
        Index(
            "uq_model_versions_base",
            "lineage_id",
            unique=True,
            postgresql_where=text("state = 'base'"),
        ),
        Index(
            "uq_model_versions_run",
            "job_id",
            unique=True,
            postgresql_where=text("state <> 'base'"),
        ),
        Index("ix_model_versions_parent", "parent_version_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    lineage_id: Mapped[str] = mapped_column(Text, ForeignKey("model_lineages.id"))
    state: Mapped[str] = mapped_column(Text)
    weights_status: Mapped[str] = mapped_column(Text)
    parent_version_id: Mapped[str | None] = mapped_column(Text, ForeignKey("model_versions.id"))
    job_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("jobs.id", name="fk_model_versions_job")
    )
    """The run that made it; for a base, the run that created the lineage."""
    dataset_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    history: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    weights_path: Mapped[str | None] = mapped_column(Text)
    """Relative to the data folder: ``registry/<lineage>/<version>``."""
    weights_bytes: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weights_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weights_deleted_by_job_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("jobs.id", name="fk_model_versions_deleted_by_job")
    )
    """The retention job that deleted the weights (REG-2.9)."""
    study_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("studies.id", name="fk_model_versions_study")
    )
    """The study whose best trial this is (HPO, ADR-0018 rule 7); null for an ordinary run."""


STUDY_KINDS = ("classical", "deep")
STUDY_STATES = ("running", "closed", "completed", "cancelled", "failed")
TRIAL_STATES = ("running", "complete", "pruned", "failed")


class Study(Base):
    """An HPO study: what is tuned, its limits, and what it found (HPO-1.1 to HPO-1.5,
    ADR-0018). Optuna's own tables, in schema ``optuna``, are the sampler's memory; this
    row and its trials are what the API reads."""

    __tablename__ = "studies"
    __table_args__ = (
        CheckConstraint(_one_of("kind", STUDY_KINDS), name="ck_studies_kind"),
        CheckConstraint(_one_of("state", STUDY_STATES), name="ck_studies_state"),
        CheckConstraint(
            _one_of("direction", ("maximize", "minimize")), name="ck_studies_direction"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id"), unique=True)
    kind: Mapped[str] = mapped_column(Text)
    dataset_version_id: Mapped[str] = mapped_column(Text, ForeignKey("dataset_versions.version_id"))
    base: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The run request every trial starts from, defaults filled in."""
    space: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    objective: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """``{n_trials, timeout_s}``: whichever comes first ends the study (HPO-1.2)."""
    pruning: Mapped[bool] = mapped_column(Boolean)
    cv_folds: Mapped[int | None] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text, server_default="running")
    ended_by: Mapped[str | None] = mapped_column(Text)
    """``n_trials`` or ``timeout``: which limit ended it."""
    elapsed_s: Mapped[float] = mapped_column(Float, server_default="0")
    """Running time used, pauses excluded."""
    best_trial: Mapped[int | None] = mapped_column(Integer)
    best_value: Mapped[float | None] = mapped_column(Float)
    best_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    importance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    best_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("model_versions.id", name="fk_studies_best_version", use_alter=True)
    )
    test_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    test_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set once, by the one test evaluation (HPO-1.4)."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudyTrial(Base):
    """One trial of a study, as SCR-10 shows it (HPO-1.3, HPO-1.5)."""

    __tablename__ = "study_trials"
    __table_args__ = (
        CheckConstraint(_one_of("state", TRIAL_STATES), name="ck_study_trials_state"),
    )

    study_id: Mapped[str] = mapped_column(Text, ForeignKey("studies.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(Text, ForeignKey("jobs.id"))
    """A deep-learning trial's own job; null for a classical trial."""
    state: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    value: Mapped[float | None] = mapped_column(Float)
    step: Mapped[int | None] = mapped_column(Integer)
    """The last step reported: the fold (classical) or epoch (deep learning)."""
    prune_reason: Mapped[str | None] = mapped_column(Text)
    """``median`` or ``budget`` for a pruned trial."""
    code: Mapped[str | None] = mapped_column(Text)
    """A failed trial's code: ``invalid_combination``, ``over_vram``, ``interrupted``, …"""
    intermediate: Mapped[list[list[float]]] = mapped_column(JSONB, server_default="[]")
    """``[[step, value], …]``: what the pruner compared."""
    duration_s: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Checkpoint(Base):
    """A run's in-run checkpoint: one per job, the latest only (RUN-1.3, REG-2.6)."""

    __tablename__ = "checkpoints"

    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id", name="fk_checkpoints_job"), primary_key=True
    )
    lineage_id: Mapped[str | None] = mapped_column(Text, ForeignKey("model_lineages.id"))
    """Null for an HPO trial whose lineage does not exist yet (ADR-0018 rule 7)."""
    path: Mapped[str] = mapped_column(Text)
    """Relative to the data folder: the checkpoint's JSON file."""
    bytes: Mapped[int] = mapped_column(BigInteger)
    global_step: Mapped[int] = mapped_column(BigInteger)
    epoch: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Pin(Base):
    """A version exempt from retention until unpinned (REG-2.5)."""

    __tablename__ = "pins"

    version_id: Mapped[str] = mapped_column(Text, ForeignKey("model_versions.id"), primary_key=True)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetentionSetting(Base):
    """``keep_intermediate``, globally (``lineage_id`` null) or for one lineage
    (REG-2.3, REG-2.4, OC-9). Null ``keep_intermediate`` means "all"."""

    __tablename__ = "retention_settings"
    __table_args__ = (
        CheckConstraint(
            "keep_intermediate IS NULL OR keep_intermediate >= 0",
            name="ck_retention_settings_keep",
        ),
        Index(
            "uq_retention_settings_scope",
            "lineage_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    lineage_id: Mapped[str | None] = mapped_column(Text, ForeignKey("model_lineages.id"))
    keep_intermediate: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettingsSection(Base):
    """One section of the settings edited on SCR-14 (OC-9): its values as JSON. Retention
    has its own table (``retention_settings``), keyed by lineage."""

    __tablename__ = "settings"

    section: Mapped[str] = mapped_column(Text, primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelTemplate(Base):
    """A saved model spec (BUILD-1.7, ADR-0017 rule 9): a graph in canonical form, for one
    dataset type. Built-in templates are written by the code and cannot be changed."""

    __tablename__ = "model_templates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    structure_hash: Mapped[str] = mapped_column(Text)
    dataset_type: Mapped[str] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# -- chat (M12, ADR-0021 rule 9, DATA_MODEL §4.16) ------------------------------------

CHAT_ROLES = ("user", "assistant")
CHAT_MODES = ("llm", "rag", "images")
REQUEST_STATUSES = ("ok", "refused", "failed", "stopped")


class ChatSession(Base):
    """A conversation on SCR-12 (open point 8a): kept and listed until the owner deletes it."""

    __tablename__ = "chat_sessions"
    __table_args__ = (Index("ix_chat_sessions_updated", text("updated_at DESC")),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InferenceRequest(Base):
    """One chat, RAG or image-search request with its route and timings (NFR-6). Kept when
    its conversation is deleted: the timings are the NFR-6 record."""

    __tablename__ = "inference_requests"
    __table_args__ = (
        CheckConstraint(_one_of("kind", CHAT_MODES), name="ck_inference_requests_kind"),
        CheckConstraint(_one_of("status", REQUEST_STATUSES), name="ck_inference_requests_status"),
        Index("ix_inference_requests_created", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )
    route: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[str | None] = mapped_column(Text)
    device: Mapped[str | None] = mapped_column(Text)
    index_version_id: Mapped[str | None] = mapped_column(Text)
    cold_start: Mapped[bool] = mapped_column(Boolean, server_default="false")
    load_ms: Mapped[float | None] = mapped_column(Float)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    total_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_per_s: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    """A message of a conversation. An answer keeps its citations (RAG), and in ``details``
    its image results, an abstention, a refusal, or that it was stopped."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(_one_of("role", CHAT_ROLES), name="ck_chat_messages_role"),
        CheckConstraint(_one_of("mode", CHAT_MODES), name="ck_chat_messages_mode"),
        Index("ix_chat_messages_session", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any] | None] = mapped_column(JSONB)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("inference_requests.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
