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
    "pending",
    "queued",
    "running",
    "paused",
    "waiting",
    "completed",
    "failed",
    "skipped",
    "cancelled",
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


class JobStudy(_Model):
    id: str
    trial: int | None = None
    study_job: str | None = None


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
    study: JobStudy | None = None
    """For an HPO study's job or one of its trials (M10): the study, and the trial's number."""
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
    caused_by: str | None = None
    """The ``event_uid`` of the alert that caused this event (MON-2.7, ADR-0020)."""


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
    semantic_cluster_id: str | None = None
    """M11 (DATA-2.3): the semantic cluster an accepted row is in."""


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
    cpu_cores: float | None = None
    """The job's process tree's CPU, in cores (MON-3.2)."""


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


class FoundedFrom(_Model):
    """The version a lineage's first fine-tune started from, in another lineage (RUN-1.10)."""

    version_id: str
    lineage_id: str
    lineage_name: str
    reset_head: bool


class ModelLineageDetail(ModelLineage):
    versions: list[ModelVersion]
    retention: LineageRetention
    founded_from: FoundedFrom | None = None
    """Set when the lineage was founded by fine-tuning a version of another lineage."""


class ModelVersionDetail(ModelVersion):
    lineage_name: str
    config: Json
    metrics: Json | None
    history: list[Json]
    """One entry per finished epoch: train and val loss and accuracy."""
    weights_path: str | None
    weights_deleted_at: str | None
    study_id: str | None = None
    """The HPO study whose best trial this version is (M10, HPO-1.4), else null."""
    children: list["VersionChild"] = []
    """Versions started from this one (RUN-1.10), in any lineage, newest first."""


class VersionChild(_Model):
    id: str
    lineage_id: str
    lineage_name: str
    weights_status: WeightsStatus


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
    name: Literal["retention", "training", "monitoring"]
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
    input: "TextInput | None" = None
    """For a text: how it was read (M11, REG-1.4, SCR-11 v2 question 2)."""
    load_ms: float | None
    """Time to load the model, when this request loaded it."""
    total_ms: float


class TextInput(_Model):
    tokens: int
    """Words and punctuation marks the text was split into, before cutting."""
    unknown: int
    """Tokens not in the version's vocabulary, read as the unknown token."""
    truncated_at: int | None
    """L, when the text had more tokens and was cut there; null when it was not cut."""


# ------------------------------------------------------------------ comparison (M11, EVAL-1.3)


class CompareSplit(_Model):
    version_id: str
    dataset: str
    dataset_type: str | None
    test_rows: int | None
    task: str | None
    classes: list[str]


class CompareCandidate(_Model):
    """A trained version on the split: compared if it has test metrics; scored once if not."""

    id: str
    lineage_id: str
    lineage_name: str
    test_metric: str | None
    test_value: float | None
    weights_status: WeightsStatus
    trained_at: str | None
    study_id: str | None
    scoring_job_id: str | None
    """A queued or running Score on test job for it."""
    can_score: bool
    why_not: str | None
    """Why it has no test metrics and cannot be scored, in words."""


class CompareCandidates(_Model):
    split: CompareSplit
    items: list[CompareCandidate]


class CompareVersion(_Model):
    id: str
    lineage_id: str
    lineage_name: str
    kind: Literal["network", "classical"]
    model: str
    parameters: int | None
    trained_at: str | None
    job_id: str | None
    weights_status: WeightsStatus
    parent_version_id: str | None
    parent_is_base: bool
    study_id: str | None
    metrics: dict[str, float]
    """The task's test metrics (EVAL-1.2): accuracy and macro_f1, or mse, rmse, mae and r2."""
    correct: int | None
    """Classification: test rows right, accuracy × rows."""
    accuracy_low: float | None
    accuracy_high: float | None
    """Accuracy's Wilson 95 % interval (EVAL-1.1's function)."""
    per_class_f1: dict[str, float]


class Comparison(_Model):
    """Two to six versions on one test split, side by side (EVAL-1.3)."""

    split: CompareSplit
    versions: list[CompareVersion]
    best: dict[str, list[str]]
    """Per metric, the versions with the best value (ties share it); empty when all tie."""
    overlap: bool
    """True when any two accuracy intervals overlap: the split cannot tell them apart."""


# ------------------------------------------------------------------ studies (M10, HPO-1.5)


class StudyCounts(_Model):
    started: int
    complete: int
    pruned: int
    failed: int
    running: int
    interrupted: int


