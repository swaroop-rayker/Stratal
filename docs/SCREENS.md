# Stratal — Screens and flows

| | |
|---|---|
| Status | **v1, approved by the owner on 2026-11-15** (Gate 1, SPEC §9.4). Changes from here on are new versions and need approval |
| Builds on | [DESIGN-SYSTEM.md](DESIGN-SYSTEM.md) v1 (Gate 0, approved 2026-11-15) |
| Data sources | Endpoints are from [API.md](API.md). Phase-2 to phase-5 interfaces are not specified yet, so those screens are outlines and are detailed at their phase kickoff |
| Approvals | Logged in [design/APPROVALS.md](design/APPROVALS.md) |

This file lists every screen with its purpose, the requirements it serves, the data it shows and its main actions, then the core user flows. It decides **what is on each screen and how the screens connect**, not what they look like: layout and styling are Gate 2, one screen at a time, low-fi first.

## 1. Rules that apply to every screen

- **Four states.** Each screen's Gate 2 mockup shows empty, loading, error and populated, in dark and light (DESIGN-SYSTEM principle 8). The empty state named below for each screen is the one with an action.
- **Errors** show the problem's `code`, `detail` and `suggested_action` from the API's problem details, with a retry (ADR-0011).
- **Live data** arrives over SSE ([API.md §4](API.md#4-live-streams)). A screen that shows live data says so in its table below, and shows "Reconnecting…" while the stream is down; values older than three sample intervals are dimmed.
- **Deep links.** Every screen and every selected object has a URL, so a job, version or review can be pasted into a note and reopened.
- **Unbuilt screens are hidden**, not shown as dead links. The navigation grows as phases land.
- **IDs** (`job_…`, `ds_…`, `rev_…`) are always mono, always copyable, and always link to their screen.

## 2. Inventory

| ID | Screen | Route | Phase | Requirements | Built in |
|---|---|---|---|---|---|
| SCR-01 | App shell and navigation | (wraps all) | 1 | all; UI-2.1 slice, MON-1.4, MON-2.1 | M6 |
| SCR-02 | Datasets and versions | `/datasets`, `/datasets/:slug` | 1 | DATA-3.1, DATA-3.2, DATA-3.4, UI-2.1 | M6 |
| SCR-03 | Version detail and lineage | `/versions/:version_id` | 1 (UI-2.2 part in 2) | DATA-3.3, DATA-2.1, DATA-2.2, DATA-6.1, DATA-5.1, UI-2.1, UI-2.2 | M6 |
| SCR-04 | Live job view | `/jobs/:job_id` | 1 | MON-4.1, MON-4.2, MON-4.3, MON-1.1–1.5, MON-2.1, 2.3, 2.4, 2.5, MON-3.1; RUN-1.1/1.2 slice | M6 |
| SCR-05 | Labeling review | `/reviews/:review_id` | 1 | LABEL-1.5 | M6 |
| SCR-06 | Eval report | `/versions/:version_id/report` | 1 (EVAL-1.2 in 2) | EVAL-1.1, EVAL-1.2 | M6 |
| SCR-07 | Jobs list and queue control | `/jobs` | phase-1 slice, completed in 2 ([open point 1](#6-open-points-for-the-owner)) | RUN-1.1, RUN-1.2 | slice in M6; pause, resume and queue control in M8 |
| SCR-08 | Model builder canvas | `/models/build` | 2 | BUILD-1.1–1.9, UI-2.3, DATA-5.5 | M9 |
| SCR-09 | Training config and HPO setup | `/runs/new` | 2 | BUILD-1.2, BUILD-1.3, HPO-1.1–1.4, RUN-1.9, UI-2.3 | M9–M10 |
| SCR-10 | HPO results | `/studies/:study_id` | 2 | HPO-1.3, HPO-1.4, HPO-1.5 | M10 |
| SCR-11 | Model registry and comparison | `/models`, `/models/:lineage` | 2 (adapters in 4) | REG-1.1–1.4, REG-2.1–2.11, EVAL-1.2, EVAL-1.3, EVAL-1.4 | M8, M11 |
| SCR-12 | Chat (manual and auto mode) | `/chat` | 3, completed in 5 | UI-1.1, LLM-1.1, LLM-2.1, LLM-2.3, LLM-2.4, LLM-3.1, LLM-3.2, IMG-1.1, TRANS-1.1 | M12–M15 |
| SCR-13 | System monitor | `/system` | 2 | MON-2.1–2.7, MON-3.2, MON-3.3, MON-4.4, MON-4.5, RUN-1.6 | M11 |
| SCR-14 | Settings | `/settings` | 2 | UI-2.3; thresholds for MON-2.2, MON-2.6, RUN-1.6, RUN-1.7; REG-2.3, REG-2.4 | M8–M11 |

Gate 2 order follows SPEC §9.5: SCR-01, 02, 03, 04, 05, 06 first, then the SCR-07 slice.

### Navigation map

```text
┌ SCR-01 shell ───────────────────────────────────────────────────────────┐
│ Data ─── SCR-02 Datasets ──► SCR-03 Version ──► SCR-05 Review            │
│                                   │  ▲  │                                │
│                                   │  │  └──────► SCR-06 Eval report      │
│                                   ▼  │                                   │
│ Jobs ─── (SCR-07 Jobs list) ──► SCR-04 Live job ──► output version       │
│ Models ─ SCR-11 Registry ◄── SCR-10 HPO ◄── SCR-09 Run setup ◄── SCR-08  │
│ Chat ─── SCR-12                                                          │
│ System ─ SCR-13          Settings ─ SCR-14                               │
│                                                                          │
│ Always visible: active-job indicator → SCR-04 · resource strip · health  │
└──────────────────────────────────────────────────────────────────────────┘
```

Phase-1 navigation shows Data and Jobs only. Jobs opens the SCR-07 slice.

## 3. Phase-1 screens in detail

Each table says which endpoint feeds each piece of data. "Live" marks what updates without a reload.

### SCR-01 — App shell and navigation

**Purpose.** The frame every screen sits in: where am I, is anything running, is the machine healthy. Answers "is a job running and is it OK?" from any screen.

| Region | Shows | Source | Live |
|---|---|---|---|
| Sidebar | Navigation groups (Data, Jobs; more as phases land), keyboard hint | static | |
| Active-job indicator | Job ID, state pill, current stage, percent. Several running jobs show a count | `GET /jobs?state=running`, then `job` messages on `GET /stream` | yes |
| Resource strip | CPU %, RAM used / total, GPU %, VRAM used / total, GPU °C, in the fixed resource colours | `GET /telemetry/latest`, then `sample` messages on `GET /stream`; totals from `GET /system` | yes |
| Health dot | Postgres, Redis, data volume; a failed check names itself | `GET /health`, polled every 30 s | polled |
| Toasts | A job completed, failed or stalled, with a link to it; failures keep the suggested action | `job` messages on `GET /stream` | yes |

**Actions.** Navigate (`g d` datasets, `g j` jobs); open the active job; switch theme; `?` shortcut help.

**Empty / error.** No job running: the indicator reads "Idle" and the strip shows idle telemetry (10 s interval). API unreachable: a full-width banner with the health problem and "Retry"; the screen under it stays as last loaded.

### SCR-02 — Datasets and versions

**Purpose.** Everything Stratal holds, and the way in for new data. One dataset is selected at a time; its versions are listed beside it.

| Region | Shows | Source | Live |
|---|---|---|---|
| Dataset list | Slug, modality, latest version ID and stage, row count, accepted share, updated | `GET /datasets` | job completions refresh it |
| Versions of the selected dataset | Version ID, stage, parent ID, rows, counts by status (accepted / rejected / duplicate / filtered), manifest bytes, new blob bytes, created, creating job with its state | `GET /datasets/{slug}/versions` | as above |

**Actions.**

- **Ingest a folder** → the *Start pipeline* drawer (below). Creates the dataset if the slug is new (`POST /datasets`), then `POST /jobs`, then opens SCR-04.
- Open a version → SCR-03. Open its creating job → SCR-04. Copy an ID.
- Filter datasets by name (`/`); move through rows (`j` / `k`, `Enter`).

**Empty.** "No datasets yet. Put a folder under `.\sources` and ingest it." with the *Ingest a folder* button.

**Start pipeline drawer** (shared by SCR-02 and SCR-03, not a separate screen). Fields: dataset slug; source folder (a path under `/sources`) **or** the version to continue from (`from_version`); the last stage to run (`until_stage`); per-stage overrides generated from each stage's config schema (`GET /config/schemas/{name}`, the DESIGN-SYSTEM forms pattern). Validation errors land on their fields from `errors[].loc`.

### SCR-03 — Version detail and lineage

**Purpose.** Everything about one version: where it came from, what each stage decided and why, and the rows themselves. The starting point for review and reports.

| Region | Shows | Source | Live |
|---|---|---|---|
| Header | Version ID, dataset, stage, created, creating job (link), content hash, config hash | `GET /versions/{id}` | |
| Lineage | Ancestors back to raw, this version, descendants, as lineage nodes (stage, version ID, one fact such as rows accepted). Click a node to open it | `GET /versions/{id}/lineage` | |
| Counts | Rows by status; non-accepted rows by reason code and by the stage that decided them | `GET /versions/{id}/stats` | |
| Config | The resolved config of the stage that made this version, e.g. the preprocess maximum side (DATA-6.1) | `GET /versions/{id}` | |
| Rows | Thumbnail, row key, status, reason, size, format, dimensions (original and preprocessed), caption and CLIP score once labelled. Filter by status, reason, split. Virtualised | `GET /versions/{id}/rows`, `GET /blobs/{sha256}?w=` | |
| Duplicate group | For a duplicate row: the group's members side by side, the kept one marked, the kind (exact / near) and pHash distance | `GET /versions/{id}/rows` filtered by `dup_group_id` | |

**Actions.**

- **Continue the pipeline from this version** → *Start pipeline* drawer with `from_version` set.
- **Start a review** (sample size, optional seed) → `POST /versions/{id}/reviews` → SCR-05. Offered once the version has captions.
- **Build the eval report** → `POST /versions/{id}/reports` → SCR-06, which shows the build in progress (link to SCR-04) and then the report. Open the latest report if one exists. (Changed at SCR-06's G3, 2026-11-17: the mockup sent builds through SCR-04.)
- Open the creating job; copy IDs.

**Empty.** A version whose rows were all rejected shows the counts and reasons, and the row table opens filtered to `rejected`. **Phase 2 adds** the UI-2.2 distributions (raw vs cleaned, per stage) and the diff with another version (DATA-3.5).

### SCR-04 — Live job view

**Purpose.** What one job is doing right now, how far along it is, what it costs, and, when it fails, why and what to do. The layout in SPEC MON-4 is the starting point for its Gate 2 wireframe.

| Region | Shows | Source | Live |
|---|---|---|---|
| Header | Job ID, state pill, current stage, dataset, version, attempt, ETA. "Cancelling…" while `desired_state` is set | `GET /jobs/{id}`, then `job` messages on `GET /jobs/{id}/stream` | yes |
| Activity line | The latest activity message in plain language | `event` messages | yes |
| Progress | Bar in the state colour, percent, step / total, rate, ETA ("—" until known) | stage `step`, `total`, `throughput`, `eta_seconds` | yes |
| Timeline | One bar per stage: name, state, duration; pending stages dashed; a reused stage marked "cached". Click → drawer with that stage's events and resource charts | `GET /jobs/{id}` stages | yes |
| Resource tiles and charts | CPU, RAM, GPU (with the NVML-utilisation tooltip from MON-2), VRAM as NVML used beside PyTorch allocated and reserved (MON-2.4), temperature and power. Filterable by stage | `sample` messages; history from `GET /jobs/{id}/telemetry` | yes |
| Activity log | Time, level, stage, message; WARN and ERROR rows show *Why* and *Do*. Filter by level and stage. A scope switch **This job / All jobs** adds a job filter (MON-4.3) | `GET /jobs/{id}/events` or `GET /events`, then `event` messages | yes |
| Failure panel | Error `code`, reason, suggested action, the stage that failed, and what was kept | `job.error`, the ERROR event | yes |
| Summary (finished) | Duration per stage, peaks per stage (VRAM, RAM, temperature), output version | `GET /jobs/{id}` stage `peaks` | |

**Actions.** Cancel (destructive confirm: says finished stages are kept); retry a failed job (`POST /jobs/{id}/retry`; finished stages are reused); open the output version → SCR-03; pause log auto-scroll; filter the log (`/`).

**Empty / edge.** Queued: header and timeline with every stage pending, "Waiting for a cpu worker" or "…gpu worker". No GPU job running: the GPU tiles show idle values, not zero. A metric that cannot be read shows "unavailable" (principle 6). Stream lost: "Reconnecting…", and replay fills the gap on return (`Last-Event-ID`).

### SCR-05 — Labeling review

**Purpose.** A person checks a random sample of captioned rows and records accept or reject, quickly and from the keyboard. The result feeds the acceptance figure in the eval report.

| Region | Shows | Source | Live |
|---|---|---|---|
| Session bar | Review ID, version, sample size, seed, progress (decided / total), accepted and rejected so far | `GET /reviews/{id}` | updates on each decision |
| Current item | The preprocessed image large; caption; caption model and revision; CLIP score; row key | `GET /reviews/{id}`, `GET /blobs/{sha256}` | |
| Decision | Accept / Reject, optional note | `PUT /reviews/{id}/items/{position}` | |
| Item strip | Every position with its decision (✔ / ✖ / undecided), to jump back | `GET /reviews/{id}` | |

**Actions.** `a` accept, `r` reject, `n` add a note, `j` / `k` previous and next, change an earlier decision; **Close session** (confirm if items are undecided) → `POST /reviews/{id}/close` → acceptance summary with a link to *Build the eval report*.

**Empty / edge.** A version without captions cannot start a review (the action on SCR-03 is disabled and says why). A closed session opens read-only with its summary.

### SCR-06 — Eval report

**Purpose.** The phase-1 report for one version, the evidence that the dataset is usable.

| Region | Shows | Source | Live |
|---|---|---|---|
| Report header | Version, report ID, review session used, building job, created | `GET /versions/{id}/reports/latest?kind=phase1_dataset` or `GET /reports/{id}` | |
| Rejections per stage | Count and share per stage and reason, as a table and a bar chart | report body | |
| CLIP score distribution | Histogram with the filter threshold drawn on it | report body | |
| Filter rate | Share of labelled rows filtered, with the threshold | report body | |
| Manual-review acceptance | Accepted / reviewed, the rate, and the 95% Wilson interval drawn as a range | report body | |

**Actions.** Rebuild the report (`POST /versions/{id}/reports`); open the version, the review session or the building job. Every chart has a text table beside or under it (accessibility rule).

**Empty.** "No report for this version yet." with *Build report*; if no review is closed, the report is built without the acceptance part and says so. **Phase 2 adds** the task-metric sets of EVAL-1.2 for model evaluations.

## 4. Later screens (outline)

Detailed at each phase kickoff, when their interfaces are specified. Listed now so the shell and the phase-1 screens leave room.

| ID | Purpose | Key data | Main actions |
|---|---|---|---|
| SCR-07 | Every job, its state and its place in the queue | Job ID, kind, state pill, dataset or model, current stage, progress, attempt, created, duration; filters by state and kind | Open; cancel; retry; pause and resume (RUN-1.2, phase 2) |
| SCR-08 | Build a model by stacking layers | Layer nodes with parameters, inferred output shapes and parameter counts; locked input and output from the dataset type (BUILD-1.3); live total parameters and memory estimate (BUILD-1.5); cross-field errors in words (BUILD-1.6) | Add, configure, reorder, remove layers; save and load templates (BUILD-1.7); continue to SCR-09 |
| SCR-09 | Configure a training run or an HPO study | Forms generated from the config schemas (UI-2.3); device choice (RUN-1.9); tunable parameters with ranges or choices (HPO-1.1); trial count and time budget (HPO-1.2) | Start run or study → SCR-04 |
| SCR-10 | Read a finished or running study | Trial table, pruned trials with their step (HPO-1.3), best config, parameter importance (HPO-1.5), the single test evaluation (HPO-1.4) | Open the best model in SCR-11; start a run from the best config |
| SCR-11 | Every trained model, its lineage, its weights and its metrics | Versions with config, dataset version, metrics, state (base / partial / trained), weights status; disk use per lineage (REG-2.11); side-by-side comparison of ≥3 models on one split (EVAL-1.3) | Pin and unpin (REG-2.5); retention dry-run and run with confirmation (REG-2.9, 2.10); re-train a version whose weights were deleted (REG-2.7); try `/predict` (REG-1.4) |
| SCR-12 | Talk to the models | Chat bubbles; assistant footer with route, latency and speed; citations for RAG answers (LLM-2.1); abstentions (LLM-2.3); generated images and translations inline | Pick a model or Auto (router); send; stop; open a citation |
| SCR-13 | The machine over time, beyond one job | Resource history, alerts with the action taken (MON-2.6, 2.7), bottleneck diagnostics (MON-3.2), load and unload events with VRAM before and after (MON-3.3), two runs overlaid (MON-4.4), trends across past jobs (MON-4.5) | Pick a time range; pick jobs to compare; open a job |
| SCR-14 | Defaults and thresholds | Sampling intervals, alert thresholds, temperature limit, stall timeout, retention defaults and overrides, theme — all as schema-generated forms | Edit and save, with validation from the API |

**Not yet placed.** RAG corpus and index management with hit@k results (LLM-2.1, LLM-2.2) and remote-offload bundles (IMG-1.4) have no screen in SPEC §9.4. I propose deciding where they go at the phase-3 and phase-4 kickoffs (a new screen or a tab of SCR-11) rather than now.

## 5. Core flows

Each step names the screen, what the person does, and the call behind it. Flows 1 to 3 are phase 1; 4 and 5 are later and stay at this level until their phases.

### Flow 1 — Raw folder → versioned dataset (phase 1)

| # | Screen | The person… | Behind it |
|---|---|---|---|
| 1 | SCR-02 | clicks *Ingest a folder*, enters `coco10k` and `/sources/coco10k`, leaves the stages at their defaults | `POST /datasets`, `POST /jobs` (`kind: pipeline`) → `202` job |
| 2 | SCR-04 | watches the timeline: ingest → clean → preprocess (→ label, filter from M4); activity, progress, ETA, resources | `GET /jobs/{id}/stream` |
| 3 | SCR-04 | sees *completed*, the summary with peaks, and the output version | `job` snapshot |
| 4 | SCR-03 | opens the version: lineage back to raw, counts by reason, the rows, a near-duplicate group | `GET /versions/{id}`, `/lineage`, `/stats`, `/rows` |
| 5 | SCR-03 | later continues from it with a new preprocess maximum, which gives a new version ID | *Start pipeline* with `from_version` |

### Flow 2 — Version → review → eval report (phase 1)

| # | Screen | The person… | Behind it |
|---|---|---|---|
| 1 | SCR-03 | opens a labelled and filtered version, clicks *Start a review*, sample 50 | `POST /versions/{id}/reviews` |
| 2 | SCR-05 | decides 50 items with `a` and `r`, fixes one earlier decision, closes the session | `PUT …/items/{position}`, `POST /reviews/{id}/close` |
| 3 | SCR-05 | clicks *Build the eval report* in the summary, and lands on SCR-06 while it builds | `POST /versions/{id}/reports` → `202` job, followed on `GET /stream` |
| 4 | SCR-06 | reads rejections per stage, the CLIP distribution with the threshold, the filter rate, acceptance with its interval | `GET /versions/{id}/reports/latest` |

### Flow 3 — Job failure → diagnose → retry (phase 1; resume in phase 2)

| # | Screen | The person… | Behind it |
|---|---|---|---|
| 1 | any (SCR-01) | gets a toast: *job_0142 failed in label: gpu_oom*, with a link | `job` message on `GET /stream` |
| 2 | SCR-04 | reads the failure panel: code, reason, suggested action ("reduce batch size from 32 → 16"), and that ingest, clean and preprocess are kept | `job.error`, ERROR event |
| 3 | SCR-04 | opens the failed stage in the timeline: its events and the VRAM curve up to the failure | drawer: `GET /jobs/{id}/events?stage=label`, `/telemetry?stage=label` |
| 4 | SCR-04 | clicks *Retry*; finished stages show "cached", the failed stage runs again | `POST /jobs/{id}/retry` |

In phase 1 a retry reuses the stage's own config; changing a value means starting a new run from the last good version (*Start pipeline* with `from_version`, also reused). Pause and resume from a checkpoint, and stalled-job handling, join this flow in phase 2 (RUN-1.2, RUN-1.3, RUN-1.7).

### Flow 4 — Dataset → build model → train → evaluate (phase 2)

SCR-03 (pick a version) → SCR-08 (stack layers; input and output locked from the dataset type; invalid stacks blocked) → SCR-09 (training config, device, optionally an HPO study) → SCR-04 (live run: loss, metrics, VRAM, checkpoints) → SCR-10 (if HPO: best trial, one test evaluation) → SCR-11 (the registered version with test metrics; compare with others).

### Flow 5 — Chat request → router → model → answer (phases 3–5)

SCR-12: the person types a request in Auto mode → the router classifies it and picks LLM, RAG, image generation, captioner or translator (LLM-3.1) → the answer streams back with its route, latency and, for RAG, citations → if the chosen model fails, the fallback answer or a clear error appears with the reason (LLM-3.2). In manual mode the person picks the model and the router is skipped.

## 6. Open points for the owner

1. **Finding jobs in phase 1.** SCR-07 is tagged phase 2 (RUN-1.1, RUN-1.2), but phase 1 already has job states, cancel and retry (CL-04). Without SCR-07 a past job is reached only from a version's "creating job" link or the shell's active-job indicator. Two options:
   - **(a)** Build a phase-1 slice of SCR-07 in M6 (list, filters, open, cancel, retry). This adds one screen to M6 and to Gate 2. *Recommended.*
   - **(b)** Keep M6 at six screens and add a "recent jobs" list to the shell's job indicator.

   **Decided 2026-11-15 by the owner: (a).** The slice shows the list with state and kind filters, opens a job in SCR-04, and offers cancel and retry. The cross-job activity log stays in SCR-04 (point 2), as approved.
2. **Where the cross-job activity log lives.** MON-4.3 needs a job filter. I put it in SCR-04 as a *This job / All jobs* switch, so it is covered by a phase-1 screen. If you choose 1(a), it could move to SCR-07 instead.
3. **`GET /system` becomes needed.** The shell's "VRAM 3.9 / 6 GB" needs the totals. It is an M2 loose end; I propose building it in M3 as plumbing so M6 is not blocked.
4. **The *Start pipeline* drawer** is the only way to start work in phase 1 and is not in SPEC's inventory. I placed it as a shared drawer of SCR-02 and SCR-03, mocked up with SCR-02 in Gate 2. Say if you want it as its own screen.

## Revision history

| Version | Date | Changes |
|---|---|---|
| v1 | 2026-11-15 | First version, for Gate 1 review. Approved the same day; open point 1 then decided by the owner as option (a), recorded in place |
