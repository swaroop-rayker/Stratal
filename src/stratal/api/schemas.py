"""Response models for the OpenAPI document (SPEC §7.3, UI-2.1).

The routes build plain dicts; these models only describe them, so the frontend's
types are generated from the backend (openapi-typescript) and cannot drift. FastAPI
validates each response against its model, so a dict that strays from it fails
loudly in the tests instead of reaching the UI. Nested documents that the UI shows
as they are (configs, metrics, report bodies) stay ``dict[str, Any]``.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Json = dict[str, Any]
JobState = Literal["queued", "running", "paused", "completed", "failed", "cancelled", "stalled"]
StageStatus = Literal[
    "pending", "queued", "running", "paused", "completed", "failed", "skipped", "cancelled"
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobError(BaseModel):
    """``{code, reason, suggested_action, detail}`` (DATA_MODEL.md §4.3)."""

    model_config = ConfigDict(extra="allow")

    code: str
    reason: str | None = None
    suggested_action: str | None = None
    detail: str | Json | None = None
    """Text, or facts about the failure, e.g. ``{"signal": "SIGKILL"}`` for a killed child."""


class JobStage(_Model):
    idx: int
    name: str
    queue: Literal["cpu", "gpu"]
    status: StageStatus
    config: Json
    input_version_id: str | None
    output_version_id: str | None
    cache_hit: bool
    step: int | None
    total: int | None
    throughput: float | None
    eta_seconds: float | None
    activity: str | None
    last_progress_at: str | None = None
    """The stage's last progress or activity (RUN-1.7)."""
    metrics: Json | None
    peaks: Json | None
    started_at: str | None
    finished_at: str | None
    error: JobError | None


class Job(_Model):
    id: str
    kind: str
    state: JobState
    desired_state: Literal["paused", "cancelled"] | None
    pause_reason: Literal["user", "thermal"] | None = None
    """Why a paused job is paused (RUN-1.6); null when it is not paused."""
    heartbeat_at: str | None = None
    """The supervisor's last heartbeat: shows a stalled job's worker is alive (RUN-1.7)."""
    attempt: int
    dataset: str | None
    current_stage: str | None
    stages: list[JobStage]
    output_version_id: str | None
    model_version_id: str | None = None
    """A training job's registry version, partial or trained (REG-1.1); null otherwise."""
    model_lineage_id: str | None = None
    """That version's lineage, for a link to SCR-11."""
    error: JobError | None
    summary: Json | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class JobList(_Model):
    items: list[Job]
    next_cursor: str | None
    counts: dict[str, int]


class Event(_Model):
    id: int
    event_uid: str
    ts: str
    job_id: str | None
    stage: str | None
    substage: str | None
    version_id: str | None
    type: str
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    step: int | None
    total: int | None
    message: str
    metrics: Json | None
    reason: str | None
    suggested_action: str | None
    attempt: int | None


class EventList(_Model):
    items: list[Event]
    next_cursor: str | None


class ResourceSample(_Model):
    """One row of ``resource_samples`` (DATA_MODEL.md §4.7). GPU values are null when
    unreadable, never zero."""

    id: int
    ts: str
    job_id: str | None
    stage: str | None
    active_jobs: list[Any] | None
    interval_s: float
    cpu_percent: float
    cpu_per_core: list[float]
    ram_used_mb: float
    ram_available_mb: float
    ram_total_mb: float
    swap_used_mb: float
    gpu_util_percent: float | None
    gpu_mem_util_percent: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    gpu_temp_c: float | None
    gpu_power_w: float | None
    disk_read_mb_s: float
    disk_write_mb_s: float


class StreamSample(BaseModel):
    """The ``sample`` message of a live stream: a resource sample plus, during a job, the
    job process's own memory view (API.md §4)."""

    model_config = ConfigDict(extra="allow")

    ts: str
    interval_s: float | None = None
    job_id: str | None = None
    stage: str | None = None
    cpu_percent: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    gpu_util_percent: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    gpu_temp_c: float | None = None
    gpu_power_w: float | None = None
    rss_mb: float | None = None
    torch_allocated_mb: float | None = None
    torch_reserved_mb: float | None = None
    torch_max_allocated_mb: float | None = None


class StreamMessages(_Model):
    """Not a response body: the payload of each SSE message name, so the frontend gets
    their types. ``event`` carries an Event, ``job`` a Job, ``sample`` a StreamSample."""

    event: Event
    job: Job
    sample: StreamSample