class StudyBest(_Model):
    trial: int
    value: float | None
    params: dict[str, Any]


class Study(_Model):
    """An HPO study (API.md §3.12): what SCR-10's header, best card, importance and test
    evaluation read."""

    id: str
    job_id: str
    kind: Literal["classical", "deep"]
    dataset_version: str
    objective: str
    direction: Literal["maximize", "minimize"]
    limits: dict[str, int]
    pruning: bool
    cv_folds: int | None
    seed: int
    space: list[dict[str, Any]]
    base: dict[str, Any]
    state: Literal["running", "closed", "completed", "cancelled", "failed"]
    ended_by: Literal["n_trials", "timeout"] | None
    elapsed_s: float
    trials: StudyCounts
    best: StudyBest | None
    importance: dict[str, Any] | None
    best_version_id: str | None
    test_metrics: dict[str, Any] | None
    test_evaluated_at: str | None
    created_at: str | None
    finished_at: str | None


class StudyList(_Model):
    items: list[Study]


class StudyTrial(_Model):
    """One trial (HPO-1.3): its state in words, value, the step it reached and why it was
    pruned or failed, each epoch's or fold's value."""

    number: int
    state: Literal["running", "complete", "pruned", "failed"]
    params: dict[str, Any]
    value: float | None
    step: int | None
    prune_reason: Literal["median", "budget"] | None
    code: str | None
    intermediate: list[list[float]]
    duration_s: float | None
    job_id: str | None
    started_at: str | None
    finished_at: str | None


class StudyTrialList(_Model):
    items: list[StudyTrial]


ModelVersionDetail.model_rebuild()
Prediction.model_rebuild()


# -- system monitor (SCR-13; MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4; ADR-0020) -------


class RangePoint(_Model):
    """One time bucket's mean of the machine samples (SCR-13's lanes)."""

    ts: str
    minute_average: bool
    """From the one-minute averages kept beyond the raw retention (NFR-12)."""
    cpu_percent: float | None
    ram_used_mb: float | None
    ram_total_mb: float | None
    gpu_util_percent: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    gpu_temp_c: float | None
    disk_read_mb_s: float | None


class RangeStage(_Model):
    name: str
    status: str
    started_at: str | None
    finished_at: str | None


class RangeJob(_Model):
    job_id: str
    kind: str
    state: str
    started_at: str | None
    finished_at: str | None
    stages: list[RangeStage]


class TelemetryRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    bucket_s: float
    points: list[RangePoint]
    jobs: list[RangeJob]
    """Jobs that ran in the range, with their stages (the jobs strip)."""


class AlertThresholds(_Model):
    vram_percent: float
    ram_percent: float
    pause_c: float
    resume_c: float
    stall_s: float


class SystemAlert(_Model):
    code: str
    """``vram_high``, ``ram_high``, ``gpu_hot`` or ``no_progress``."""
    firing: bool
    fired: Event
    cleared: Event | None
    actions: list[Event]
    """Events that record this alert in ``caused_by``: a pause, a resume, ``stalled``, a
    linked out-of-memory failure."""
    lasted_s: float | None


class SystemAlerts(_Model):
    items: list[SystemAlert]
    thresholds: AlertThresholds


class DiagnosticObserved(_Model):
    samples: int
    gpu_percent: float | None
    cpu_cores: float | None
    """The job's process tree, in cores (OC-12)."""
    capacity_cores: float
    """``1 + loader_workers``, at most the logical CPUs."""
    loader_workers: int
    disk_read_mb_s: float


class DiagnosticThresholds(_Model):
    gpu_low_percent: float
    cpu_saturated_share: float
    disk_mb_s: float


class DiagnosisText(_Model):
    code: str
    message: str
    reason: str
    suggested_action: str


class DiagnosticLive(_Model):
    job_id: str
    stage: str
    queue: Literal["cpu", "gpu"]
    expected: list[str]
    """MON-3's main resource: ``gpu``, ``cpu``, ``disk``, ``ram``."""
    running_for_s: float
    observed: DiagnosticObserved
    thresholds: DiagnosticThresholds
    diagnosis: DiagnosisText | None


class DiagnosticPast(_Model):
    fired: Event
    cleared: Event | None
    lasted_s: float | None


class SystemDiagnostics(_Model):
    live: list[DiagnosticLive]
    logical_cpus: int
    items: list[DiagnosticPast]


