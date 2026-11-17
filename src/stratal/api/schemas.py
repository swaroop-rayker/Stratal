"""Response models for the OpenAPI document (SPEC §7.3, UI-2.1).

The routes build plain dicts; these models only describe them, so the frontend's
types are generated from the backend (openapi-typescript) and cannot drift. FastAPI
validates each response against its model, so a dict that strays from it fails
loudly in the tests instead of reaching the UI. Nested documents that the UI shows
as they are (configs, metrics, report bodies) stay ``dict[str, Any]``.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Json = dict[str, Any]
JobState = Literal["queued", "running", "paused", "completed", "failed", "cancelled", "stalled"]
StageStatus = Literal["pending", "queued", "running", "completed", "failed", "skipped", "cancelled"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobError(BaseModel):
    """``{code, reason, suggested_action, detail}`` (DATA_MODEL.md §4.3)."""

    model_config = ConfigDict(extra="allow")

    code: str
    reason: str | None = None
    suggested_action: str | None = None
    detail: str | None = None


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
    attempt: int
    dataset: str | None
    current_stage: str | None
    stages: list[JobStage]
    output_version_id: str | None
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