class System(_Model):
    gpu_name: str | None
    gpu_error: str | None
    vram_total_mb: float | None
    vram_cap_fraction: float
    vram_cap_mb: float | None
    ram_total_mb: float | None
    cpu_count: int | None
    gpu_slowdown_c: float | None = None
    """The card slows itself down at this temperature (NVML); the most thermal_pause_c may be."""
    thermal_pause_c: float
    """GPU jobs pause at this temperature (RUN-1.6), as saved on SCR-14."""
    thermal_resume_c: float
    """Jobs paused for heat resume below this temperature."""
    stall_timeout_s: float
    """A silent stage is marked stalled after this long (RUN-1.7)."""


class Version(_Model):
    """A dataset version (API.md §3.2). ``counts`` holds rows by status and by reason, and
    the stage's own reports (``validation``, ``split``)."""

    version_id: str
    dataset: str
    stage: str
    parent_version_id: str | None
    content_hash: str
    config_hash: str
    config: Json
    dtype: str | None
    row_count: int
    counts: Json
    manifest_path: str
    manifest_bytes: int
    new_blob_bytes: int
    schema_version: int
    code_version: str | None
    created_by_job_id: str | None
    created_by_job_state: JobState | None
    created_at: str


class VersionList(_Model):
    items: list[Version]
    next_cursor: str | None


class Dataset(_Model):
    slug: str
    display_name: str | None
    modality: Literal["image", "tabular", "text"]
    source_uri: str | None
    created_at: str
    updated_at: str
    version_count: int
    stored_bytes: int
    latest_version: Version | None


class DatasetList(_Model):
    items: list[Dataset]
    next_cursor: str | None


class ExtendingJob(_Model):
    job_id: str
    state: JobState
    stage: str
    step: int | None
    total: int | None


class Lineage(_Model):
    version_id: str
    ancestors: list[Version]
    descendants: list[Version]
    in_progress: list[ExtendingJob]


class Row(BaseModel):
    """One manifest row (DATA_MODEL.md §5). Columns a stage has not reached are null."""

    model_config = ConfigDict(extra="allow")

    row_key: str
    sha256: str | None = None
    size_bytes: int | None = None
    format: str | None = None
    width: int | None = None
    height: int | None = None
    status: Literal["accepted", "rejected", "duplicate", "filtered"]
    decided_at_stage: str | None = None
    reason_code: str | None = None
    reason_detail: str | None = None
    phash: str | None = None
    dup_kind: str | None = None
    dup_group_id: str | None = None
    dup_of: str | None = None
    derived_sha256: str | None = None
    derived_width: int | None = None
    derived_height: int | None = None
    caption: str | None = None
    caption_model: str | None = None
    caption_model_revision: str | None = None
    clip_score: float | None = None
    clip_model: str | None = None
    clip_model_revision: str | None = None
    split: str | None = None
    split_group: str | None = None
    split_stratum: str | None = None


class RowPage(_Model):
    items: list[Row]
    matched: int
    next_cursor: str | None


class Distribution(_Model):
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    quantiles: dict[str, float]
    edges: list[float]
    histogram: list[int]
    below: int
    above: int


class ReasonCount(_Model):
    reason_code: str
    decided_at_stage: str
    status: str
    rows: int
    share: float


class Stats(_Model):
    version_id: str
    row_count: int
    by_status: dict[str, int]
    reasons: list[ReasonCount]
    by_stage: dict[str, dict[str, int]]
    by_split: dict[str, int] | None
    size_bytes: Distribution
    clip_score: Distribution | None


class Source(_Model):
    name: str
    path: str
    files: int


class SourceList(_Model):
    items: list[Source]
    next_cursor: str | None


class StageSchema(_Model):
    name: str
    queue: Literal["cpu", "gpu"]
    title: str


class StageSchemaList(_Model):
    items: list[StageSchema]
    next_cursor: str | None


class ProcessSample(_Model):
    """One row of ``process_samples`` (DATA_MODEL.md §4.8): the job's own memory view."""

    id: int
    ts: str
    job_id: str
    stage: str | None
    rss_mb: float
    torch_allocated_mb: float | None
    torch_reserved_mb: float | None
    torch_max_allocated_mb: float | None


class Telemetry(_Model):
    resource: list[ResourceSample]
    process: list[ProcessSample]


class ReviewRow(_Model):
    """The manifest facts of a sampled row (API.md §3.5)."""

    sha256: str | None = None
    derived_sha256: str | None = None
    caption: str | None = None
    clip_score: float | None = None
    split_stratum: str | None = None
    caption_model: str | None = None
    caption_model_revision: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    derived_width: int | None = None
    derived_height: int | None = None


