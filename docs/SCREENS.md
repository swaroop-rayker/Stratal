# Stratal — Screens and flows

| | |
|---|---|
| Status | **v3, approved by the owner on 2026-11-20** (Gate 1 revision: choosing the model kind after the split — the *Train a model* drawer, SCR-03, SCR-08, SCR-09, SCR-11, Flow 4 steps 1–3; open point 5). v2 approved by the owner on 2026-11-17 (phase-2 screens detailed in §3a, Flow 4 in steps, Flow 3's phase-2 part). v1 approved 2026-11-15. Changes from here on are new versions and need approval |
| Builds on | [DESIGN-SYSTEM.md](DESIGN-SYSTEM.md) v1 (Gate 0, approved 2026-11-15) |
| Data sources | Endpoints are from [API.md](API.md). Phase-2 screens name the endpoints their milestone adds (*planned*). Phase-3 to phase-5 interfaces are not specified yet, so those screens are outlines and are detailed at their phase kickoff |
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
| SCR-03 | Version detail and lineage | `/versions/:version_id` | 1 (UI-2.2 part in 2) | DATA-3.3, DATA-2.1, DATA-2.2, DATA-6.1, DATA-5.1, UI-2.1, UI-2.2; DATA-3.5, DATA-2.3 | M6; v2 additions in M11 |
| SCR-04 | Live job view | `/jobs/:job_id` | 1 | MON-4.1, MON-4.2, MON-4.3, MON-1.1–1.5, MON-2.1, 2.3, 2.4, 2.5, MON-3.1; RUN-1.1/1.2 slice | M6 |
| SCR-05 | Labeling review | `/reviews/:review_id` | 1 | LABEL-1.5 | M6 |
| SCR-06 | Eval report | `/versions/:version_id/report` | 1 (EVAL-1.2 in 2) | EVAL-1.1, EVAL-1.2 | M6 |
| SCR-07 | Jobs list and queue control | `/jobs` | phase-1 slice, completed in 2 ([open point 1](#6-open-points-for-the-owner)) | RUN-1.1, RUN-1.2 | slice in M6; pause, resume and queue control in M8 |
| SCR-08 | Model builder canvas | `/models/build` | 2 | BUILD-1.1–1.9, UI-2.3, DATA-5.5 | M9 |
| SCR-09 | Training config and HPO setup | `/runs/new` | 2 | BUILD-1.2, BUILD-1.3, HPO-1.1–1.4, ML-1.1, ML-1.4, RUN-1.9, UI-2.3 | M9–M10 |
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

**Into training (v3).** SCR-11 and SCR-03 open the *Train a model* drawer; it leads to SCR-08 (a network) or straight to SCR-09 (a classical model). *Train again…* (SCR-11), *Start a run from the best configuration* (SCR-10) and *Start again with N* (SCR-04) keep going straight to SCR-09.

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
- **Train a model…** (v3, M11) → the *Train a model* drawer with this version as step 1, opened at step 2. Offered on a version a model trains on: a `split` version, or a `tokenize` version for labelled text; on any other stage it is disabled and says "Training needs a split version (a tokenize version for text)".
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

## 3a. Phase-2 screens in detail (v2)

Detailed at phase 2's kickoff (2026-11-17) from the approved plan (MILESTONES.md, M8–M11) and ADR-0014 to ADR-0016. Endpoints marked *planned* are added to API.md by the milestone named; their exact shape is settled there, and a G2 mockup shows them as planned. Each screen still goes through Gate 2 (low-fi, then hi-fi) before it is built.

### SCR-04 v2 — Live job view, training runs (M8)

**Purpose.** Follow a training run the way a pipeline run is followed: what it is doing, how far it is, how well it is learning, and what it costs.

| Region | Shows | Source | Live |
|---|---|---|---|
| Header | Job, state pill, model and dataset version, device and why ("auto: GPU 12 ms/step, CPU 41 ms/step"), attempt | `GET /jobs/{id}` (training fields *planned*, M8) | yes |
| Progress | Epoch e/E and step s/S with a bar, samples/s, ETA | job stream progress events | yes |
| Training charts | Loss (train per step, validation per epoch), accuracy, learning rate, gradient norm, step time; each with a table view | job stream event metrics; `GET /jobs/{id}/events` for the history | yes |
| Resource tiles | As phase 1: CPU, RAM, GPU, VRAM (allocated vs reserved), temperature | job stream samples | yes |
| Timeline | Epochs as segments, checkpoints as markers (only the latest kept), pauses as gaps with their reason | events | yes |
| Activity log | As phase 1, with checkpoint, pause, resume and out-of-memory events | events | yes |
| State banners | Paused: by whom (user or heat, "GPU 84 °C, resumes below 75 °C"); stalled: "no progress for 5 min" with the last activity | job and events | yes |

**Actions.** Pause, resume, cancel (RUN-1.2; `POST /jobs/{id}/pause`, `/resume` *planned*, M8); open the registered version (SCR-11) when the run completes. An out-of-memory failure shows MON-1.3's "reduce batch size from 32 → 16" with *Start again with 16*.

**Empty.** Not applicable (a job exists). Before the first step: "Preparing data…" or "Benchmarking devices…" with the activity message.

### SCR-07 v2 — Jobs list (M8)

**Changes from the phase-1 slice.** *Pause* on running jobs and *Resume* on paused ones, as row actions beside *Cancel* (RUN-1.2, OC-8: pipelines too). Paused and stalled jobs count as Active, with the pause reason in "Now / outcome". The kind filter gains `train` and `retention`; a training row's Dataset column reads "model on dataset version". Everything else is the approved slice.

### *Train a model* drawer (v3, M11; shared by SCR-11 and SCR-03)

**Purpose.** Choose what to train on first, then the kind of model that data allows, so a classical model never needs a network built and thrown away (ML-1.1, BUILD-1.3, UI-2.1). A drawer, not a separate screen, like the *Start pipeline* drawer.

| Step | Shows | Source |
|---|---|---|
| 1. Data | Dataset select, then the versions a model trains on, newest first, with row counts: `split` versions, and `tokenize` versions for labelled text (which trains on its tokens and vocabulary, not on its split). Once chosen: the version's type (e.g. *labelled images*, *tabular*), its task (classification with N classes, or regression) and its input → output (e.g. `image 3×32×32 → 10 classes`, `13 features → 3 classes`) | `GET /datasets`, `GET /datasets/{slug}/versions`, `GET /versions/{id}` (`dtype`); the version's ends from `POST /model-specs/validate`, as SCR-08 and SCR-09 read them today. No API change |
| 2. Kind | One line per kind, each a radio with what it leads to. **Network**: build one or start from a template → SCR-08; offered for every trainable type. **Classical**: an estimator from scikit-learn → SCR-09 in classical mode, no builder; offered for tables only. A kind the split does not allow stays in the list, greyed and not selectable, with its reason in words beside it ("Classical models train on tables"). The primary button names where it goes: *Open the builder* or *Set up the run* | `GET /dataset-types` (`trainable`, `families`) |

**Behaviour.** Opened from SCR-11 it starts at step 1, empty. Opened from SCR-03 (or from SCR-09, below) step 1 is filled with that version and the drawer opens at step 2, with step 1 shown as one line and *Change* to go back. Network goes to `/models/build?version=…`; Classical to `/runs/new?version=…&kind=classical`. Nothing is created until a run is started on SCR-09.

**States.** *Loading*: skeleton lines for the version's type and the kinds. *Empty*: no dataset yet → "No datasets yet." with a link to SCR-02; a dataset with no split version → "No split version of wine yet. Run the pipeline to its split stage." with *Continue the pipeline* (the *Start pipeline* drawer). *Error*: the version or the types cannot be loaded → the error state with *Retry* inside the drawer, step 1 kept. *Not trainable*: a split whose type nothing can train (`trainable = false`) shows both kinds greyed with the type's own note as the reason, and the primary button stays disabled.

### SCR-08 — Model builder canvas (M9)

**Purpose.** Build a model by stacking layers, with mistakes caught before training starts.

| Region | Shows | Source | Live |
|---|---|---|---|
| Dataset and type | The dataset version and its type; input shape and output size, locked (BUILD-1.3); only compatible model families offered (DATA-5.5). v3: the two selects are filled from the drawer's step 1 (`?version=`) and can still be changed | `GET /versions/{id}`; type registry *planned*, M9 | |
| Layer palette | Linear, Conv2d, MaxPool, BatchNorm, Dropout, Flatten, Embedding, RNN, LSTM, GRU, attention block, activations (BUILD-1.1) | spec schema *planned*, M9 | |
| Canvas | Layer nodes in order; edges for skip connections (BUILD-1.9); each node shows its output shape and parameter count (BUILD-1.4) | `POST /model-specs/validate` *planned*, M9 | on every edit |
| Layer inspector | The selected layer's fields, generated from its schema with ranges and defaults (BUILD-1.2, UI-2.3) | spec schema | |
| Summary bar | Total parameters; memory estimate split into weights, gradients and optimizer state for the chosen optimizer (BUILD-1.5) | validate response | on every edit |
| Problems | Cross-field errors in words, each linked to its node (BUILD-1.6) | validate response | on every edit |

**Actions.** Add, configure, reorder and remove layers; save and load templates (BUILD-1.7); *Continue to training* (SCR-09 with the kind *Network*), disabled while there are problems. The canvas has an equivalent list view, so every action works from the keyboard (accessibility rule).

**Empty.** "Start from a template, or add a first layer." with the template list.

### SCR-09 — Training run and HPO setup (M9; HPO part M10)

**Purpose.** Configure one training run, or a study of many, and start it.

| Region | Shows | Source | Live |
|---|---|---|---|
| Model | v3: the kind arrives chosen (from the drawer, from SCR-08, or from the job or study it starts from) and shows as a label, not a switch, with *Change…*, which opens the drawer at step 2 over this page; when a network spec would be left behind, step 2 says so before going on ("The network from job_0067 will not be used. A builder draft stays in the builder."). *Network*: the spec from SCR-08 (or a template), its parameters and memory estimate. *Classical* (M10): an estimator picked here, filtered by the dataset's task, with its parameter form and cross-field rules (ML-1.1, ML-1.4; ADR-0019 rule 15; added with the owner's approval of the study part's low-fi wireframe, 2026-11-19) | spec; `GET /ml/estimators/schema` *planned*, M10 | |
| Data | The split dataset version; per-split row counts | `GET /versions/{id}` | |
| Training config | Epochs, batch size, optimizer, learning rate and schedule, seed: a form from the config schema (UI-2.3) | `GET /config/schemas/train` *planned*, M8 | |
| Start from | The lineage's base, or a chosen version (ADR-0015) | `GET /models/{lineage}` *planned*, M8 | |
| Device | Auto, CPU or GPU, with what auto will do (RUN-1.9) | | |
| VRAM check | The estimate against the cap, before starting | validate response | |
| Study (M10) | A *tunable* toggle on each field with its range or choices and scale (HPO-1.1), model parameters included; trial count and time budget (HPO-1.2); pruning (HPO-1.3); the metric to optimise on validation, or cross-validation folds for a classical model (HPO-1.4); the VRAM check for the worst case in the space | study schema *planned*, M10 | |

**Actions.** *Start run* (`POST /jobs` with kind `train`) → SCR-04; *Start study* (*planned*, M10) → SCR-10.

**Entry (v3).** `/runs/new?version=…&kind=network|classical` opens the page with that kind. `?from=job_…` and `?study=…` take the kind of that run or study. `/runs/new?version=…` without a kind opens the drawer at step 2 for that version; `/runs/new` with nothing opens it at step 1.

**Empty.** The drawer closed without a choice, or *Network* with no spec yet: "Choose what to train first." with *Choose…* (the drawer) and, for a network, *Open the builder*. It replaces v2's "Build or pick a model first."

### SCR-10 — HPO results (M10)

**Purpose.** Read a study, running or finished, and act on its best configuration.

| Region | Shows | Source | Live |
|---|---|---|---|
| Header | Study, objective and metric, trials done of the limit, time used of the budget, state | `GET /studies/{id}` *planned*, M10 | yes |
| Trial table | Parameters, value, state (complete, pruned at step n, failed), duration; sortable, virtualised (HPO-1.3) | study trials | yes |
| Best configuration | Its parameters and validation value | study | yes |
| Parameter importance | Chart with a table view (HPO-1.5) | study | at the end |
| Optimisation history | Value per trial, best so far | study | yes |
| Test evaluation | The single test result of the best configuration, labelled "computed once" (HPO-1.4) | study | at the end |

**Actions.** Open the best model (SCR-11); *Start a run from the best configuration* (prefills SCR-09, straight there with the study's kind, v3); pause, resume and cancel a running study (they act on its current trial). A trial's row opens a drawer with its values per step against the median the pruner used.

**Reached from** a study job on SCR-07, a trial's header on SCR-04 and a registered version on SCR-11; there is no list of studies (approved with the low-fi wireframe, 2026-11-19). SCR-07 gains a *study* kind with its trials grouped under it, and SCR-04 "Trial n of st_…" for a trial: drawn as addenda at their Gate 3.

**Empty.** A study with no finished trial: "No trial has finished yet." with the first trial's job link.

### SCR-11 — Model registry and comparison (M8; comparison M11)

**Purpose.** Every trained model: where it came from, how good it is, what its weights cost on disk, and what retention will do.

| Region | Shows | Source | Live |
|---|---|---|---|
| Lineages | Name, task, versions, latest metrics, disk use; filter by state (REG-1.2) | `GET /models` *planned*, M8 | |
| Versions of a lineage | Version, state (base, partial, trained), weights status, dataset version, config hash, key metrics, created, pinned | `GET /models/{lineage}` *planned*, M8 | |
| Version detail (drawer) | Full resolved config, dataset version, job, parent, metrics (EVAL-1.2 sets once written) (REG-1.1) | `GET /model-versions/{id}` *planned*, M8 | |
| Disk use | Base, latest, intermediate, pinned and checkpoints for the lineage, against the bytes on disk (REG-2.11) | registry *planned*, M8 | |
| Retention | The lineage's `keep_intermediate` (or the global one), a dry-run preview of what would go and the bytes, and the last run's deletions (REG-2.9) | retention plan *planned*, M8 | |
| Try it | A sample input, the prediction, and the device that served it (REG-1.4, ADR-0016) | `POST /models/{version}/predict` *planned*, M8 | |
| Comparison (M11) | Three or more versions on one test split, side by side, with per-class detail (EVAL-1.3) | *planned*, M11 | |

**Actions.** Pin and unpin (REG-2.5); run retention after its preview, with a confirmation that lists the exact versions and sizes when N is lowered (REG-2.10); *Re-train* a version whose weights were deleted (REG-2.7); open the job, the dataset version or the parent. v3: *Train a model…* opens the *Train a model* drawer at step 1 (it replaces *Build a model…*); *Train again…* on a lineage still opens SCR-09 straight away with that lineage's last run.

**Empty.** "No models yet." with *Train a model…* (the drawer).

### SCR-13 — System monitor (M11)

**Purpose.** The machine over time, beyond any one job: where the resources went, what alerted, and why things were slow.

| Region | Shows | Source | Live |
|---|---|---|---|
| Resource history | CPU, RAM, GPU, VRAM and temperature for a chosen range; one-minute averages beyond 7 days (NFR-12), marked as such | telemetry *planned*, M11 | yes |
| Alerts | Each alert with the action taken (pause, fail, smaller batch) (MON-2.6, MON-2.7) | events | yes |
| Diagnostics | Expected against observed bottleneck per running stage, in words (MON-3.2) | *planned*, M11 | yes |
| Load and unload | Each model load and unload with VRAM before and after (MON-3.3) | events | yes |
| Compare two jobs | Their resource curves overlaid (MON-4.4) | telemetry | |
| History | Throughput and peaks across the last N jobs (MON-4.5, P2) | job summaries | |

**Actions.** Pick a range; pick two jobs to compare; open any job. NVML's "GPU utilisation" keeps its tooltip (MON-2 interpretation note).

**Empty.** Before any sample: "No telemetry yet: the monitor has not reported." with the monitor's health.

### SCR-14 — Settings (M8; thresholds M11)

**Purpose.** The defaults and thresholds the owner can change, with validation from the API.

| Section | Settings | Requirement |
|---|---|---|
| Training | Checkpoint interval (5 min), temperature pause and resume (83 °C, 75 °C), stall timeout (300 s) | RUN-1.3, RUN-1.6, RUN-1.7 (OC-3) |
| Retention | Global `keep_intermediate`; per-lineage overrides; lowering N asks first, with the versions and sizes | REG-2.3, REG-2.4, REG-2.10 (OC-9) |
| Monitoring (M11) | Sampling intervals; alert thresholds (VRAM 90 % of the card, RAM 85 % of the VM) | MON-2.2, MON-2.6 (OC-7) |
| Appearance | Theme | – |

Every section is a form generated from its schema (UI-2.3), saved with `PUT /settings` (*planned*, M8).

### SCR-03 v2 additions (M11)

Raw against cleaned distributions and the rejection reasons per stage (UI-2.2); the difference from another version: rows added, removed and changed (DATA-3.5, P2); semantic-duplicate clusters, shown like duplicate groups (DATA-2.3, ADR-0013).

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

**Phase 2 (v2).** A training job that runs out of memory fails with a suggested batch size and *Start again with 16* on SCR-04, which opens SCR-09 prefilled, since a training retry with the same batch would fail the same way. A job paused for heat resumes by itself below 75 °C; one paused by the person waits for *Resume*. A job with no progress for 5 minutes turns `stalled`: SCR-04's banner shows its last activity, and SCR-13 (M11) says which resource it is waiting on. A crash or a lost worker resumes from the last checkpoint, at most 5 minutes back.

### Flow 4 — Dataset → build model → train → evaluate (phase 2)

SCR-03 (pick a split version) → *Train a model* drawer (the kinds that split allows) → SCR-08 for a network (stack layers; input and output locked from the dataset type; invalid stacks blocked; a classical model skips it) → SCR-09 (training config, device, optionally an HPO study) → SCR-04 (live run: loss, metrics, VRAM, checkpoints) → SCR-10 (if HPO: best trial, one test evaluation) → SCR-11 (the registered version with test metrics; compare with others).

Step by step (v2; steps 1–3 rewritten in v3), as the phase-2 acceptance runs it on CIFAR-10:

| # | Screen | The person… | Behind it |
|---|---|---|---|
| 1 | SCR-02, SCR-03 → drawer | opens the split version of the CIFAR-10 dataset (`ds_f8e67211dd94`, labelled images) and clicks *Train a model…*; the drawer opens at step 2 with the version filled: *Network* is offered and *Classical* is greyed with "Classical models train on tables"; picks *Network*, then *Open the builder* | `GET /versions/{id}`, `GET /dataset-types` |
| 2 | SCR-08 | finds the dataset selects already set; stacks convolution, pooling and linear layers; input is locked at 3×32×32 and output at 10; a mismatched layer is flagged before going on | `POST /model-specs/validate` |
| 3 | SCR-09 | sees *Network* as the chosen kind; sets epochs, batch size and learning rate, leaves device on auto, clicks *Start run* | `POST /jobs` (kind `train`) → `202` |
| 4 | SCR-04 | watches loss and accuracy; pauses and resumes once | job stream; `POST /jobs/{id}/pause`, `/resume` |
| 5 | SCR-11 | finds the trained version, with its test metrics and the dataset version it came from | `GET /models/{lineage}` |
| 6 | SCR-09 → SCR-10 (M10) | tunes the learning rate in a study and reads the single test evaluation of the best trial | studies |
| 7 | SCR-11 (M11) | compares three versions on the same test split | comparison |

**A table (v3).** On SCR-11 the person clicks *Train a model…*, picks the wine dataset's split (`ds_6448d354c9c6`, tabular, classification with 3 classes), then *Classical* and *Set up the run*; SCR-09 opens in classical mode with an estimator to choose. SCR-08 is never opened.

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
5. **Choosing the model kind after the split (v3, open for Gate 1).**
   - **(a) Drawer or screen.** A drawer shared by SCR-11 and SCR-03, like *Start pipeline*. *Recommended*: two short steps, nothing to save, and it can open over SCR-09 for *Change…*. A screen (`/train/new`) would add an address, which `/runs/new?version=` already gives.
   - **(b) The button label.** *Train a model…* (*recommended*: it names the goal, not the builder). Alternatives: *New model…*, *Start training…*.
   - **(c) From SCR-03,** open at step 2 with the version filled and *Change* back to step 1 (*recommended*), or always at step 1, prefilled.
   - **(d) Templates.** *Network* always goes through SCR-08, where templates are loaded (*recommended*: one way in). The alternative is a third line, *Network from a template*, straight to SCR-09.
   - **(e) SCR-08's dataset selects** stay changeable after being prefilled (*recommended*, as with `?version=` today), or are locked to step 1's choice.
   - **(f) *Change…* on SCR-09** reopens the drawer at step 2 and warns only when a network spec would be left behind (*recommended*). A builder draft stays in session storage either way, so the warning says whether it stays.
   - **(g) Gate order with fine-tuning (RUN-1.10, K11).** A low-fi of SCR-09 v2's fine-tuning form already exists, not yet approved (`design/screens/SCR-09v2-lofi_v1.html`, commit `7700fa3`). I propose one SCR-09 v2 gate covering both: this entry as its own low-fi file (`SCR-09v2-entry-lofi_v1.html`), then one hi-fi mockup with both. The alternative is this entry first, fine-tuning after.

   **Decided 2026-11-20 by the owner: the recommended option in each of (a) to (g)** ("Approved"). A drawer; *Train a model…*; from SCR-03 at step 2; *Network* always through SCR-08; SCR-08's selects stay changeable; *Change…* warns only when a spec would be left behind; one SCR-09 v2 gate, the entry's low-fi reviewed first.

## Revision history

| Version | Date | Changes |
|---|---|---|
| v1 | 2026-11-15 | First version, for Gate 1 review. Approved the same day; open point 1 then decided by the owner as option (a), recorded in place |
| v2 | 2026-11-17 | Phase-2 kickoff: §3a details SCR-04 v2 (training runs), SCR-07 v2 (pause, resume), SCR-08 to SCR-11, SCR-13, SCR-14 and SCR-03's M11 additions, from the approved phase-2 plan and ADR-0014 to ADR-0016; Flow 4 in steps; Flow 3's phase-2 part. Approved by the owner the same day |
| v3 | 2026-11-20 | Choosing the model kind after the split (ML-1.1, BUILD-1.3, UI-2.1; the owner's request of 2026-11-20): the *Train a model* drawer (step 1 the split version with its type and task, step 2 the kinds it allows, *Classical* greyed with its reason off tables); SCR-03 and SCR-11 open it, replacing *Build a model…*; SCR-08's dataset selects filled from step 1; SCR-09 receives the chosen kind with *Change…* and entry rules, its empty state reworded; *Train again…*, SCR-10's and SCR-04's starts unchanged; Flow 4's summary and steps 1–3 rewritten, with a table variant; open point 5. Approved by the owner the same day, with the recommended option in each of point 5's choices. Corrected with the approval of the entry's low-fi wireframe (same day): step 1 and SCR-03's button take `tokenize` versions for labelled text as well as `split` versions |
