# Stratal — Interfaces

| | |
|---|---|
| Status | Built: health (M0); jobs, events, telemetry, live streams and host endpoints (M2, 2026-11-14); the clean and preprocess stages and `from_version` (M3, 2026-11-15); review and evaluation (M5, 2026-11-16); datasets, versions, blobs, sources, configuration, `GET /system` and the SCR-07 job filters (M6, 2026-11-16) |
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
| `conflict` | 409 | The resource already exists, e.g. a dataset slug; or settings changed since they were read (the problem then carries `version`) |
| `confirmation_required` | 409 | The change would delete data. The problem carries `plan`; send the request again with `confirm` set to `plan.token` (REG-2.10, §3.10) |
| `source_not_found` | 422 | The source folder does not exist or is not readable from the workers |
| `dependency_unavailable` | 503 | Postgres, Redis or the data volume is unreachable |
| `model_not_allowed` | 422 | A model name that is not on the allow-list (NFR-8) |
| `model_integrity_failed` | 502 | A downloaded model file did not match its pinned size or hash; it was not kept (NFR-8) |
| `vram_insufficient` | 409 | A model load was refused: free VRAM is below its estimate (ADR-0006) |
| `gpu_busy` | 409 | Another process holds the GPU lease; the detail names it (BUG6) |
| `weights_deleted` | 409 | The version's weights were deleted by retention; re-train it (REG-2.7) |
| `weights_unreadable` | 409 | A weights or checkpoint file is not valid safetensors (a planted pickle, say); it was refused before any of it was used. Re-train the version, or cancel the run and start it again (NFR-8) |
| `model_not_pulled` | 409 | An allowed model that has not been downloaded; run time never downloads (NFR-7) |
| `internal_error` | 500 | Anything unexpected. The detail carries the request ID, not a stack trace |