class ReviewItem(_Model):
    position: int
    row_key: str
    split: str | None
    decision: Literal["accept", "reject"] | None
    note: str | None
    decided_at: str | None
    row: ReviewRow | None = None


class SplitProgress(_Model):
    sampled: int
    accept: int
    reject: int


class ReviewProgress(_Model):
    sampled: int
    decided: int
    accept: int
    reject: int
    undecided: int
    by_split: dict[str, SplitProgress]


class Review(_Model):
    id: str
    version_id: str
    sample_size: int
    seed: int
    status: Literal["open", "closed"]
    created_at: str
    closed_at: str | None
    progress: ReviewProgress
    items: list[ReviewItem]


class ReviewClosed(_Model):
    """The answer to closing: the session without its items, and the acceptance summary."""

    id: str
    version_id: str
    sample_size: int
    seed: int
    status: Literal["open", "closed"]
    created_at: str
    closed_at: str | None
    progress: ReviewProgress
    acceptance: Json | None
    acceptance_unavailable: str | None


class Interval(_Model):
    estimate: float | None
    low: float | None
    high: float | None
    confidence: float


class AcceptanceRate(_Model):
    decided: int
    accepted: int
    interval: Interval


class Acceptance(_Model):
    sampled: int
    undecided: int
    overall: AcceptanceRate
    by_split: dict[str, AcceptanceRate] | None


class ReportReview(_Model):
    review_id: str
    status: str
    sample_size: int
    seed: int
    decided: int
    acceptance: Acceptance | None


class ReportRows(_Model):
    total: int
    accepted: int
    by_split: dict[str, int] | None


class ReportRejections(_Model):
    total: int
    accepted: int
    by_stage: dict[str, dict[str, int]]


class ClipScores(_Model):
    edges: list[float]
    labelled: Distribution
    accepted: Distribution
    by_split: dict[str, Distribution] | None


class ReportBody(BaseModel):
    """The phase-1 report document (API.md §3.6)."""

    model_config = ConfigDict(extra="allow")

    kind: str
    version_id: str
    stage: str
    algorithm_version: int
    confidence: float
    rows: ReportRows
    rejections: ReportRejections
    filter_rate: float | None
    clip_scores: ClipScores | None
    review: ReportReview | None


class Report(_Model):
    id: int
    version_id: str
    kind: str
    review_session_id: str | None
    job_id: str | None
    created_at: str
    report: ReportBody


class ReviewSummary(_Model):
    id: str
    version_id: str
    sample_size: int
    seed: int
    status: Literal["open", "closed"]
    created_at: str
    closed_at: str | None
    progress: ReviewProgress


class ReviewList(_Model):
    items: list[ReviewSummary]
    next_cursor: str | None


# -- model registry (REG-1.1, REG-1.2; API.md §3.9) ---------------------------------

VersionState = Literal["base", "partial", "trained"]
WeightsStatus = Literal["present", "deleting", "weights_deleted"]


class VersionCounts(_Model):
    base: int
    partial: int
    trained: int


class ModelLineage(_Model):
    id: str
    name: str
    task: str
    model: Json
    dataset: str
    input_shape: list[int]
    classes: list[str]
    parameters: int
    counts: VersionCounts
    latest_version_id: str | None
    """The newest trained version, or the base before any run completes (REG-2.2)."""
    latest_state: VersionState | None
    main_metric: float | None
    """``val_accuracy`` of the latest version, until EVAL-1.2's task metrics exist."""
    weights_bytes: int
    """Weights on disk by the registry's records: base, trained and intermediate versions."""
    checkpoint_bytes: int
    """In-run checkpoints of the lineage's running and paused jobs."""
    created_at: str
    updated_at: str


class ModelLineageList(_Model):
    items: list[ModelLineage]
    next_cursor: str | None


class ModelVersion(_Model):
    id: str
    lineage_id: str
    state: VersionState
    is_latest: bool
    weights_status: WeightsStatus
    weights_bytes: int | None
    """Null for a partial version: its weights are its run's checkpoint."""
    pinned: bool
    parent_version_id: str | None
    job_id: str | None
    job_state: JobState | None
    dataset_version_id: str | None
    config_hash: str
    main_metric: float | None
    note: str | None
    created_at: str
    trained_at: str | None
    weights_deleted_by_job_id: str | None
    """The retention job that deleted the weights (REG-2.9)."""
    retention_action: Literal["keep", "delete", "blocked", "none"]
    """What the next cleanup does to it under the saved settings: ``delete`` means it
    goes then; ``blocked`` that the policy would delete it but it is in use or a parent."""
    retention_reason: str


