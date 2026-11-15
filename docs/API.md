# Stratal — Interfaces

| | |
|---|---|
| Status | Built: health (M0); jobs, events, telemetry, live streams and host endpoints (M2, 2026-11-14); the clean and preprocess stages and `from_version` (M3, 2026-11-15). Datasets, versions, review, evaluation, configuration and `GET /system` are still planned |
| Detail level | Phase-1 HTTP interface and the internal contracts for owner-implemented logic. Later phases are listed in [§7](#7-later-phases) |
| Authority | This file states the intended contract. Once code exists, the generated OpenAPI document at `/api/v1/openapi.json` gives field-level detail. If the two disagree, stop and ask |

## 1. Conventions

- **Base path:** `/api/v1`. No authentication (single user, local only; SPEC §5).
- **Bodies:** JSON, UTF-8. Field names are `snake_case`.
- **Times:** RFC 3339 in UTC, e.g. `2026-11-20T14:03:11.204Z`.
- **Lists:** cursor pagination with `?limit=` (default 50, maximum 500) and `?cursor=`. Responses have the form `{"items": [...], "next_cursor": "..." | null}`.
- **Long work:** anything that can take more than a moment returns `202 Accepted` with a job, and progress is followed through the job endpoints.
- **Frontend types:** generated from OpenAPI with openapi-typescript (SPEC §7.3), so they cannot drift from the backend.

## 2. Errors

Every error response uses RFC 9457 problem details with media type `application/problem+json`, plus a stable `code` ([ADR-0011](decisions/ADR-0011-error-model.md)).

```json
{
  "type": "https://stratal.local/errors/invalid_state_transition",
  "title": "Invalid state transition",
  "status": 409,
  "code": "invalid_state_transition",
  "detail": "Job job_0142 is completed and cannot be cancelled.",
  "suggested_action": "Start a new run instead.",
  "request_id": "req_01JCX5M2",
  "errors": []
}
```

`errors` lists field-level problems for validation failures, each as `{"loc": ["stages", "ingest", "min_size"], "msg": "must be at least 1", "code": "greater_than_equal"}`.

| Code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | The request body or a config value failed validation |
| `not_found` | 404 | No such dataset, version, job, review or report |
| `invalid_state_transition` | 409 | The requested action is not allowed in the job's current state |
| `conflict` | 409 | The resource already exists, e.g. a dataset slug |
| `source_not_found` | 422 | The source folder does not exist or is not readable from the workers |
| `dependency_unavailable` | 503 | Postgres, Redis or the data volume is unreachable |
| `internal_error` | 500 | Anything unexpected. The detail carries the request ID, not a stack trace |

Jobs that fail carry the same idea in `job.error`. These codes appear there and in ERROR events, never as HTTP statuses:

| Code | Meaning | Suggested action given |
|---|---|---|
| `gpu_oom` | PyTorch ran out of VRAM | A concrete smaller batch size |
| `host_oom_killed` | The kernel killed the job for exceeding its memory limit | Fewer loader workers or a smaller batch, with the peak RSS reached |
| `worker_lost` | The supervisor disappeared | Retry; finished work is kept |
| `cuda_unavailable` | The GPU cannot be reached, e.g. after sleep | Restart WSL, then retry |
| `interrupted` | The job was cut short and will resume | None needed |
| `stage_failed` | The stage's own logic raised | Depends on the stage |

## 3. Endpoints (phase 1)

### 3.1 System

| Method and path | Purpose | Success |
|---|---|---|
| `GET /health` | Liveness and dependency checks | `200 {"status": "ok", "checks": {"postgres": "ok", "redis": "ok", "data_volume": "ok"}, "version": "…"}`; `503` problem if a check fails |
| `GET /system` | Hardware and limits as Stratal sees them: GPU name, VRAM total, RAM total (the VM's), memory limits, VRAM cap | `200` |

### 3.2 Datasets and versions

| Method and path | Purpose | Success |
|---|---|---|
| `POST /datasets` | Create a dataset. Body: `{slug, display_name?, modality}` | `201` Dataset |
| `GET /datasets` | List datasets with their latest version | `200` list |
| `GET /datasets/{slug}` | One dataset | `200` Dataset |
| `GET /datasets/{slug}/versions` | Versions, newest first | `200` list of Version |
| `GET /versions/{version_id}` | One version: stage, parent, config, counts, bytes, creating job | `200` Version |
| `GET /versions/{version_id}/lineage` | Ancestors back to raw, and descendants (DATA-3.3) | `200 {"ancestors": [...], "descendants": [...]}` |
| `GET /versions/{version_id}/rows` | Page through manifest rows. Filters: `status`, `reason_code`, `split`, `dup_group_id` (the members of one duplicate group, for SCR-03's row drawer; accepted 2026-11-15), `q` (row-key substring) | `200` list of Row |
| `GET /versions/{version_id}/stats` | Counts by status, reason and deciding stage; size and CLIP-score distributions | `200` Stats |
| `GET /blobs/{sha256}` | The file's bytes. `?w=` returns a thumbnail of that width | `200` image |
| `GET /sources` | Folders directly under the sources mount (`/sources`), each with its file count, so the Start pipeline drawer (SCR-02) can offer them. Accepted with the SCR-02 wireframe on 2026-11-15 | `200 {"items": [{"name": "coco10k", "path": "/sources/coco10k", "files": 10000}]}` |

Version (abridged):

```json
{
  "version_id": "ds_7f3a9c21b04e",
  "dataset": "coco10k",
  "stage": "label",
  "parent_version_id": "ds_1c44e09ab7d2",
  "content_hash": "9f86d081884c7d65…",
  "config_hash": "2c26b46b68ffc68f…",
  "config": {"model": "florence-community/Florence-2-base", "batch_size": 16},
  "dtype": null,
  "row_count": 10000,
  "counts": {"accepted": 9512, "rejected": 148, "duplicate": 340, "filtered": 0},
  "manifest_bytes": 1632004,
  "new_blob_bytes": 0,
  "created_by_job_id": "job_0142",
  "created_at": "2026-11-20T14:21:40.118Z"
}
```

### 3.3 Jobs

| Method and path | Purpose | Success |
|---|---|---|
| `POST /jobs` | Start a job. Body is a union on `kind`; phase 1 accepts `pipeline` | `202` Job |
| `GET /jobs` | List. Filters: `state`, `kind`. Planned additions for SCR-07 (accepted 2026-11-15): `state` as a comma-separated list (`running,queued`), a `dataset` filter, and `counts` (jobs per state under the other filters) beside `items` | `200` list of Job |
| `GET /jobs/{job_id}` | One job with its stages | `200` Job |
| `POST /jobs/{job_id}/cancel` | Request cancellation | `202` Job; `409` if already finished |
| `POST /jobs/{job_id}/retry` | Requeue a failed job; finished stages are reused | `202` Job; `409` if not failed |
| `GET /jobs/{job_id}/events` | Page through events. Filters: `level`, `stage`, `after` (event ID) | `200` list of Event |
| `GET /jobs/{job_id}/telemetry` | Samples for charts. Params: `from`, `to`, `stage`, `max_points` | `200 {"resource": [...], "process": [...]}` |
| `GET /jobs/{job_id}/stream` | Live feed for one job (SSE, [§4](#4-live-streams)) | `200 text/event-stream` |

Pause and resume (`POST /jobs/{job_id}/pause`, `/resume`) arrive in phase 2 with RUN-1.2.

Pipeline request:

```json
{
  "kind": "pipeline",
  "dataset": "coco10k",
  "source": {"kind": "folder", "path": "/sources/coco10k"},
  "stages": {
    "ingest": {"min_size": 256},
    "label": {"batch_size": 16}
  },
  "until_stage": null
}
```

Every key under `stages` is optional and overrides the stage's defaults. As of M3 the stages are `ingest`, `clean` and `preprocess`; a key that names any other stage is rejected with `validation_error`.

Give exactly one of `source` and `from_version`:

- **`source`**: start from a folder. The pipeline begins with `ingest`, and `dataset` is required.
- **`"from_version": "ds_…"`**: continue from an existing version. The stages after the one that made it run, and its output is the first stage's input. `dataset` may be left out; if given it must be the version's dataset. A version made by the last available stage has nothing after it and is rejected. An unknown version is a `validation_error` with `loc: ["from_version"]`.

With either, a key under `stages` or an `until_stage` that names a stage the request would not run is rejected. A stage whose input and config match an existing version reuses it (`cache_hit: true`) instead of running again.

Until the owner's DATA-2.1 and DATA-2.2 functions exist, the `clean` stage fails with `stage_failed` and a reason naming them; `"until_stage": "ingest"` runs ingestion alone.

Job (abridged):

```json
{
  "id": "job_0142",
  "kind": "pipeline",
  "state": "running",
  "desired_state": null,
  "attempt": 1,
  "dataset": "coco10k",
  "current_stage": "label",
  "stages": [
    {"idx": 0, "name": "ingest", "queue": "cpu", "status": "completed",
     "output_version_id": "ds_0b1f9e77a3c4", "cache_hit": false,
     "started_at": "2026-11-20T13:57:01Z", "finished_at": "2026-11-20T13:58:13Z",
     "peaks": {"ram_used_mb": 3120, "vram_used_mb": 0, "gpu_temp_c": 41}},
    {"idx": 3, "name": "label", "queue": "gpu", "status": "running",
     "step": 42, "total": 313, "throughput": 2.4, "eta_seconds": 872,
     "activity": "Captioning batch 42/313 · Florence-2-base · bs=16 · fp16"}
  ],
  "error": null,
  "created_at": "2026-11-20T13:57:00Z",
  "started_at": "2026-11-20T13:57:01Z",
  "finished_at": null
}
```

### 3.4 Activity log and telemetry

| Method and path | Purpose | Success |
|---|---|---|
| `GET /events` | Events across all jobs (MON-4.3). Filters: `level`, `stage`, `job_id`, `after` | `200` list of Event |
| `GET /telemetry/latest` | The most recent resource sample | `200` Sample; `204` before the first sample exists |
| `GET /stream` | Global live feed: job state changes, alerts, idle telemetry (SSE) | `200 text/event-stream` |

Event: the shape in SPEC MON-1, plus `id`, `event_uid`, `type`, `reason`, `suggested_action` and `attempt`. Progress events carry `items_per_s` and `eta_s` in `metrics` (MON-1.5). Event lists are oldest first; `next_cursor` is the last event ID of a full page.

### 3.5 Review (LABEL-1.5)

| Method and path | Purpose | Success |
|---|---|---|
| `POST /versions/{version_id}/reviews` | Draw a sample. Body: `{sample_size, seed?}` | `201` ReviewSession with its items |
| `GET /reviews/{review_id}` | Session, items and progress | `200` |
| `PUT /reviews/{review_id}/items/{position}` | Record a decision. Body: `{decision: "accept" | "reject", note?}` | `200` item |
| `POST /reviews/{review_id}/close` | Close the session and return the acceptance summary | `200` |

### 3.6 Evaluation (EVAL-1.1)

| Method and path | Purpose | Success |
|---|---|---|
| `POST /versions/{version_id}/reports` | Build a report. Body: `{kind: "phase1_dataset", review_id?}` | `202` Job |
| `GET /versions/{version_id}/reports/latest` | The newest report of a kind (`?kind=`) | `200` Report |
| `GET /reports/{report_id}` | One report | `200` Report |

### 3.7 Configuration

| Method and path | Purpose | Success |
|---|---|---|
| `GET /config/schemas` | Names of the config models | `200` list |
| `GET /config/schemas/{name}` | JSON Schema for one config model, for generated forms | `200` JSON Schema |
| `GET /config/defaults` | Current defaults | `200` |

### 3.8 Host script

| Method and path | Purpose | Success |
|---|---|---|
| `GET /host/keep-awake` | Should Windows be kept awake right now? Polled by the host script | `200 {"keep_awake": true, "active_jobs": ["job_0142"]}` |
| `POST /host/power-events` | Report a suspend or resume. Body: `{type: "suspend" | "resume", at}` | `202` |

## 4. Live streams

Server-sent events (OC-2, accepted by the owner on 2026-11-14). Each message carries an `id` so a client that reconnects with `Last-Event-ID` receives what it missed, replayed from Postgres, before the stream switches to live. `?after=<event id>` does the same for clients that cannot set the header. A job stream opened without a cursor starts with the job's whole history and a `job` snapshot; the global stream starts at "now".

```text
id: 18342
event: event
data: {"id":18342,"ts":"2026-11-20T14:03:11.204Z","job_id":"job_0142","stage":"label","level":"INFO","step":42,"total":313,"message":"Captioning batch 42/313 · Florence-2-base · bs=16 · fp16","metrics":{"img_per_s":2.4,"batch_ms":6650}}

event: sample
data: {"ts":"2026-11-20T14:03:12Z","cpu_percent":38.0,"ram_used_mb":7372,"gpu_util_percent":94,"vram_used_mb":3994,"gpu_temp_c":71,"gpu_power_w":48,"torch_allocated_mb":3174,"torch_reserved_mb":3686}
```

| SSE event name | Payload | On which stream |
|---|---|---|
| `event` | An Event | job, global |
| `job` | A Job snapshot, sent whenever state or stage status changes | job, global |
| `sample` | One merged resource + process sample | job; global when idle |
| comment line `: ping` | Keep-alive every 15 seconds | both |

## 5. Internal contracts for owner-implemented logic

These are the seams between the agent's plumbing and the owner's core logic. Each is a plain function or small class with no dependency on Celery, Redis or FastAPI. Exact signatures are fixed when the stubs are written in each milestone, and this table is updated in the same change.

| Unit | Requirement | Contract in one line | Milestone |
|---|---|---|---|
| Version ID derivation | DATA-3.2 | Manifest rows + resolved config → content hash, config hash, version ID, per [DATA_MODEL.md §3](DATA_MODEL.md#3-identifiers) | M1 |
| Exact-duplicate grouping | DATA-2.1 | `group_exact_duplicates(items: Sequence[ExactItem]) -> list[DuplicateGroup]`: rows with content hashes → groups of identical files, one deterministic keeper each | M3, stub delivered |
| Perceptual hash and near-duplicate grouping | DATA-2.2 | `perceptual_hash(image, *, hash_size) -> str` (lowercase hex of the hash's bits); `group_near_duplicates(items: Sequence[HashedItem], *, max_distance) -> list[DuplicateGroup]` by Hamming distance | M3, stub delivered |
| Caption batching loop | LABEL-1.1, LABEL-1.2 | Uncached items + captioner → cached captions, committed per batch, surviving OOM by shrinking the batch | M4 |
| VRAM manager | RUN-1.4, RUN-1.5 | Load one model under the cap; unload and prove VRAM returned to baseline | M4 |
| CLIP scoring | LABEL-1.3 | Image + caption → similarity score | M4 |
| Filter rule | LABEL-1.4 | Scores + threshold → rows marked `filtered` with a reason | M4 |
| Split assignment | DATA-4 | Rows + split config + seed → a split per row, per the section the owner writes | M5 |
| Report statistics | EVAL-1.1 | Manifest + review decisions → counts per stage, score distribution, filter rate, acceptance with a 95% Wilson interval | M5 |
| Semantic-duplicate clustering | DATA-2.3 (P2) | CLIP embeddings → clusters | M7 if time |

What the plumbing gives each unit: a `JobContext` (emit an event, report progress, ask whether to stop), the derivation cache (`get_many`, `put_many`), `load_version` (stream manifest batches), the blob store (open a file by hash), and a model handle from the VRAM manager.

## 6. Command line

The CLI calls the same domain functions as the workers. It prints its result as JSON on standard output and logs to standard error. Exit code 0 means success, 1 a typed error (printed as `{"error": {code, detail, suggested_action}}`), 2 an owner unit that is not implemented yet.

| Command | Does | Since |
|---|---|---|
| `python -m stratal.cli ingest --dataset NAME --source PATH [--min-size N]` | Ingests a folder as a raw version and registers it | M1 |
| `python -m stratal.cli versions --dataset NAME` | Lists a dataset's versions, newest first | M1 |
| `python -m stratal.cli lineage VERSION_ID` | Shows a version's ancestry back to raw | M1 |

Still planned for phase 1: run a pipeline, pull models.

### Internal interfaces added in M1

| Interface | Where | Shape |
|---|---|---|
| `VersionDeriver` (owner implements `derive_version_identity`) | `stratal.data.versioning` | `(rows: Iterator[Row], config: Mapping) -> VersionIdentity(content_hash, config_hash, version_id)` |
| `EventSink` | `stratal.mon.sink` | `emit(*, stage, message, level, substage, step, total, metrics)` |
| `BlobStore` | `stratal.storage.blobs` | `put_file(path) -> StoredBlob(sha256, size_bytes, created)`, `path_for`, `exists` |
| Manifest I/O | `stratal.storage.manifest` | `write_manifest(path, rows, metadata=)`, `iter_rows(path)`, `read_metadata(path)` |
| Commit step | `stratal.data.commit` | `commit_version(...) -> VersionRecord` |
| Version registry | `stratal.data.registry` | `ensure_dataset`, `register_version`, `lineage`, `list_versions` |

### Internal interfaces added in M2

| Interface | Where | Shape |
|---|---|---|
| `Event` | `stratal.mon.events` | The validated event model; a WARN or ERROR without `reason` and `suggested_action` cannot be constructed (MON-1.3). `oom_event(...)` builds the out-of-memory event with a smaller batch size |
| `EventSink.emit` | `stratal.mon.sink` | Gained optional `reason` and `suggested_action` |
| `JobContext` | `stratal.runtime.context` | An `EventSink` plus `should_stop()`, `raise_if_cancelled()`, `set_version(version_id)` and `event(type, message, ...)` |
| Stage registry | `stratal.runtime.stages` | `register(StageDef(name, queue, config_model, run))`; `run(StageRun) -> StageResult(output_version_id, cache_hit, metrics)` |
| State machine | `stratal.runtime.states` | `transition(session, job, to_state, reason=, actor=)`; `TRANSITIONS` |
| Supervisor | `stratal.runtime.supervisor` | `run_stage(job_id, idx, enqueue=) -> "completed" \| "failed" \| "cancelled" \| "not_claimed" \| "superseded"` |
| Child | `python -m stratal.runtime.child --job --stage --attempt` | Exit code 0 completed, 1 failed (error written to `job_stages.error`), 3 stopped on request |
| `Bus` | `stratal.core.bus` | Every Redis operation: stream, pub/sub, control flags, active-job registry |
| `EtaEstimator` | `stratal.mon.eta` | `update(monotonic_seconds, step, total) -> Estimate(throughput, eta_seconds) \| None` |
| Monitor | `python -m stratal.mon` | Collector, sampler and watchdog threads |
| Host script | `host\stratal-host.ps1` | Polls `/host/keep-awake`; reports suspend and resume |

### Internal interfaces added in M3

| Interface | Where | Shape |
|---|---|---|
| Owner's dedupe units (stubs) | `stratal.data.dedupe` | `ExactItem(row_key, sha256)`, `HashedItem(row_key, phash, width, height, size_bytes)`, `DuplicateGroup(keeper, members)`, the three functions above, and `ALGORITHM_VERSION` |
| Clean stage | `stratal.data.clean` | `run_clean(*, parent_manifest, parent_version_id, data_dir, config: CleanConfig, derive, sink, cache, units: DedupeUnits = OWNER_UNITS, checkpoint) -> CleanResult(record, metrics, seconds)`; raises `ContractBroken` when an owner unit breaks its contract |
| Preprocess stage | `stratal.data.preprocess` | `run_preprocess(*, parent_manifest, parent_version_id, data_dir, config: PreprocessConfig, derive, sink, cache, checkpoint) -> PreprocessResult`; `transform(path, config) -> (jpeg_bytes, width, height)`; `target_size(w, h, max_side)` |
| Derivation cache | `stratal.storage.derivations` | `DerivationCache` protocol: `get_many(kind, params_hash, input_hashes) -> {input_hash: value}`, `put_many(kind, params_hash, {input_hash: value})`; `PgDerivationCache(engine, job_id=)`, `MemoryDerivationCache()`, `params_hash(params)` |
| Blob store | `stratal.storage.blobs` | gained `put_bytes(data) -> StoredBlob` |
| Version loading | `stratal.data.registry` | `manifest_for(session, data_dir, version_id) -> (DatasetVersion, Path)`, checked against the registration (`VersionDataMissing`); `load_version(...) -> Iterator[Row]`; `find_memoized(session, data_dir, *, stage, parent_version_id, config, derive)` |
| Derived-stage frame | `stratal.runtime.stages` | `run_derived_stage(run, *, stage, config_model, body)`: resolve the input by ID, reuse a memoized version, otherwise run `body` and register; `make_clean_runner(units)` |
| Job creation | `stratal.runtime.jobs` | `create_job_from_plan(..., input_version_id=None)` |
| Test helpers | `stratal.testing` | `fakes`: `no_exact_duplicates`, `fake_perceptual_hash`, `no_near_duplicates`, `ScriptedGroups`; `fake_clean` (registers clean with fakes when named in `STRATAL_EXTRA_STAGES`); `stagekit` (stages as library calls); `synthetic` gained `exif_rotated` and `odd_modes` |

## 7. Later phases

Listed so the phase-1 design leaves room. Each is specified at its phase kickoff.

| Phase | Interfaces |
|---|---|
| 2 | `POST /jobs` with `kind: train`, `hpo_study`, `eval`; pause and resume; model specs and templates; registry (`/models`, `/models/{id}/versions`, pin, retention preview and run); `POST /models/{version}/predict` (REG-1.4); comparison views |
| 3 | Chat with token streaming over SSE; RAG corpora and indexes; router decisions; image search |
| 4 | Image generation; LoRA jobs; export and import of remote bundles |
| 5 | Translation; full chat with manual and auto mode |