class OverlayMachinePoint(_Model):
    x: float
    """Seconds spent running, or the epoch reached (``align``)."""
    gpu_util_percent: float | None
    vram_used_mb: float | None
    gpu_temp_c: float | None
    cpu_percent: float | None
    ram_used_mb: float | None
    minute_average: bool


class OverlayProcessPoint(_Model):
    x: float
    rss_mb: float | None
    torch_allocated_mb: float | None
    cpu_cores: float | None


class OverlaySummary(_Model):
    seconds_per_epoch: float | None
    gpu_mean_percent: float | None
    vram_peak_mb: float | None
    torch_allocated_peak_mb: float | None
    rss_mean_mb: float | None
    cpu_cores_mean: float | None
    gpu_temp_max_c: float | None


class OverlaySide(_Model):
    job_id: str
    kind: str
    state: str
    label: str
    started_at: str | None
    running_s: float
    epochs: int | None
    machine: list[OverlayMachinePoint]
    process: list[OverlayProcessPoint]
    summary: OverlaySummary


class JobOverlay(_Model):
    align: Literal["time", "epoch"]
    common_epochs: int | None
    epoch_unavailable: str | None
    """Why the two cannot be aligned by epoch, when they cannot."""
    a: OverlaySide
    b: OverlaySide


# -- SCR-03 v2: distributions, diff, models trained on a version, clusters (M11) -----------


class FiveNumbers(_Model):
    n: int
    min: float
    p10: float
    median: float
    p90: float
    max: float
    mean: float


class HistBin(_Model):
    low: float
    high: float
    raw: int
    here: int


class ColumnProfile(_Model):
    key: str
    label: str
    unit: str
    raw_label: str
    """``raw`` (every row) or ``with a value`` (a column a later stage adds)."""
    raw: FiveNumbers | None
    here: FiveNumbers | None
    removed: FiveNumbers | None
    bins: list[HistBin] | None
    """Shared bins; null when every row has one value."""


class RemovedReason(_Model):
    reason_code: str
    status: str
    rows: int


class FunnelStep(_Model):
    stage: str
    version_id: str
    rows_in: int
    removed: list[RemovedReason]
    rows_out: int
    parts: dict[str, int] | None


class ClassCounts(_Model):
    label: str
    train: int
    val: int
    test: int
    rows: int


class TokenCut(_Model):
    length: int
    cut: int
    rows: int


class Distributions(_Model):
    version_id: str
    stage: str
    dtype: str | None
    row_count: int
    accepted: int
    removed: int
    funnel: list[FunnelStep]
    columns: list[ColumnProfile]
    classes: list[ClassCounts] | None
    formats: dict[str, int]
    markers: dict[str, Any]
    """Thresholds to draw on a column, e.g. the filter's ``clip_score``."""
    token_cut: TokenCut | None


class VersionRef(_Model):
    version_id: str
    stage: str
    created_at: str | None


class ConfigChange(_Model):
    stage: str
    field: str | None
    """Null when the stage is in one lineage only."""
    a: Any
    b: Any


class DiffField(_Model):
    field: str
    a: Any
    b: Any


class DiffRow(_Model):
    row_key: str
    fields: list[DiffField]


class WordingRow(_Model):
    row_key: str
    a: str | None
    b: str | None


class VersionDiff(_Model):
    a: VersionRef
    b: VersionRef
    stages_differ: bool
    config_diff: list[ConfigChange]
    added: int
    removed: int
    changed: int
    wording_only: int
    same: int
    by_field: dict[str, int]
    only_in_a: list[str]
    only_in_b: list[str]
    changed_rows: list[DiffRow]
    wording_rows: list[WordingRow]
    shown_limit: int


class ModelMetric(_Model):
    split: Literal["test", "val"]
    name: str
    value: float


class TrainedModel(_Model):
    id: str
    lineage_id: str
    lineage_name: str
    state: str
    kind: Literal["run", "fine-tune", "study", "classical"]
    parent_version_id: str | None
    metric: ModelMetric | None
    weights_status: str
    trained_at: str | None
    study_id: str | None


class VersionModels(_Model):
    items: list[TrainedModel]


class ClusterPair(_Model):
    a: str
    b: str
    similarity: float | None
    phash_distance: int | None


class SemanticClusterView(_Model):
    cluster_id: str
    rows: list[Row]
    pairs: list[ClusterPair]
    split_groups: list[str]