class RetentionLastRun(_Model):
    job_id: str
    finished_at: str | None
    versions_deleted: int
    bytes_freed: int


class LineageRetention(_Model):
    keep_intermediate: int | Literal["all"]
    source: Literal["global", "lineage"]
    last_run: RetentionLastRun | None


class ModelLineageDetail(ModelLineage):
    versions: list[ModelVersion]
    retention: LineageRetention


class ModelVersionDetail(ModelVersion):
    lineage_name: str
    config: Json
    metrics: Json | None
    history: list[Json]
    """One entry per finished epoch: train and val loss and accuracy."""
    weights_path: str | None
    weights_deleted_at: str | None


# -- retention and settings (REG-2; API.md §3.9, §3.10) ---------------------------


class DiskParts(_Model):
    base: int
    latest: int
    intermediate: int
    pinned: int
    checkpoints: int


class DiskOrphan(_Model):
    path: str
    bytes: int


class DiskReport(_Model):
    lineage_id: str
    parts: DiskParts
    """Bytes measured on disk (REG-2.11)."""
    disk_bytes: int
    registry_bytes: int
    """What the registry's rows say should be on disk."""
    matches: bool
    orphans: list[DiskOrphan]
    """Files under the lineage's folder that no version accounts for."""
    missing: list[str]
    """Versions whose weights should be present but are not on disk."""


class PlanDecision(_Model):
    version_id: str
    reason: str
    weights_bytes: int
    main_metric: float | None
    created_at: str | None


class PlanCheckpoint(_Model):
    job_id: str
    bytes: int
    reason: str


class LineagePlan(_Model):
    lineage_id: str
    name: str
    keep_intermediate: int | Literal["all"]
    source: Literal["global", "lineage"]
    latest_version_id: str | None
    delete: list[PlanDecision]
    keep: list[PlanDecision]
    blocked: list[PlanDecision]
    """Versions the policy would delete that are in use or a parent (REG-2.8)."""
    checkpoints: list[PlanCheckpoint]
    bytes_to_free: int


class RetentionPlan(_Model):
    lineages: list[LineagePlan]
    versions_to_delete: int
    bytes_to_free: int
    token: str
    """Names this exact plan; a settings save is confirmed with it (REG-2.10)."""
    settings_version: str


class SettingsLineage(_Model):
    lineage_id: str
    name: str
    keep_intermediate: int | Literal["all"]
    source: Literal["global", "lineage"]
    intermediates: int
    intermediates_bytes: int


class TemperatureHigh(_Model):
    celsius: float
    job_id: str | None
    at: str | None


class TrainingContext(_Model):
    """What SCR-14 shows beside the thermal limits."""

    gpu_slowdown_c: float | None
    """The card's slowdown point (NVML): the most thermal_pause_c may be."""
    gpu_temp_high_7d: TemperatureHigh | None
    running_jobs: list[str]
    """Jobs a changed stall timeout applies to at once."""


class SettingsSection(_Model):
    name: Literal["retention", "training"]
    title: str
    requirements: list[str]
    values: Json
    schema_: Json = Field(alias="schema")
    version: str
    lineages: list[SettingsLineage] | None
    """Retention only: each lineage's keep_intermediate and current intermediates."""
    context: TrainingContext | None
    """Training only."""


class SettingsList(_Model):
    sections: list[SettingsSection]


class SettingsSaved(_Model):
    section: SettingsSection
    job_id: str | None
    """The retention job the save queued, when it deletes weights."""


# -- prediction (REG-1.4; API.md §3.9) ---------------------------------------------


class PredictedClass(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_: str = Field(alias="class")
    probability: float | None
    """Null when the model gives no probabilities (a classical SVM, ADR-0019 rule 21)."""


class Prediction(_Model):
    version_id: str
    lineage_id: str
    device: Literal["cpu", "gpu"]
    """Which device served it (ADR-0016 rule 4)."""
    fallback_reason: str | None
    """Why the CPU served it, when it did: the GPU's holder, or why inference avoids it."""
    task: Literal["classification", "regression", "clustering", "projection"] = "classification"
    """What the version predicts (M10, ADR-0019 rule 21)."""
    top: list[PredictedClass] | None = None
    """Classification: up to five classes, most probable first."""
    value: float | None = None
    """Regression: the prediction on the target's scale."""
    cluster: int | None = None
    """k-means: the record's cluster."""
    components: list[float] | None = None
    """PCA: the record's projection."""
    load_ms: float | None
    """Time to load the model, when this request loaded it."""
    total_ms: float