A problem may carry extension members (RFC 9457 §3.2) beside the standard ones, such as `plan` or `version` above; each is named where the error is described.

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
| `GET /system` | Hardware and limits as Stratal sees them: GPU name, VRAM total, RAM total (the VM's), VRAM cap | `200` |

`GET /system` built in M6 (2026-11-16): `{gpu_name, gpu_error, vram_total_mb, vram_cap_fraction, vram_cap_mb, ram_total_mb, cpu_count}`. The API has no NVML, so the monitor describes the machine when it starts (Redis key `stratal:system`); the latest resource sample fills in the totals if it has not. Unknown values are null, never zero. The cap is the label stage's default `vram_fraction` of the card. Container memory limits are not included: the API can read only its own, and they are fixed in `compose.yaml`. M8 adds `thermal_pause_c`, `thermal_resume_c` and `stall_timeout_s`, the limits the watchdog applies (RUN-1.6, RUN-1.7; as saved on SCR-14, §3.10), so SCR-07 and SCR-04 can say when a paused job resumes, and `gpu_slowdown_c`, the card's slowdown temperature from NVML (null without a GPU).

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

Built in M6 (2026-11-16). Details:

- **Dataset:** `{slug, display_name, modality, source_uri, created_at, updated_at, version_count, stored_bytes, latest_version}`. `updated_at` is the latest version's creation time (the dataset's own without versions); `stored_bytes` sums manifest and new-file bytes over its versions (DATA-3.4). `GET /datasets` sorts by `updated_at`, newest first. `POST /datasets` gives `409 conflict` for a slug that exists and `422` for one that does not match `[a-z0-9][a-z0-9_-]{1,62}`.
- **Version** adds to the example below `manifest_path`, `schema_version`, `code_version` and `created_by_job_state` (the creating job's state, for SCR-02's icon).
- **Cursors** of `GET /datasets` and `GET /datasets/{slug}/versions` are opaque; both lists are short and sorted in memory.
- **Lineage:** `{version_id, ancestors, descendants, in_progress}`. `ancestors` run nearest first and end at the raw version; `descendants` are breadth first, each with its `parent_version_id` so the graph can be drawn. `in_progress` lists active jobs whose next version will be a child of this one, `{job_id, state, stage, step, total}` (SCR-03's running child).
- **Rows** are manifest rows with every column of DATA_MODEL.md §5, in `row_key` order. Filters combine with *and*; `status` and `split` take one value each. The response adds `matched`, the rows that pass the filters across all pages, and `next_cursor` is the last row key of a full page.
- **Stats:** `{version_id, row_count, by_status, reasons, by_stage, by_split, size_bytes, clip_score}`. `reasons` lists each `(reason_code, decided_at_stage, status)` with `rows` and `share` of all rows, largest first. `by_split` is null before a split. `size_bytes` and `clip_score` are distributions of the accepted rows in the report's shape (§3.6): sizes on fixed doubling edges from 1 KiB to 64 MiB, scores on the report's edges; `clip_score` is null before labelling.
- **Blobs:** `sha256` must be 64 lowercase hex characters (else `422`); an unknown blob is `404 not_found`. Without `w` the stored file is returned with its image media type; with `w` (16 to 2048) an upright RGB JPEG at most `w` wide. Both are cached as immutable.
- **Sources:** hidden folders are left out; `files` counts files recursively, hidden ones excluded.

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
| `POST /jobs` | Start a job. Body is a union on `kind`: `pipeline`, and since M8 `train` (below) | `202` Job |
| `GET /jobs` | List. Filters: `state` (one or a comma-separated list, `running,queued`), `kind`, `dataset` (slug), `q` (job-ID substring, for SCR-07's search). `counts` beside `items` gives jobs per state under the other filters, every state present. An unknown state is `422 validation_error`. The SCR-07 additions were accepted 2026-11-15 and built in M6 | `200` list of Job |
| `GET /jobs/{job_id}` | One job with its stages | `200` Job |
| `POST /jobs/validate` | Check a network's `kind: train` request as `POST /jobs` would, and create nothing (M11, RUN-1.10, ADR-0014 Addendum A rule 12). Returns `{parent_version_id, checks, lineage, freeze_problems, trainable_parameters, frozen_parameters, memory}`: for a fine-tune one check per rule `{rule, ok, parent, here, message, code}` (`Dataset type`, `Task`, `Input`, `Output`, `Spec`), all at once; `lineage` `{id, name, joins}` says whether the run joins a lineage or founds one; `memory` is the builder's estimate with gradients and optimizer state for trainable parameters only. Any other problem is the `422` of `POST /jobs`; a parent whose weights were deleted is `409 weights_deleted` | `200` TrainValidation |
| `POST /jobs/{job_id}/cancel` | Request cancellation | `202` Job; `409` if already finished |
| `POST /jobs/{job_id}/retry` | Requeue a failed job; finished stages are reused | `202` Job; `409` if not failed |
| `GET /jobs/{job_id}/events` | Page through events. Filters: `level`, `stage`, `after` (event ID), `min_level` (that level and above) and `tail` (the newest `limit` matching events, still oldest first; added in M6 for SCR-04) | `200` list of Event |
| `GET /jobs/{job_id}/telemetry` | Samples for charts. Params: `from`, `to`, `stage`, `max_points`. Samples older than 7 days come from the 1-minute averages (NFR-12, DATA_MODEL §4.8a): one point a minute, `cpu_per_core` empty | `200 {"resource": [...], "process": [...]}` |
| `GET /jobs/{job_id}/stream` | Live feed for one job (SSE, [§4](#4-live-streams)) | `200 text/event-stream` |
| `POST /jobs/{job_id}/pause` | Ask a running job to pause at its next safe point (M8, RUN-1.2, ADR-0014 rule 6). Sets `desired_state: "paused"` and the Redis control flag. A pipeline stage stops at its next safe point and runs again on resume from its caches; a training run writes a checkpoint at its current step first; a job between stages pauses before the next one starts. The supervisor then moves the job to `paused` and the stage to `paused` | `202` Job; `409 invalid_state_transition` if not running, `409 conflict` if being cancelled |
| `POST /jobs/{job_id}/resume` | Requeue a paused job from its paused stage (M8, RUN-1.2): `paused → queued`, the next attempt continues from the checkpoint or the caches | `202` Job; `409 invalid_state_transition` if not paused |

A paused job can be cancelled (`paused → cancelled`, at once). Pausing a queued job is refused: cancel it instead.

The Job also carries `heartbeat_at`, the supervisor's last heartbeat, and each stage `last_progress_at`, its last progress or activity; a stage's `status` can be `paused` (M8). The Job carries `pause_reason` (M8, RUN-1.6): `user` for a pause asked through the API, `thermal` for one the watchdog made when the GPU reached `thermal_pause_c` (83 °C); null when the job is not paused. The watchdog resumes `thermal` pauses below `thermal_resume_c` (75 °C) and never a `user` one. A job whose stage is alive but silent for `stall_timeout_s` (300 s) is `stalled` (RUN-1.7) and returns to `running` when the stage reports again; it can be cancelled like a running job. Each of these actions is an `action` event with `metrics.code` `thermal_pause`, `thermal_resume` or `stalled`.

Training request (M8, RUN-1.1, [ADR-0014](decisions/ADR-0014-training-jobs.md)):

```json
{
  "kind": "train",
  "dataset_version": "ds_f8e67211dd94",
  "config": {"model": {"name": "small_cnn"}, "epochs": 10, "batch_size": 128, "device": "auto"}
}
```

- **`dataset_version`** must be a `split` version of a `labelled_images` or (M10) `tabular` dataset, or a `tokenize` version of a `labelled_text` one; anything else, or an unknown ID, is a `validation_error` at `["dataset_version"]`. Tables and texts train builder graphs only: a reference model (`mlp`, `small_cnn`) on them is a `validation_error` at `["config", "model", "name"]`. A table with a numeric target trains a regression: a one-wide head, MSE on the standardised target ([ADR-0019](decisions/ADR-0019-tabular-and-text-models.md) rule 14).
- **`config`** is the `train` stage's config (`GET /config/schemas/train`): `model` (`mlp` or `small_cnn` until M9), `epochs`, `batch_size`, `optimizer` (`adam`, `sgd`), `learning_rate`, `momentum`, `weight_decay`, `schedule` (`cosine`, `constant`), `seed`, `deterministic`, `device` (`auto`, `cpu`, `gpu`; `auto` times a model under 1 M parameters on both devices and trains on the faster, RUN-1.9), `loader_workers` (null: 4 on the GPU worker, 1 on the CPU worker), `checkpoint_every_s` (300) and a read-only `algorithm_version`. Every field is recorded in the stage's config.
- **Fine-tuning** (M11, RUN-1.10, [ADR-0014 Addendum A](decisions/ADR-0014-training-jobs.md)): `config.init_from` (a trained version's ID, `mv_` and 12 hex) starts the run from that version's weights; `reset_head` (default false) re-initialises only the locked head; `freeze` (graph node IDs, or `head`; stored sorted and once each) keeps those layers' weights and BatchNorm statistics. `reset_head` and `freeze` need `init_from`. A run without `init_from` serializes without the three fields, so its config hash is as before M11. `config.model` must be the parent's spec (label ignored). The parent must be `trained` (`422 invalid` otherwise, `not_found` for an unknown ID) with its weights (`409 weights_deleted`). Each mismatch with the split is a `422 validation_error` at `["config", "init_from"]` naming both values: `init_from_type`, `init_from_task`, `init_from_input` (image shape; a table's columns by name, kind and role; a text's vocabulary and maximum length), `init_from_output` (classes by name and order, unless `reset_head`), `init_from_spec`. Freeze problems are at `["config", "freeze", i]`: `freeze_invalid` (an unknown node, one without weights, `head` with `reset_head`, or every layer frozen) and `freeze_unsupported` (a reference model, which has no node IDs). A table's run keeps the parent's fitted preprocessing. The stage runs the checks again before step 1.
- The job has kind `train` and one stage, `train`, on the `gpu` queue for `gpu` and `auto` and the `cpu` queue for `cpu` (CPU training needs the ml-cpu image, ADR-0014 rule 11). The stage makes no dataset version, so the job's `output_version_id` is null; its metrics are `epochs`, `steps`, `parameters`, `device`, `val_accuracy` (classification only), `val_loss`, `train_loss`, `checkpoint_bytes`, `weights_bytes`, `seconds`, and since M10 `task` with EVAL-1.2's metrics on validation and test once the run ends (`val_`/`test_` `accuracy`, `macro_f1`; for regression `mse`, `rmse`, `mae`, `r2` on the target's scale; the version's `metrics.detail` adds per-class F1 and the confusion matrix) and, on the GPU, `peak_vram_allocated_mb` and `peak_vram_reserved_mb`. With `device: auto` and a small model they also carry `device_choice` (`gpu` or `cpu`), `device_gpu_step_ms`, `device_cpu_step_ms` and `device_benchmark_cached` (0 or 1). When the CPU wins, the job goes `running → queued` and the stage's `queue` becomes `cpu`; an `action` event with `metrics.code` `device_handoff` says why. The run registers a model version (§3.9): the job's `model_version_id` names it, `partial` while the job is paused and `trained` once it completes; the weights are in `registry/<lineage>/<version>/`, not in the job's folder. A retry of a run that had already registered its weights returns that result without training again.
- **Events** (RUN-1.8): `substage` `setup` (model, device, rows), `train` progress reports at most once a second (`step`/`total` are global steps; metrics `epoch`, `loss`, `accuracy`, `learning_rate`, `grad_norm`, `step_ms`, `samples_per_s`), `validate` once per epoch (`train_loss`, `train_accuracy`, `val_loss`, `val_accuracy`), `checkpoint` (`checkpoint_step`, `checkpoint_bytes`), `resume` when a retry continues from a checkpoint (`resumed_at_step`, `resumed_epoch`), `device` when `auto` chose a device (the timings), a second `setup` event naming the lineage and its base (`lineage_id`, `base_version_id`, and since M11 `parent_version_id`), for a fine-tune a `setup` event with `init_from` and `frozen_parameters` and a `validate` event for epoch 0 before step 1 (`epoch: 0`, `val_loss`, `val_accuracy` or `val_rmse`, and `parent_val_loss` when it validates on the parent's own split with its own head; the version's `history` starts with that entry), `registry` when a pause records the version as partial, and `finish` (`model_version_id`).
- `GET /config/schemas` and `/config/defaults` list pipeline stages only, so the Start pipeline drawer does not show `train`.

**A classical model** (M10, ML-1.1 to ML-1.5, [ADR-0019](decisions/ADR-0019-tabular-and-text-models.md) part D) is the same `kind: train` with `classical` in place of `config` (exactly one of the two; both or neither is `422`):

```json
{
  "kind": "train",
  "dataset_version": "ds_98c3fdfd7edc",
  "classical": {"model": {"kind": "sklearn", "estimator": "random_forest", "params": {"max_depth": 9}}, "seed": 0}
}
```

- **`dataset_version`** must be a `split` version of a `tabular` dataset (`validation_error` at `["dataset_version"]` otherwise).
- **`estimator`**: `linear`, `ridge`, `lasso` (regression); `logistic_regression`, `naive_bayes` (classification); `svm`, `decision_tree`, `random_forest`, `gradient_boosting` (scikit-learn's histogram boosting), `knn` (either, by the dataset's task); `kmeans`, `dbscan`, `hdbscan`, `pca` (any table; the target is ignored). `params` are checked against the estimator's schema (`GET /ml/estimators/schema`) and its cross-field rules (ML-1.4): a broken one is `422` at `["classical", "model"]` with the fields named in the message. Rules that depend on the data are checked against the split before queueing: an estimator for the other task (`["classical", "model", "estimator"]`), `n_neighbors` or `n_clusters` above the train rows, `n_components` above the encoded width (`["classical", "model", "params", <field>]`).
- The job has kind `train` and one stage, `train_classical`, on the `cpu` queue; its config records every parameter, defaults filled in (NFR-4). Metrics: `task`; for classification `val_accuracy`, `val_macro_f1`, `test_accuracy`, `test_macro_f1`; for regression `val_`/`test_` `mse`, `rmse`, `mae`, `r2`; for clustering `clusters`, `noise_points`, `silhouette`, `davies_bouldin` on train; for PCA `explained_variance`; and `fit_s`, `weights_bytes`, `model_version_id`. The version's `metrics.detail` adds per-class F1 and the confusion matrix with its labels (EVAL-1.2).
- **Registry:** the lineage is the estimator on the dataset (its parameters are not part of the key, so a study's runs and re-runs share it); its base is the unfitted pipeline. Each version's folder holds `model.skops` and `model.json`. Re-training a classical version (`POST /model-versions/{id}/retrain`) repeats its `classical` config.
- **`GET /ml/estimators/schema`** returns, per estimator, `{title, tasks, schema}` (the parameters' JSON Schema), for SCR-09's classical form (UI-2.3).

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

Every key under `stages` is optional and overrides the stage's defaults. As of M4 the stages are `ingest`, `clean`, `preprocess`, `label` (GPU queue) and `filter`; M5 added `split` and `evaluate`. Which of them a request runs depends on its `dataset_type` (below); a key that names any other stage is rejected with `validation_error`. `until_stage` stops the pipeline after the named stage (`"ingest"` runs ingestion alone); `null` runs every available stage.

Give exactly one of `source` and `from_version`:

- **`source`**: start from a folder. The pipeline begins with `ingest`, and `dataset` is required.
- **`"from_version": "ds_…"`**: continue from an existing version. The stages after the one that made it run, and its output is the first stage's input. `dataset` may be left out; if given it must be the version's dataset. A version made by the last available stage has nothing after it and is rejected. An unknown version is a `validation_error` with `loc: ["from_version"]`.

With either, a key under `stages` or an `until_stage` that names a stage the request would not run is rejected. A stage whose input and config match an existing version reuses it (`cache_hit: true`) instead of running again.

**`dataset_type`** (M8, DATA-5.2) decides which stages run and some of their defaults:

| `dataset_type` | Stages | Defaults it changes |
|---|---|---|
| `image_caption` | `ingest`, `clean`, `preprocess`, `label`, `filter`, `split`, `evaluate` | none |
| `labelled_images` | `ingest`, `clean`, `preprocess`, `split` | `ingest.label_from: "folder"` (each image's class is its first folder); `clean.phash_max_distance: 4` and `split.leakage_distance: 6`, since pHash on small images is coarse (OC-5, the owner's choice 2026-11-18); `split.stratum_keys: ["source_folder"]`, so the split stratifies by class |
| `tabular` (M10) | `ingest`, `clean_table`, `split` | `split.stratum_keys: []` and `split.leakage_distance: 0`: the stratum is the class, or the target's quintile for a numeric target, and there is no pHash. The request must give `stages.ingest.table.target` |
| `labelled_text` (M10) | `ingest`, `clean_text`, `split`, `tokenize` | `ingest.text: {}` (JSONL keys `text`, `label`, `group`); the split as for `tabular`, grouped by `text_group` |

Left out or null, it is the `from_version`'s type if that version has one, else `image_caption`. A request's own `stages` override the type's defaults. Each type needs exactly one ingest setting and refuses the others: `label_from` for `labelled_images`, `table` for `tabular`, `text` for `labelled_text`, none for `image_caption`; a mismatch is a `validation_error` at `["stages", "ingest", <field>]`, and two of them at once at `["stages", "ingest"]`. Continuing a version with another type is a `validation_error` at `["dataset_type"]`. The job's spec records the resolved type. CIFAR-10 at 32 px also needs `"ingest": {"min_size": 32}`.

**Ingest settings for M10** ([ADR-0019](decisions/ADR-0019-tabular-and-text-models.md)):

| Field | Shape | Meaning |
|---|---|---|
| `label_from` | `"folder"` or `{csv, path_column: "path", label_column: "label"}` | Labelled images' classes from their first folder, or from a CSV in the source folder (DATA-1.5). With a CSV: an image without a row is `no_label`, one listed twice with different labels `conflicting_labels`; rows whose file is missing, paths listed twice and empty labels are counted and named, with their line numbers, under the ingest version's `counts.labels_csv`, and announced as WARN events |
| `table` | `{target, kinds: {column: "numeric" \| "categorical"}}` | One CSV or Parquet table (DATA-1.4). `kinds` overrides the inferred kinds. A missing target or a value that breaks the schema rejects the record (`missing_target`, `not_a_number`, `not_a_category`); other files in the folder are `unsupported_format` rows |
| `text` | `{text_field: "text", label_field: "label", group_field: "group"}` | JSONL files (one object per line) and TXT files in class folders (one text per line) (DATA-1.4). A line that is not JSON or has no text is `unreadable`; no class, `missing_label` (or `no_label` for a TXT file at the root) |

Stage configs: `clean_table` has only `algorithm_version`; `clean_text` `{max_chars: 10000, unicode_form: "NFKC"}` (DATA-2.4); `tokenize` `{vocab_size: 20000, min_freq: 2, max_length: null}`, null meaning the train split's 95th-percentile length rounded up to a multiple of 8, at most 512. A tokenize version's `counts.tokenize` holds the vocabulary's size and hash, L, and the train lengths' distribution with the share cut.

Job (abridged):

```json
{
  "id": "job_0142",
  "kind": "pipeline",
  "state": "running",
  "desired_state": null,
  "pause_reason": null,
  "heartbeat_at": "2026-11-14T13:58:02Z",
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

A job's and a stage's `error` is `{code, reason, suggested_action, detail}`; `detail` is text or an object of facts, such as `{"signal": "SIGKILL", "peak_rss_mb": 2410, "limit_mb": 2560}` for a killed process (E-013). A training job also has `model_version_id` and `model_lineage_id` (§3.9), null for other kinds. Since M10 a job also has `study`: for an `hpo_study` job `{id}` and for an `hpo_trial` job `{id, trial, study_job}` (§3.12), null for other kinds.

### 3.4 Activity log and telemetry

| Method and path | Purpose | Success |
|---|---|---|
| `GET /events` | Events across all jobs (MON-4.3). Filters: `level`, `stage`, `job_id`, `after`, `min_level`, `tail` (as for one job) | `200` list of Event |
| `GET /telemetry/latest` | The most recent resource sample | `200` Sample; `204` before the first sample exists |
| `GET /stream` | Global live feed: job state changes, alerts, idle telemetry (SSE) | `200 text/event-stream` |

Event: the shape in SPEC MON-1, plus `id`, `event_uid`, `type`, `reason`, `suggested_action` and `attempt`. Progress events carry `items_per_s` and `eta_s` in `metrics` (MON-1.5). Event lists are oldest first; `next_cursor` is the last event ID of a full page.

### 3.5 Review (LABEL-1.5)

| Method and path | Purpose | Success |
|---|---|---|
| `POST /versions/{version_id}/reviews` | Draw a sample. Body: `{sample_size, seed?}` | `201` ReviewSession with its items |
| `GET /versions/{version_id}/reviews` | The version's reviews, newest first, each with `progress` and without items (added in M6 for SCR-06: the review a report will use, and whether a newer one exists) | `200` list |
| `GET /reviews/{review_id}` | Session, items and progress | `200` |
| `PUT /reviews/{review_id}/items/{position}` | Record a decision. Body: `{decision: "accept" | "reject", note?}` | `200` item |
| `POST /reviews/{review_id}/close` | Close the session and return the acceptance summary | `200` |

Built in M5 (2026-11-16). Details:

- **Sampling.** `sample_size` is 1 to 1,000 and at most the version's accepted rows. The sample is drawn from the accepted row keys, sorted, with `random.Random(seed).sample`, so the same version and seed always give the same rows in the same order. Without `seed` the server draws one below 2³¹ and records it.
- **ReviewSession:** `{id, version_id, sample_size, seed, status, created_at, closed_at, progress, items}`. `progress` is `{sampled, decided, accept, reject, undecided, by_split: {split: {sampled, accept, reject}}}`. Each item is `{position, row_key, split, decision, note, decided_at, row: {sha256, derived_sha256, caption, clip_score, split_stratum, caption_model, caption_model_revision, width, height, size_bytes, derived_width, derived_height}}` (the last seven added in M6 for SCR-05). `row` is read from the manifest so SCR-05 can show the image and caption. `split` is the row's split when it was sampled (DATA-4.7).
- **Decisions** can be changed while the session is open. A decision on a closed session gives `409 conflict`, an unknown position `404 not_found`, and a value other than `accept` or `reject` gives `422 validation_error`. No decision changes a manifest, a split or a status.
- **Close** returns the session without its items, with `progress`, `acceptance` and `acceptance_unavailable`. `acceptance` is the owner's `review_acceptance` result (EVAL-1.1): overall and per split, each with a Wilson interval. While that unit is not written, `acceptance` is null and `acceptance_unavailable` says why. Closing a session twice gives `409 conflict`.

### 3.6 Evaluation (EVAL-1.1)

| Method and path | Purpose | Success |
|---|---|---|
| `POST /versions/{version_id}/reports` | Build a report. Body: `{kind: "phase1_dataset", review_id?}` | `202` Job |
| `GET /versions/{version_id}/reports/latest` | The newest report of a kind (`?kind=`) | `200` Report |
| `GET /reports/{report_id}` | One report | `200` Report |

Built in M5 (2026-11-16). Details:

- **Jobs.** `POST /versions/{id}/reports` queues a one-stage job of kind `report`, whose only stage is `evaluate` (cpu queue). A pipeline also ends with `evaluate`, after `split`. The stage makes no dataset version. Its output is the version it reported on, so a pipeline's output version is still the split version, and its metrics carry `report_id`.
- **Review.** With `review_id`, the report uses that review, which must belong to the version (otherwise `422 validation_error`). Without one, it uses the version's newest closed review, or none if there is none.
- **Report:** `{id, version_id, kind, review_session_id, job_id, created_at, report}`. `report` is `{kind, version_id, stage, algorithm_version, confidence, rows: {total, accepted, by_split}, rejections: {total, accepted, by_stage: {stage: {reason: rows}}}, filter_rate, clip_scores: {edges, labelled, accepted, by_split: {split: …}}, review}`. Each distribution is `{count, mean, std, minimum, maximum, quantiles: {p05…p95}, edges, histogram, below, above}`. `review` is `{review_id, status, sample_size, seed, decided, acceptance: {sampled, undecided, overall, by_split}}`, or null. Each rate is `{decided, accepted, interval: {estimate, low, high, confidence}}`. `by_split` is null for a version that has not been split. The statistics are the owner's (§5), so the stage fails with the usual owner-unit message until they are written.
- **Config** (`stages.evaluate`): `kind`, `review_id`, `score_edges` (default 0 to 0.5 in steps of 0.025, strictly increasing), `confidence` (0.95).
- `GET …/reports/latest` gives `404 not_found` until the version has a report.

### 3.9 Model registry (M8, REG-1.1, REG-1.2, [ADR-0015](decisions/ADR-0015-model-registry-and-retention.md))

Since M11 a version's detail has `children` (`[{id, lineage_id, lineage_name, weights_status}]`, the versions started from it with `init_from`, in any lineage; empty for a base) and a lineage's detail has `founded_from` (`{version_id, lineage_id, lineage_name, reset_head}` when its first fine-tune started from another lineage's version, else null) (RUN-1.10, SCR-11 v2).

| Method and path | Purpose | Success |
|---|---|---|
| `GET /models?state=&task=&q=` | Lineages, most recently updated first. `state` (`base`, `partial`, `trained`) keeps lineages with a version in that state; `q` matches the name | `200 {items, next_cursor}` |
| `GET /models/{lineage_id}?state=&weights=` | One lineage and its versions, newest first. `weights` is `present` or `deleted` | `200` lineage with `versions` |
| `GET /model-versions/{version_id}` | Everything that made a version (REG-1.1) | `200` version detail |
| `GET /models/{lineage_id}/disk` | Disk use measured on disk (REG-2.11) | `200 {lineage_id, parts: {base, latest, intermediate, pinned, checkpoints}, disk_bytes, registry_bytes, matches, orphans, missing}` |
| `POST /model-versions/{version_id}/pin` | Exempt from retention (REG-2.5); `409 conflict` unless trained with weights on disk | `200` version detail |
| `DELETE /model-versions/{version_id}/pin` | Unpin; deletes nothing now | `200` version detail |
| `GET /models/compare?split=&v=&v=` | Two to six versions scored on one split version, side by side (M11, EVAL-1.3, SCR-11 v2). `{split: {version_id, dataset, dataset_type, test_rows, task, classes}, versions: [{id, lineage_id, lineage_name, kind, model, parameters, trained_at, job_id, weights_status, parent_version_id, parent_is_base, study_id, metrics, correct, accuracy_low, accuracy_high, per_class_f1}], best: {metric: [version ids]}, overlap}`, versions in the order asked. `metrics` are the task's test metrics without the prefix (`accuracy`, `macro_f1`; `mse`, `rmse`, `mae`, `r2`); `accuracy_low`/`high` the Wilson 95 % interval of `correct` of `test_rows` (EVAL-1.1's function); `best` lists the versions with the best value of each metric, ties sharing it, none when all tie; `overlap` is true when any two intervals overlap. An unknown version, one scored on another split or one without test metrics is `422 validation_error` at `["v", i]`; fewer than 2, more than 6 or a repeat at `["v"]` | `200` Comparison |
| `GET /models/compare/candidates?split=` | The trained versions on a split, newest first, for the comparison's picker: `{split, items: [{id, lineage_id, lineage_name, test_metric, test_value, weights_status, trained_at, study_id, scoring_job_id, can_score, why_not}]}`. `test_value` is null for a version without test metrics (trained before M10); `can_score` when it has weights and is a network; `scoring_job_id` names a queued or running Score on test job | `200` CompareCandidates |
| `POST /model-versions/{version_id}/score-test` | Score on test, once (M11, EVAL-1.3; the owner's exception to K8): a job of kind `score_test` runs the `test_eval` stage for a trained network version without test metrics, on its own split, and writes `test_*` and `metrics.detail.test` with a conditional update. `409 conflict` if it has test metrics, is being scored, is classical or is not trained; `409 weights_deleted` without weights | `202` Job |
| `POST /model-versions/{version_id}/retrain` | A run with the version's exact config and dataset version (REG-2.7); `409 conflict` for a base or partial version | `202` Job |
| `POST /models/{version_id}/predict` | A prediction (REG-1.4). Body: exactly one input, the one the version takes: `{image_b64}` or `{blob}` (a data-folder SHA-256, e.g. a row's `derived_sha256`) for images; since M10 `{record}` for a table (one value per feature column by name, `null` to impute, no target) and `{text}` for text. Another input is `422 wrong_input`, as is a record with a missing, unknown or ill-typed field or the target (each in `errors` by name); DBSCAN and HDBSCAN versions are `409 not_servable` | `200 {version_id, lineage_id, device, fallback_reason, task, top: [{class, probability}] \| value \| cluster \| components, load_ms, total_ms}`: `top` for classification (a classical SVM's probability is null), `value` for regression on the target's scale, `cluster` for k-means, `components` for PCA. Since M11 a text's answer has `input: {tokens, unknown, truncated_at}`: the tokens before cutting (punctuation counts), those outside the vocabulary, and L when it was cut (SCR-11 v2). Classical models run on the CPU and never take the GPU lease ([ADR-0019](decisions/ADR-0019-tabular-and-text-models.md) rules 20 and 21) |
| `POST /retention/plan` | The dry run, answered at once (REG-2.9). Body `{lineage_id?, values?}`; `values` plans with other settings than the saved ones | `200` plan |
| `POST /retention/run` | Queue a retention job with the saved settings. Body `{lineage_id?, dry_run?}` | `202` Job |

- **Lineage:** `{id, name, task, model, dataset, input_shape, classes, parameters, counts: {base, partial, trained}, latest_version_id, latest_state, main_metric, weights_bytes, checkpoint_bytes, created_at, updated_at}`. `latest` is the newest trained version, or the base before any run completes (REG-2.2). `main_metric` is its `val_accuracy` until EVAL-1.2. `weights_bytes` sums the registry's records of weights on disk; `checkpoint_bytes` the lineage's in-run checkpoints. The measured disk use (REG-2.11) comes with retention.
- **Version (in a lineage's list):** `{id, lineage_id, state, is_latest, weights_status, weights_bytes, pinned, parent_version_id, job_id, job_state, dataset_version_id, config_hash, main_metric, note, created_at, trained_at}`. A partial version has no `weights_bytes`: its weights are its run's checkpoint.
- **Version detail** adds `{lineage_name, config, metrics, history, weights_path, weights_deleted_at, study_id}`; since M10 `metrics` holds EVAL-1.2's `val_*` and `test_*` scores and their `detail` by split (per-class F1, confusion matrix, labels, rows), and `study_id` names the study a version is the best trial of.
- Each version also carries `weights_deleted_by_job_id`, `retention_action` (`keep`, `delete` at the next cleanup, `blocked`, or `none` once deleted) and `retention_reason`, under the saved settings. A lineage's detail adds `retention: {keep_intermediate, source: global | lineage, last_run: {job_id, finished_at, versions_deleted, bytes_freed} | null}`.
- **Plan:** `{lineages: [{lineage_id, name, keep_intermediate, source, latest_version_id, delete, keep, blocked, checkpoints, bytes_to_free}], versions_to_delete, bytes_to_free, token, settings_version}`. Each decision is `{version_id, reason, weights_bytes, main_metric, created_at}`; `blocked` lists versions the policy would delete that are in use or a parent (REG-2.8); `checkpoints` those of completed and cancelled runs (`{job_id, bytes, reason}`). `token` names this exact plan.
- **Retention jobs** have kind `retention` and one `retention` stage on the CPU queue. The supervisor queues one for a run's lineage after every training run that completes or is cancelled; cancelling a paused run through the API does too. Events: `plan` (per lineage, and each would-be deletion in a dry run), `blocked` (WARN, with `reason` and `suggested_action`), `deleting` and `delete` (`version_id`, `lineage_id`, `bytes_freed`), `checkpoint` (`checkpoint_job_id`, `bytes_freed`), `recover` and `orphan` (WARN). Metrics: `lineages`, `dry_run`, and `versions_deleted`, `bytes_freed`, `checkpoints_removed`, `blocked`, `orphans_removed`, `recovered`, or for a dry run `would_delete` and `bytes_to_free`.
- **Predict** (ADR-0016). The API forwards to the `inference` service (`inference_url`) with the standard library; the service holds one model. A model already on the GPU is used there; otherwise the GPU lease is tried once. While a job holds it, a model under `inference_cpu_max_parameters` (50 M) runs on the CPU and `fallback_reason` says why; a larger one is `409 gpu_busy` naming the job. If `TrainingState.close` (RUN-1.4) fails its probe at the service's first decision, the GPU is not used at all and `fallback_reason` says so (it was the case until 2026-11-19, when `close()` was written). A version whose weights were deleted is `409 weights_deleted`; a partial one `409 conflict`; an undecodable image `422`; the service not running `503 dependency_unavailable`, saying how to start it. The image is resized to the model's input with LANCZOS and normalised as in training.
- Unknown IDs are `404 not_found`.

### 3.10 Settings (M8, OC-9; SCR-14)

| Method and path | Purpose | Success |
|---|---|---|
| `GET /settings` | Every section: `{sections: [{name, title, requirements, values, schema, version, lineages}]}` | `200` |
| `PUT /settings` | Save one section. Body `{section, values, version, confirm?}` | `200 {section, job_id}` |

- **Retention** (`retention`, REG-2.3, REG-2.4, REG-2.10): `values` is `{keep_intermediate: 0–100 | "all", overrides: {lineage_id: 0–100 | "all"}}`; `schema` is its JSON Schema, with `x-applies` on each field; `lineages` lists each lineage's `keep_intermediate` in force, its `source` and its current `intermediates` and `intermediates_bytes`. 
- **Training** (`training`, RUN-1.3, RUN-1.6, RUN-1.7): `values` is `{checkpoint_interval_min: 1–60, thermal_pause_c: 60–100, thermal_resume_c, stall_timeout_s: 60–3600}`; `thermal_resume_c` must be at least 5 below `thermal_pause_c`, and `thermal_pause_c` at most the card's slowdown point. A field's problem is `422 validation_error` with `errors[].loc` = `["values", field]`. `context` is `{gpu_slowdown_c, gpu_temp_high_7d: {celsius, job_id, at} | null, running_jobs}`; `lineages` is null. The section is the retention section's sibling in `sections`; retention's `context` is null. The watchdog applies a saved section from its next check; the checkpoint interval applies to new runs (NFR-4).
- `version` must be the one read; otherwise `409 conflict` with the current `version`.
- A save that would delete weights is refused with `409 confirmation_required` and the `plan`; the same body with `confirm: plan.token` saves it and queues a retention job that deletes only that plan's versions (`job_id`). If anything changed in between, the token no longer matches and a new plan is returned the same way (ADR-0015 rule 7).
- An unknown lineage in `overrides` is `422 validation_error`.

### 3.12 Studies (M10, HPO-1.1 to HPO-1.5; [ADR-0018](decisions/ADR-0018-hpo-execution.md))

`POST /jobs` with `kind: hpo_study` starts one:

```json
{
  "kind": "hpo_study",
  "dataset_version": "ds_98c3fdfd7edc",
  "classical": {"model": {"kind": "sklearn", "estimator": "random_forest"}},
  "space": [
    {"path": "/classical/model/params/n_estimators", "type": "int", "low": 50, "high": 500, "step": 10},
    {"path": "/classical/model/params/max_features", "type": "categorical", "choices": ["sqrt", "log2", "1.0"]}
  ],
  "objective": "cv_accuracy", "n_trials": 50, "timeout_s": 600, "pruning": true, "cv_folds": 5, "seed": 0
}
```

- **The base** is `classical` for a classical model or `config` for a network, checked as a single run would be (`POST /jobs` with `kind: train`). **`space`** holds 1 to 20 markers `{path, type: int | float | log_float | categorical, low, high, step?, choices?}`; a path is a JSON pointer into the base (`/classical/model/params/<field>`, `/config/<training field>`, `/config/model/graph/nodes/<node id>/layer/<field>`). A marker outside its field's own range, on a field that cannot be tuned, tuned twice, or none of whose values could be valid with the fixed fields is a `validation_error` at `["space", i, "path"]` (HPO-1.1).
- **`objective`**: `cv_accuracy` (default) or `cv_macro_f1` for a classical classifier, `cv_r2` (default) or `cv_rmse` for a regressor; for a network, `val_accuracy` (default) or `val_loss` for a classifier, `val_rmse` (default) or `val_loss` for a regressor. The direction follows. Clustering and PCA are refused: they have no score to tune. `n_trials` 1–200 (20), `timeout_s` 60–86,400 (3,600, running time, pauses excluded), whichever comes first ends the study (HPO-1.2); `pruning` (true: the median pruner, HPO-1.3); `cv_folds` 2–10 (5); `seed`.
- **A classical study** is a job of kind `hpo_study` with one stage, `study`, on the `cpu` queue; its config and spec carry the `study_id`.
- **A network's study** has two stages: `study` on the `cpu` queue, then `test_eval` on the run's queue (`gpu`, or `cpu` for `device: cpu`). The `study` stage runs in short steps; after each it is `waiting` (a stage status) while the job stays `running`, and the trial it waits for is a job of kind `hpo_trial`, a `train` run whose spec carries `study_id`, `study_job` and `trial` (its number). A trial's job is listed and controlled like any other.
- **Pause, resume and cancel** are the study job's. On a network's study waiting for its trial, pause pauses the trial (a checkpoint first) and the study follows; a trial not yet started is withdrawn (`interrupted`). Resume resumes the trial. Cancel cancels the study at once and its trial at its next safe point, and keeps no trial's weights.

| Method and path | Purpose | Success |
|---|---|---|
| `GET /studies?limit=` | Studies, newest first | `200 {items}` |
| `GET /studies/{id}` | `{id, job_id, kind, dataset_version, objective, direction, limits, pruning, cv_folds, seed, space, base, state, ended_by, elapsed_s, trials: {started, complete, pruned, failed, running, interrupted}, best: {trial, value, params} \| null, importance, best_version_id, test_metrics, test_evaluated_at, created_at, finished_at}` | `200` |
| `GET /studies/{id}/trials` | `{items: [{number, state, params, value, step, prune_reason, code, intermediate: [[step, value]], duration_s, job_id, started_at, finished_at}]}`, oldest first | `200` |
| `GET /studies/{id}/best-config` | The base with the best trial's values set, for SCR-09 to prefill (`/runs/new?study=`, HPO-1.5); `404` before a trial completes | `200` |

- **States:** a study is `running`, then `closed` (no further trial; `ended_by` `n_trials` or `timeout`), then `completed` once its best configuration is registered (`best_version_id`) and tested once (`test_metrics`, HPO-1.4); or `cancelled`. For a network the registration happens when the study closes and the test in `test_eval`. A trial is `complete`, `pruned` (`prune_reason` `median` or `budget`, `step` the fold or epoch it stopped at), `failed` (`code` `invalid_combination` or `over_vram`, never trained; a trial job's error code, such as `gpu_oom`; `cancelled`; or `interrupted` by a pause or crash, which does not count against `n_trials`) or `running`.
- **`importance`** is `{method: "fanova", values: {path: share}}`, or `{reason}` when it cannot be computed (one field tuned, fewer than two complete trials, or every trial scoring the same).

### 3.11 Model builder (M9, BUILD-1, UI-2.3, DATA-5.5; [ADR-0017](decisions/ADR-0017-model-spec.md))

| Method and path | Purpose | Success |
|---|---|---|
| `GET /model-specs/schema` | JSON Schemas for SCR-08's forms: `layer` (one per type under `$defs`, with ranges, defaults and descriptions), `graph`, `max_nodes`, `max_edges` (UI-2.3) | `200` |
| `POST /model-specs/validate` | Shapes, counts, the memory estimate and every problem. Body `{graph, dataset_version, optimizer?}` (`adam`, `sgd`, `sgd_momentum`) | `200 {dataset, report}` |
| `GET /dataset-types` | Each type's `{name, title, input_kind, families, default_metrics, trainable, note}` (DATA-5.5) | `200` list |
| `GET /model-templates?dataset_type=` | Built-in templates first, then saved ones, by name | `200` list |
| `POST /model-templates` | Save a spec. Body `{name, description?, dataset_type, spec}`; a taken name is `409 conflict` | `201` template |
| `GET`, `PUT`, `DELETE /model-templates/{id}` | Read; change `name`, `description` or `spec`; delete. A built-in template is `409 conflict` for `PUT` and `DELETE` | `200`, `200`, `204` |

- **A graph** is `{spec_version: 1, label, nodes: [{id, layer: {type, ...}}], edges: [[from, to]]}`, between the fixed nodes `input` and `output` (ADR-0017 rule 1). Layer types and fields are in the schema; a field out of range, an unknown type or an unknown field is `422 validation_error` with `errors[].loc` naming it (BUILD-1.2).
- **`dataset`** is the version's two locked ends: `{version_id, dataset_type, input: {kind, dims, vocab}, input_text, output_size, classes, families, trainable, problem}`. When the version cannot feed the builder (not a split, or a type not trained until M10), `problem` says why in words and `report` is null.
- **`report`** is `{valid, input, output_size, nodes: [{id, type, inputs, shape, shape_text, parameters}], head: {in_features, out_features, parameters}, total_parameters, family, memory: {optimizer, weights_bytes, gradients_bytes, optimizer_bytes, total_bytes, activation_bytes_per_sample}, problems: [{node_id, code, message}], structure_hash}`. A spec with problems is still `200`; `valid` is false and `total_parameters`, `memory` and `structure_hash` are null. Nodes are in canonical order (topological, ties by ID); a node whose shape could not be worked out has `shape: null`.
- **Problem codes:** `shape_mismatch`, `rank_mismatch`, `below_one_pixel`, `invalid_padding`, `heads_do_not_divide`, `odd_model_dim`, `sequence_too_long`, `dropout_needs_layers`, `tokens_need_embedding`, `invalid_padding_idx`, `merge_needs_two`, `merge_shape_mismatch`, `too_many_inputs`, `output_not_vector`, `output_inputs`, `cycle`, `unreachable`, `dangling`, `duplicate_id`, `unknown_node`, `edge_direction`, `duplicate_edge`, `incompatible_family`.
- **A template** is `{id, name, description, dataset_type, builtin, spec, structure_hash, created_at, updated_at}`; `spec` is stored in canonical form, so it reloads identically however it was listed (BUILD-1.7).
- **Training a graph:** `POST /jobs` with `config.model` = `{name: "graph", graph}`. A graph with problems is `422 validation_error`, each problem an error with `loc` `["config", "model", "graph", node_id?]` and its `code`. The lineage is named after the graph's `label` and keyed without it (ADR-0017 rule 1).

### 3.7 Configuration

| Method and path | Purpose | Success |
|---|---|---|
| `GET /config/schemas` | Names of the config models | `200` list |
| `GET /config/schemas/{name}` | JSON Schema for one config model, for generated forms | `200` JSON Schema |
| `GET /config/defaults` | Current defaults | `200` |

Built in M6 (2026-11-16) for the Start pipeline drawer. The names are the stage names, in pipeline order: `GET /config/schemas` gives `{items: [{name, queue, title}]}`, `title` being the config model's class. `GET /config/defaults` gives `{stage: config}`, each as a pipeline with no overrides would resolve it. An unknown name is `404 not_found`.

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
| Exact-duplicate grouping | DATA-2.1 | `group_exact_duplicates(items: Sequence[ExactItem]) -> list[DuplicateGroup]`: rows with content hashes → groups of identical files, one deterministic keeper each | M3; written by the agent at the owner's request (ADR-0012) |
| Perceptual hash and near-duplicate grouping | DATA-2.2 | `perceptual_hash(image, *, hash_size) -> str` (lowercase hex of the hash's bits); `group_near_duplicates(items: Sequence[HashedItem], *, max_distance) -> list[DuplicateGroup]` by Hamming distance, grouped transitively | M3; written by the agent at the owner's request (ADR-0012); threshold 8, chosen by the owner (OC-5) |
| Caption batching loop | LABEL-1.1, LABEL-1.2 | `caption_items(items: Sequence[CaptionItem], *, captioner, batch_size, open_image, commit, sink, checkpoint) -> CaptionReport`: uncached items → captions committed per batch, surviving `CudaOutOfMemory` by halving the batch | M4; written by the agent at the owner's request (ADR-0012) |
| VRAM manager | RUN-1.4, RUN-1.5 | `VramManager(gpu: GpuBackend, *, sink)`: `load(loader, *, name, estimate_mb)`, `unload() -> UnloadReport`; one model at a time, refuse what does not fit, prove allocated memory is back at baseline | M4; written by the agent at the owner's request (ADR-0012) |
| CLIP scoring | LABEL-1.3 | `clip_similarity(image_vectors, text_vectors) -> list[float]`: raw CLIP embeddings → one score per pair: plain cosine | M4; written by the agent at the owner's request (ADR-0012) |
| Filter rule | LABEL-1.4 | `filter_decisions(items: Sequence[ScoredItem], *, threshold) -> dict[row_key, reason]`: rows to mark `filtered` (`clip_below_threshold`, `missing_caption`) | M4; written by the agent at the owner's request (ADR-0012); threshold 0.2, chosen by the owner (OC-5) |
| Leakage grouping | DATA-4.2 | `group_for_split(items: Sequence[LeakageItem], *, max_distance) -> list[SplitGroup]` in `stratal.data.splitting`: accepted rows (`row_key`, `sha256`, `phash`, `dup_group_id`) → every row in exactly one group, joined transitively by a shared duplicate group or a pHash distance ≤ `max_distance`; `SplitGroup(anchor, members)`, anchor = smallest member `sha256` | M5; written by the agent at the owner's request, 2026-11-16 (ADR-0012) |
| Stratification | DATA-4.3 | `strata_for(items: Sequence[StratumItem], *, keys: Sequence[StratumKey]) -> dict[row_key, stratum]`: `clip_quartile` (`q1`–`q4`, interpolated boundaries, ties down), `orientation` of the upright image, `source_folder` (first `row_key` component, `.` at the root), joined by `\|` in key order; `all` without keys | M5; written by the agent at the owner's request, 2026-11-16 (ADR-0012) |
| Split assignment | DATA-4.1, 4.4, 4.5 | `assign_splits(groups: Sequence[GroupToAssign], *, proportions: Proportions, seed) -> dict[anchor, split]`: each `GroupToAssign(anchor, stratum, members: Member(row_key, previous))` carries its split where members agree, merges to the side with more members, and new groups fill each stratum's shortfall to within one group, per stratum and in total. The shell looks up `previous` and lists moved rows | M5; written by the agent at the owner's request, 2026-11-16 (ADR-0012) |
| Report statistics | EVAL-1.1 | In `stratal.eval.report_stats`: `rejection_counts(rows: Iterable[RowStatus]) -> RejectionCounts`, `filter_rate(counts) -> float \| None`, `score_distribution(scores, *, edges) -> ScoreDistribution` (type-7 quantiles, fixed bins), `wilson_interval(successes, trials, *, confidence=0.95) -> Interval`, `review_acceptance(items: Sequence[ReviewedItem], *, confidence) -> Acceptance` (overall and per split) | M5; written by the agent at the owner's request, 2026-11-16 (ADR-0012) |
| Training state close | RUN-1.4 (v2) | `TrainingState(model, optimizer, *, info).close()` in `stratal.train.memory`: gradients set to `None`, optimizer state emptied, own references dropped, harmless twice; after it the v1 `VramManager.unload` finds allocated memory back at baseline | M8; stub, strict expected-failure tests and `FakeTrainingState` delivered 2026-11-17 |
| Training out-of-memory | RUN-1.5 | `suggest_batch_size(*, failed_batch, fixed_mb, per_sample_mb, cap_mb) -> int \| None` (largest power of two below the failed batch that fits 90 % of the cap) and `training_oom_error(*, failed_batch, suggested, peak_mb, cap_mb) -> GpuOom` ("reduce batch size from 32 → 16") | M8; stubs, tests and fakes delivered 2026-11-17 |
| From-scratch layers | BUILD-1.8 | `MultiHeadSelfAttention`, `PositionalEncoding`, `ScratchLSTMCell` in `stratal.build.scratch`, with PyTorch's parameter layout; each matches PyTorch's own layer within 1e-5 (attention) or 1e-6 | M9; written by the agent at the owner's request, 2026-11-19 (ADR-0012) |
| Task metrics | EVAL-1.2 | In `stratal.eval.task_metrics`: `classification`, `regression`, `clustering`, `perplexity`, `translation` (BLEU, chrF), `cider_d`, `mean_clip_score`; pure functions; `metric_set(task)` names each task's metrics | M10; stubs and tests delivered 2026-11-17 (M8); written 2026-11-19 by the agent at the owner's request (ADR-0012), on scikit-learn's metrics and sacreBLEU (K2), CIDEr-D, perplexity and the mean CLIP score in house. The libraries load inside the functions, so `api` imports the module for `metric_set` without them |
| Semantic-duplicate clustering | DATA-2.3, DATA-4.8 (P2) | CLIP image vectors → clusters in the label stage, joined by the split ([ADR-0013](decisions/ADR-0013-semantic-duplicates.md)) | M11 |

What the plumbing gives each unit: a `JobContext` (emit an event, report progress, ask whether to stop), the derivation cache (`get_many`, `put_many`), `load_version` (stream manifest batches), the blob store (open a file by hash), and a model handle from the VRAM manager.

## 6. Command line

The CLI calls the same domain functions as the workers. It prints its result as JSON on standard output and logs to standard error. Exit code 0 means success, 1 a typed error (printed as `{"error": {code, detail, suggested_action}}`), 2 an owner unit that is not implemented yet.

| Command | Does | Since |
|---|---|---|
| `python -m stratal.cli ingest --dataset NAME --source PATH [--min-size N] [--label-from folder]` | Ingests a folder as a raw version and registers it; `--label-from folder` records each image's class from its first folder (M8, DATA-5.2) | M1 |
| `python -m stratal.cli versions --dataset NAME` | Lists a dataset's versions, newest first | M1 |
| `python -m stratal.cli lineage VERSION_ID` | Shows a version's ancestry back to raw | M1 |

| `python -m stratal.cli models list` | Lists the allowed models and whether each is pulled | M4 |
| `python -m stratal.cli models pull NAME... \| --all` | Downloads and verifies allowed models; the only hub access (NFR-8) | M4 |
| `python -m stratal.cli registry backfill [--dry-run]` | Registers training runs that finished before the registry: rebuilds a missing base from the run's seed, moves `jobs/<id>/weights/` into `registry/`, and gives a run a parent only if its initial weights equal the base. Needs PyTorch: run it in `worker-cpu` | M8 |

Still planned for phase 1: run a pipeline.

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

### Internal interfaces added in M4

| Interface | Where | Shape |
|---|---|---|
| Model protocols | `stratal.label.models` | `Captioner.caption(images) -> list[str]`, `ClipEncoder.encode_images/encode_texts -> list[Vector]`, `GpuBackend` (allocated, reserved, free, total MB; `synchronize`, `empty_cache`); adapters raise `CudaOutOfMemory(batch_size, detail)` |
| Model adapters | `stratal.label.hf` (GPU image only) | `load_captioner(name, models_dir, *, dtype, task, max_new_tokens, num_beams)`, `load_clip(name, models_dir, *, dtype)`, `TorchGpu`, `set_vram_cap(fraction)`, `library_versions()` |
| Label stage | `stratal.label.stage` | `run_label(*, parent_manifest, parent_version_id, data_dir, config: LabelConfig, derive, sink, cache, models: LabelModels, units: LabelUnits = OWNER_UNITS, lease, checkpoint) -> LabelResult` |
| Filter stage | `stratal.label.filter_stage` | `run_filter(*, parent_manifest, parent_version_id, data_dir, config: FilterConfig, derive, sink, rule = filter_decisions) -> FilterResult`; since M5 it also validates the image–caption schema (DATA-5.1), rejecting rows without a caption or file, and sets `dtype` and `counts.validation`. `VersionRecord` gained `dtype`, registered by `register_version`; `run_split` gained `dtype` (the parent's) |
| GPU lease | `stratal.runtime.gpu_lease` | `with gpu_lease(engine, *, holder, wait_s=0)`: raises `GpuBusy` naming the holder; `current_holder(engine)` |
| Pretrained models | `stratal.pretrained` | `catalog.get_model(name)`, `catalog.local_path(models_dir, name)`, `pull.pull(model, models_dir)` |

### Internal interfaces added in M3

| Interface | Where | Shape |
|---|---|---|
| Dedupe units (DATA-2.1, DATA-2.2) | `stratal.data.dedupe` | `ExactItem(row_key, sha256)`, `HashedItem(row_key, phash, width, height, size_bytes)`, `DuplicateGroup(keeper, members)`, the three functions above, and `ALGORITHM_VERSION` |
| Clean stage | `stratal.data.clean` | `run_clean(*, parent_manifest, parent_version_id, data_dir, config: CleanConfig, derive, sink, cache, units: DedupeUnits = OWNER_UNITS, checkpoint) -> CleanResult(record, metrics, seconds)`; raises `ContractBroken` when an owner unit breaks its contract |
| Preprocess stage | `stratal.data.preprocess` | `run_preprocess(*, parent_manifest, parent_version_id, data_dir, config: PreprocessConfig, derive, sink, cache, checkpoint) -> PreprocessResult`; `transform(path, config) -> (jpeg_bytes, width, height)`; `target_size(w, h, max_side)` |
| Derivation cache | `stratal.storage.derivations` | `DerivationCache` protocol: `get_many(kind, params_hash, input_hashes) -> {input_hash: value}`, `put_many(kind, params_hash, {input_hash: value})`; `PgDerivationCache(engine, job_id=)`, `MemoryDerivationCache()`, `params_hash(params)` |
| Blob store | `stratal.storage.blobs` | gained `put_bytes(data) -> StoredBlob` |
| Version loading | `stratal.data.registry` | `manifest_for(session, data_dir, version_id) -> (DatasetVersion, Path)`, checked against the registration (`VersionDataMissing`); `load_version(...) -> Iterator[Row]`; `find_memoized(session, data_dir, *, stage, parent_version_id, config, derive)` |
| Derived-stage frame | `stratal.runtime.stages` | `run_derived_stage(run, *, stage, config_model, body)`: resolve the input by ID, reuse a memoized version, otherwise run `body` and register; `make_clean_runner(units)` |
| Job creation | `stratal.runtime.jobs` | `create_job_from_plan(..., input_version_id=None)` |
| Test helpers | `stratal.testing` | `fakes`: `no_exact_duplicates`, `fake_perceptual_hash`, `no_near_duplicates`, `ScriptedGroups`; `fake_clean` (registers clean with fakes when named in `STRATAL_EXTRA_STAGES`); `stagekit` (stages as library calls); `synthetic` gained `exif_rotated` and `odd_modes` |

### Internal interfaces added in M5

| Interface | Where | Shape |
|---|---|---|
| Split units (DATA-4.1 to 4.5) | `stratal.data.splitting` | `LeakageItem`, `SplitGroup`, `StratumItem`, `StratumKey`, `Member`, `GroupToAssign`, `Proportions`, `SPLITS`, the three functions in §5, `targets(rows, proportions)` and `ALGORITHM_VERSION` |
| Split stage | `stratal.data.split_stage` | `run_split(*, parent_manifest, parent_version_id, data_dir, config: SplitConfig, derive, sink, previous: PreviousSplit \| None, units: SplitUnits = OWNER_UNITS, checkpoint) -> SplitResult(record, report, metrics, seconds)`; raises `ContractBroken` when a unit breaks its contract; `leakage_check(rows, *, max_distance)` |
| Split config | `stratal.data.split_stage.SplitConfig` | `train`, `val`, `test` (0.8 / 0.1 / 0.1, finite, not negative, summing to 1), `seed` (0), `leakage_distance` (12), `stratum_keys` (all three), `continues_version_id` (set by the stage; a different value from the user fails the stage), `algorithm_version`. Set per job as `stages.split` |
| Previous split | `stratal.data.registry` | `find_previous_split(session, *, dataset_id, parent_version_id, config) -> DatasetVersion \| None`: the dataset's newest split with the same proportions and seed, not made from the same parent |
| Split runner | `stratal.runtime.stages` | `make_split_runner(units)`: looks up the previous split, writes it into the config, then the derived-stage frame |
| Report statistics (EVAL-1.1) | `stratal.eval.report_stats` | `RowStatus`, `RejectionCounts`, `ScoreDistribution`, `Interval`, `ReviewedItem`, `AcceptanceRate`, `Acceptance`, `QUANTILES` and the five functions in §5 |
| Test helpers | `stratal.testing` | `fakes`: `singleton_split_groups`, `one_stratum`, `hashed_splits`, `lumped_rejection_counts`, `no_filter_rate`, `crude_score_distribution`, `vacuous_interval`, `overall_only_acceptance`; `fake_split` (registers split with fakes); `stagekit.split(data_dir, parent, units, *, config, previous, sink)` |

### Internal interfaces added in M8

| Interface | Where | Shape |
|---|---|---|
| Training config (RUN-1.1) | `stratal.train.config` | `ModelSpec(name: "mlp", "small_cnn" or "graph", graph?)` (since M9; `display`, `identity()`), `TrainConfig` (fields in §3.3), `TrainRequest(kind, dataset_version, config)`; torch-free |
| Reference models | `stratal.train.reference` | `build_model(spec, input_shape: (C, H, W), num_classes) -> nn.Module`, `parameter_count(model)` (PyTorch images only) |
| Image loader | `stratal.train.data` | `split_items(rows, blobs) -> SplitItems(classes, by_split)`, `LabelledImages(items)`, `EpochOrder(size, *, seed, epoch, start=0)`, `to_tensor(image)` |
| Checkpoints (RUN-1.3) | `stratal.train.checkpoint` | `Position(epoch, batches_done, global_step)`, `Checkpoint(position, config_hash, history)`, `save(folder, *, model, optimizer, scheduler, checkpoint)`, `latest(folder)`, `load(path) -> Loaded`, `restore(loaded, *, model, optimizer, scheduler)`, `remove_all(folder) -> bytes freed`, `checkpoint_dir(data_dir, job_id)` |
| Train stage | `stratal.train.stage` | `run_train(run: StageRun, units: TrainUnits = OWNER_UNITS) -> StageResult`; `TrainUnits(state, suggest, oom_error)` are the RUN-1.4 and RUN-1.5 units (owner units, written by the agent at the owner's request, ADR-0012) |
| Train jobs | `stratal.runtime.jobs` | `create_train_job(session, request) -> (Job, Enqueue)`; `create_job_from_plan` gained `queues` (a stage's queue by name) |
| Test helpers | `stratal.testing.labelled` | `class_folders(root, *, per_class, size)`, `labelled_split(runner, root, slug) -> version_id` |
| Registry rows (REG-1.1) | `stratal.registry.store` | `lineage_key(...) -> (lineage_id, key_hash)`, `ensure_lineage(session, ...)` (creates and locks), `base_of`, `run_version(session, job_id)`, `add_base`, `record_run(..., state)`, `record_checkpoint`, `drop_checkpoint`, `version_folder(lineage_id, version_id)`; views `list_lineages`, `lineage_detail`, `version_detail`; torch-free |
| Registry files | `stratal.train.weights` | `write(folder, model, meta) -> bytes` (temporary folder, then rename), `load_into(model, folder)`, `size(folder)` (PyTorch images only) |
| Backfill | `stratal.train.backfill` | `backfill(engine, data_dir, *, dry_run) -> list[report]` |
| Crash point | `stratal.runtime.context` | `STRATAL_TEST_CRASH_AT` also takes `<stage>:substage:<name>` |
| Keep rule (REG-2) | `stratal.registry.policy` | `plan_lineage(versions: [VersionFacts], keep_intermediate: int | None) -> LineagePlan(latest, delete, keep, blocked)`; pure |
| Retention | `stratal.registry.retention` | `RetentionValues`, `read_values`, `write_values`, `values_version`, `effective_keep`; `in_use(session, loaded, *, redis_ok) -> InUse`; `build_plan(session, *, use, lineage_ids, values)`; `run(engine, data_dir, emit, *, job_id, lineage_ids, dry_run, confirmed, loaded, redis_ok)`; `disk_report`, `last_run`, `decisions` |
| Lineage lock | `stratal.registry.store` | `lock_lineage(session, id)` (transaction), `holding_lineage(engine, id)` (a block): one Postgres advisory lock per lineage, taken by every writer of registry files and by retention |
| Registry jobs | `stratal.runtime.jobs` | `create_retention_job(session, *, lineage_ids, dry_run, confirmed, reason)`, `retention_after_run(session, job)`, `create_retrain_job(session, version_id)` |
| GPU lease for a long-lived holder | `stratal.runtime.gpu_lease` | `HeldLease(engine, *, holder)`: `try_acquire() -> bool` (never waits), `release()`, `held`; `gpu_lease(..., on_wait=)` calls `on_wait(holder)` while it waits, by default setting `stratal:gpu:wanted` |
| Inference service | `stratal.inference` | `holder.ModelHolder(*, engine, settings, bus, gpu, state_cls, on_leak)`: `predict(version_id, image)`, `predict_values(version_id, *, record=None, text=None)` (M10), `status()`, `tick()`, `run_checks(stop)`, `shutdown()`; `holder.decode_image`, `prepare_input`, `close_written`; `app.create_inference_app(holder, decode)` (`POST /predict`, `GET /status`); `python -m stratal.inference` |
| Inference holdings | `stratal.core.bus` | `set_inference_loaded({version_id, device, since} | None)`, `get_inference_loaded()`: Redis key `stratal:inference:loaded`, written by the inference service (ADR-0016 rule 6), read by retention |

### Internal interfaces added in M9

| Interface | Where | Shape |
|---|---|---|
| Shapes | `stratal.build.shapes` | `Shape(kind: image, vector, sequence or tokens, dims, vocab?)`, `SpecProblem(code, message)` |
| Layer specs (BUILD-1.1, 1.2) | `stratal.build.layers` | One Pydantic model per type, the `Layer` union on `type`; `apply(inputs: [Shape]) -> Applied(shape, parameters)`, `family()`; `LAYER_TYPES`, `head_parameters` |
| Graph specs (BUILD-1.4 to 1.6, 1.9) | `stratal.build.spec` | `GraphSpec`, `Node`; `validate_graph(spec, *, input_shape, output_size, optimizer, families) -> SpecReport`; `canonical`, `structure_hash`, `identity`, `family_of`, `input_order`, `chain(label, *layers)` |
| Locked ends (BUILD-1.3) | `stratal.build.io` | `version_io(engine, data_dir, version_id) -> VersionIo` (cached per version), `image_shape(rows)`, `image_classes(rows)`, `check_train_graph(...)` |
| Tabular input block | `stratal.build.tabular` | `TabularColumn(name, kind, categories, encoding, embedding_dim)`, `input_block(columns) -> (width, parameters)` |
| Compilation | `stratal.build.compile` | `compile_graph(spec, input_shape, num_classes) -> GraphModule` (PyTorch images only); called by `build_model` for `name: "graph"` |
| Templates (BUILD-1.7) | `stratal.build.store`, `stratal.build.templates` | `ensure_builtins`, `list_templates`, `get_template`, `create_template`, `update_template`, `delete_template`, `template_view`; `BUILTINS` |
| Dataset types (DATA-5.2, 5.5) | `stratal.data.dataset_types` | `TYPE_INFO[name] -> TypeInfo(input_kind, families, default_metrics, trainable, note)`; `TableSchema`, `Column`, `validate_tabular`, `validate_labelled_text` |
| From-scratch layers (BUILD-1.8) | `stratal.build.scratch` | `MultiHeadSelfAttention(embed_dim, num_heads, *, dropout, causal)`, `PositionalEncoding(d_model, *, max_len, dropout)`, `ScratchLSTMCell(input_size, hidden_size)` (PyTorch images only) |
| Unpickling tripwire (NFR-8) | `stratal.testing.no_pickle`, `stratal.runtime.child` | `forbid_unpickling(report=None)`, `plant_pickle`; `STRATAL_TEST_FORBID_UNPICKLING` (M8) |

## 7. Later phases

Listed so the phase-1 design leaves room. Each is specified at its phase kickoff.

| Phase | Interfaces |
|---|---|
| 2 | `hpo_study` and `eval` jobs; comparison views. Train jobs, pause and resume (§3.3), the registry and retention (§3.9), settings (§3.10) and the model builder (§3.11) are built |
| 3 | Chat with token streaming over SSE; RAG corpora and indexes; router decisions; image search |
| 4 | Image generation; LoRA jobs; export and import of remote bundles |
| 5 | Translation; full chat with manual and auto mode |
