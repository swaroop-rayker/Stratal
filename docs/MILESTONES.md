# Stratal — Milestones

| | |
|---|---|
| Status | M0 merged 2026-11-14. M1 merged 2026-11-14. M2 merged 2026-11-15. M3 merged 2026-11-15 (DATA-2.1 and DATA-2.2 written by the agent at the owner's request; OC-5 threshold 8). M4 in progress: dataset chosen (COCO 2017, 10k subset); downloads waiting for approval. G0 and G1 approved 2026-11-15; G2 passed for every phase-1 screen (SCR-01 to SCR-06, SCR-07 slice) 2026-11-15 |
| Planning style | Phase 1 (M0–M7) is planned in detail. Phases 2–5 (M8–M15) are outlined and get detailed, with the owner's approval, when the previous phase closes |

## Rules

- A box is ticked only when its test passes or its evidence exists. A requirement is never marked done without a passing `test_<ID>_*` (SPEC §9.1).
- Every milestone ends with: tests pass, docs updated, demo shown, owner's OK to merge its branch into `main`.
- After each milestone: tick this file and update affected docs (SPEC §8.4).
- Sizes are relative (S, M, L), not durations.
- 👤 marks work the owner implements ([ADR-0012](decisions/ADR-0012-working-model-and-git-workflow.md)).

## Overview

| | Milestone | Phase | Size | Status |
|---|---|---|---|---|
| M0 | Environment proof, skeleton, docs | 1 | M | Done 2026-11-14 |
| M1 | Ingestion, manifests, versions, lineage | 1 | M | Done 2026-11-14 |
| M2 | Job and event spine | 1 | L | Done 2026-11-15 |
| M3 | Cleaning and preprocessing | 1 | M | Done 2026-11-15 |
| M4 | Labeling | 1 | L | In progress: started 2026-11-15 |
| M5 | Splits, review, eval report | 1 | M | Not started |
| M6 | Phase-1 screens | 1 | L | Not started |
| M7 | Phase-1 acceptance | 1 | M | Not started |
| M8 | Training runtime and registry | 2 | L | Outline |
| M9 | Model builder | 2 | L | Outline |
| M10 | Classical ML, HPO, task metrics | 2 | L | Outline |
| M11 | Dashboards and monitoring completion | 2 | L | Outline |
| M12 | LLM serving and RAG | 3 | L | Outline |
| M13 | Router, tools, caption refinement | 3 | L | Outline |
| M14 | Text→image and LoRA | 4 | L | Outline |
| M15 | Translation, chat UI, polish | 5 | L | Outline |

## Design gates (run alongside the backend milestones)

No UI is implemented before its design is approved (SPEC §9.2). The gates wait on the owner, so they start early.

| Gate | Deliverable | Starts | Unblocks |
|---|---|---|---|
| G0 | `docs/DESIGN-SYSTEM.md` and `docs/design/system-preview.html` | alongside M1 | every screen design |
| G1 | `docs/SCREENS.md`: screen inventory and core flows | after G0 is approved | G2 |
| G2 | Mockups, one screen at a time, low-fi then hi-fi: SCR-01, 02, 03, 04, 05, 06 | alongside M2–M5 | M6 |
| G3 | Implementation that matches the approved mockups | M6 | – |

Approvals are logged in `docs/design/APPROVALS.md`. Only an explicit "approved" passes a gate.

- [x] G0 approved (v1, 2026-11-15)
- [x] G1 approved (v1, 2026-11-15)
- [x] G2 approved: SCR-01 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-01_v1.html`)
- [x] G2 approved: SCR-02 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-02_v1.html`)
- [x] G2 approved: SCR-03 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-03_v1.html`)
- [x] G2 approved: SCR-04 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-04_v1.html`)
- [x] G2 approved: SCR-05 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-05_v1.html`)
- [x] G2 approved: SCR-06 (low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-06_v1.html`)
- [x] G2 approved: SCR-07 phase-1 slice (added by the owner's choice on SCREENS.md open point 1; low-fi v1 and hi-fi v1, 2026-11-15; `docs/design/screens/SCR-07_v1.html`)

---

## M0 — Environment proof, skeleton, docs

**Size** M · **Branch** `m0-environment` · **Serves** NFR-8, NFR-9, NFR-10; ADR-0001, ADR-0007, ADR-0011

**Scope**

- The documentation set.
- The owner's four setup steps (see [KICKOFF.md](KICKOFF.md#what-the-owner-does-next)).
- Spikes S-01 to S-05 ([RISKS.md](RISKS.md#spike-plans)), run before any pipeline code, as the input requires.
- Project skeleton: Python 3.12 with uv and a lockfile; ruff, pyright in strict mode, pytest, pre-commit; `stratal.core` with settings, JSON logging and the error model; a FastAPI app with a health endpoint; Compose with postgres, redis and api; a worker image that can use the GPU; Alembic initialised; the CI workflow file; the NFR-9 meta-test.

**Done when**

- [x] Documentation set written and committed
- [x] Docker Desktop running, disk image on D:, `.wslconfig` in place, downloads approved (done by the agent on the owner's instruction, 2026-11-13)
- [x] S-01: the GPU is visible in a container and PyTorch reports CUDA available
- [x] S-02: behaviour at the VRAM limit recorded; cap fraction chosen (0.85)
- [x] S-03: sleep and wake behaviour recorded (one run; a GPU job survived a 705 s sleep)
- [x] S-04: file I/O speed recorded
- [x] S-05: metric availability inside containers recorded
- [x] `docker compose up` brings postgres, redis and api to healthy
- [x] `GET /api/v1/health` returns ok, and an error response has the problem-details shape (`test_API_health_*`, `test_ADR_0011_*`)
- [x] `test_NFR_10_*`: every log line is JSON and carries the bound IDs
- [x] `test_NFR_9_*`: the meta-test runs and reports no done-but-untested P0
- [x] ruff, pyright (strict) and pytest pass locally; CI workflow file present (first green run on GitHub 2026-11-15, after E-009 was fixed)
- [x] README has real setup and run steps
- [x] RISKS.md, ERRORS.md and affected ADRs updated with spike results
- [x] The old 84.6 GB Docker disk image on C: deleted on the owner's instruction; the D: image compacted from 89.7 GB to 40.9 GB
- [x] Demo: stack up, health endpoint, GPU check output (shown in the session of 2026-11-13)
- [x] Owner's OK to merge into `main` ("go ahead and finish M0", 2026-11-14)

**Carried forward, not blocking:** the Mermaid diagrams in ARCHITECTURE.md and DATA_MODEL.md have not been rendered. The CI workflow first ran on 2026-11-15, when `main` was pushed to GitHub. It was rejected for a workflow-file error (`runner.temp` in job-level `env`), then failed on a real deadlock (E-009); both are fixed and CI has been green since.

## M1 — Ingestion, manifests, versions, lineage

**Size** M · **Branch** `m1-ingest-versioning` · **Serves** DATA-1.1, DATA-1.2, DATA-1.3, DATA-3.1, DATA-3.2, DATA-3.3, DATA-3.4, DATA-5.1

**Scope**

- A deterministic synthetic fixture generator: images with planted corrupt files, exact copies, JPEG 90/70 pairs and undersized images. Used by tests in every later milestone.
- Blob store; manifest reader and writer (atomic, streamed).
- Ingest stage as a library function: scan, copy and hash, read metadata, reject corrupt and undersized files.
- `datasets` and `dataset_versions` tables with the first migration; version registration and lineage.
- Dataset-type registry with image–caption schema validation.
- CLI: ingest a folder, list versions, show lineage.
- 👤 Version-ID derivation (DATA-3.2). Stub and tests supplied; open clarification OC-4 settled by the owner.

**Done when**

- [x] `test_DATA_1_1_*`: a 10k-file folder gives a stage-0 manifest with 10k rows
- [x] `test_DATA_1_2_*`: 5 planted corrupt files give 5 rejected rows, each with a reason
- [x] `test_DATA_1_3_*`: the minimum size comes from config; undersized images are flagged
- [x] `test_DATA_3_1_*`: the stage writes a manifest with a status and content hash per row
- [x] `test_DATA_3_2_*`: same input and config give the same ID; any change gives a new one — implemented by the agent at the owner's request on 2026-11-14 (24 tests)
- [x] `test_DATA_3_3_*`: lineage can be walked from any version back to raw
- [x] `test_DATA_3_4_*`: a second version adds only manifest bytes plus new files
- [x] `test_DATA_5_1_*`: rows missing a caption or a file are rejected with a report
- [x] Invariants INV-01 to INV-04 and INV-07 covered by tests. INV-06 (a stage keeps its parent's row count) is first exercised by a real second stage in M3
- [x] Demo: ingest from the CLI; rerun gives the same version ID; lineage printed (76-file folder: `ds_252406f6f2a9` twice, then `ds_dc71db1c8a78` with a different minimum size)
- [x] Docs updated (DATA_MODEL, API, DECISIONS, learning notes)
- [x] Owner's OK to merge (2026-11-14)

**Note on DATA-3.1, DATA-3.3 and DATA-3.4.** Their unit tests still use a stand-in for the version identity, so they prove the plumbing in isolation. The end-to-end path with the real identity is covered by the CLI demo above, not yet by an automated test.

## M2 — Job and event spine

**Size** L · **Branch** `m2-job-event-spine` · **Serves** MON-1.1–1.5, MON-2.1–2.5, MON-3.1 (ingestion, staging), NFR-10, NFR-13, the phase-1 slice of RUN-1.1 and RUN-1.2; BUG2, BUG4

**Before starting:** ADR-0005 accepted; open clarifications OC-1 and OC-2 confirmed. All three accepted by the owner on 2026-11-14.

**Scope**

- Tables: `jobs`, `job_stages`, `job_transitions`, `events`, `resource_samples`, `process_samples`.
- State machine with validated, logged transitions; cancel.
- Celery `cpu` and `gpu` workers; supervisor and child; the job context.
- Event emitter (stream plus spool), collector, SSE endpoints with replay.
- Sampler (psutil + NVML) with tagging and interval switching; the job's own reporter thread; ETA; stage peaks and job summary.
- Watchdog v1: dead-job detection from heartbeats.
- Host script v1: keep-awake and power events.
- Jobs, events and telemetry endpoints. Ingestion runs as a pipeline job.

**Done when**

- [x] `test_MON_1_1_*`: a job's full timeline is rebuilt from the event log alone
- [x] `test_MON_1_2_*`: activity messages with key parameters appear live
- [x] `test_MON_1_3_*`: a WARN or ERROR without reason and action is rejected; an OOM-style event carries its suggested action
- [x] `test_MON_1_4_*`: events survive a restart of api and monitor; SSE delivers within 1 s
- [x] `test_MON_1_5_*`: ETA within ±20% once 10% is complete, on a steady synthetic stage
- [x] `test_MON_2_1_*`: every listed metric is recorded (GPU fields under the gpu marker)
- [x] `test_MON_2_2_*`: sample timestamps match the configured interval, active and idle
- [x] `test_MON_2_3_*`: samples are tagged with job and stage
- [x] `test_MON_2_4_*` (gpu): NVML and PyTorch figures recorded side by side
- [x] `test_MON_2_5_*`: the job summary lists peaks for every stage
- [x] `test_MON_3_1_*`: ingestion and staging emit their listed messages and metrics
- [x] `test_RUN_1_1_*` (slice): transitions logged; invalid transitions rejected
- [x] `test_NFR_10_*`: job logs carry job ID and version ID on every line
- [x] `test_NFR_13_*`: kill the monitor mid-job and the job continues; kill the job and the monitor continues
- [x] `test_BUG2_*`: a child killed by a signal leaves the job failed with a reason and the worker healthy
- [x] `test_BUG4_*`: a frozen worker container resumes or recovers when unfrozen
- [x] Demo: start an ingest job through the API, watch events and samples arrive, kill things (2026-11-14: 1,535 files to `ds_389dd9b94119`; monitor killed mid-job; child killed then retried; worker frozen 45 s and recovered on attempt 2)
- [x] Docs updated; ADR-0003 and ADR-0005 confirmed or amended

**Not built in M2, carried forward:** `GET /system` (API.md §3.1); `from_version` in the pipeline request (no stage consumes a version until M3); the host script is v1 and its sleep detection has not been tried against a real sleep.

- [x] Owner's OK to merge (2026-11-15, after the MON-2.4 GPU test was fixed for E-008)

## M3 — Cleaning and preprocessing

**Size** M · **Branch** `m3-clean-preprocess` · **Serves** DATA-2.1, DATA-2.2, DATA-6.1, MON-3.1 (cleaning, preprocessing); BUG1

**Before starting:** open clarification OC-3 (preprocess size) confirmed. **Done 2026-11-15:** 768 px, accepted by the owner.

**Scope**

- Clean stage shell; pHash values kept in the derivation cache.
- Exact-duplicate grouping (DATA-2.1) and near-duplicate detection (DATA-2.2). Owner units, written by the agent at the owner's request on 2026-11-15 (ADR-0012). 👤 The threshold is the owner's: 8, chosen 2026-11-15 (OC-5).
- Preprocess stage: resize, apply EXIF orientation, convert to RGB, store the copy.
- Carried from M2: `from_version` in the pipeline request.

**Agent's part built 2026-11-15:** stubs, strict expected-failure tests and fakes for DATA-2.1 and DATA-2.2 (`src/stratal/data/dedupe.py`; 16 tests in `tests/unit/test_DATA_2_1_*.py` and `test_DATA_2_2_*.py`); the clean shell; the preprocess stage; the derivation cache; stage memoization; `from_version`.

**DATA-2.1 and DATA-2.2 written 2026-11-15** by the agent, at the owner's request ("you write it"). The expected-failure markers are removed and the 16 contract tests pass. Twelve more tests (`test_DATA_2_dedupe_decisions.py`) pin the choices the contract left open: the keepers, transitive grouping, unusual image modes, the threshold's edges. An integration test runs the real units in a pipeline job. The owner accepted the threshold of 8 on 2026-11-15 from the measured distances in DECISIONS.md.

**Done when**

- [x] `test_DATA_2_1_*`: 20 planted exact copies are marked, one kept per group (units written by the agent at the owner's request)
- [x] `test_DATA_2_2_*`: the same image at JPEG 90 and 70 leaves one kept (same; at the chosen threshold of 8)
- [x] 👤 Near-duplicate threshold chosen from the measured distances (OC-5): 8, accepted by the owner 2026-11-15 (`test_DATA_2_2_default_threshold_is_the_owners_choice`)
- [x] `test_DATA_6_1_*`: copies exist and respect the maximum; originals kept; a rerun adds nothing; a new maximum gives a new version ID
- [x] `test_MON_3_1_*`: cleaning and preprocessing emit their listed messages and metrics (the clean stage's with fakes for the owner's units)
- [x] `test_BUG1_*`: every stage's output reloads by version ID and equals what was written
- [x] Invariant INV-05 holds after clean: the stage refuses to commit a version that breaks it (`test_INV_05_*`)
- [x] Demo: ingest → clean → preprocess as one pipeline job (2026-11-15, `job_0005` on the live stack). 595 planted files in `sources/demo-m3`: 580 ingested; 20 exact and 20 near duplicates marked, with every JPEG 90 kept over its JPEG 70; 540 copies at ≤ 768 px. Final version `ds_ad9a39e33ab5`
- [x] Docs updated (2026-11-15, including the OC-5 choice; learning notes §5e and §20)
- [x] Owner's OK to merge (2026-11-15, "Yes, merge and push")

## M4 — Labeling

**Size** L · **Branch** `m4-labeling` · **Serves** LABEL-1.1–1.4, MON-3.1 (labeling), NFR-1, NFR-3, NFR-7, NFR-8, the phase-1 slice of RUN-1.4 and RUN-1.5; BUG2, BUG5, BUG6

**Before starting:** phase-1 dataset chosen (open question); model-weight downloads approved. **Dataset chosen 2026-11-15:** a 10k subset of COCO 2017, accepted by the owner. Downloads: waiting for approval.

**Scope**

- Model allow-list and an explicit pull command; the hub is offline at run time.
- Captioner interface with Florence-2 and BLIP adapters and a deterministic fake for CPU tests.
- Spike S-06: Florence-2 vs BLIP on 50 images, quality and speed.
- GPU lease primitive; label and filter stage shells.
- 👤 Caption batching loop with resume (LABEL-1.1, LABEL-1.2).
- 👤 VRAM manager v1: load under the cap, unload and verify, OOM backoff (slice of RUN-1.4, RUN-1.5).
- 👤 CLIP scoring (LABEL-1.3) and the filter rule with a chosen threshold (LABEL-1.4, OC-5).
- Spike S-07: memory under the container limits.

**Done when**

- [ ] S-06 report delivered; owner decides Florence-2 vs BLIP
- [ ] 👤 `test_LABEL_1_1_*`: every accepted row has a caption and a model version
- [ ] 👤 `test_LABEL_1_2_*`: kill at 60%, restart, completes; no finished image is captioned twice
- [ ] 👤 `test_LABEL_1_3_*`: a score is present for every pair
- [ ] 👤 `test_LABEL_1_4_*`: rows below the threshold are marked with a reason, not deleted
- [ ] `test_MON_3_1_*`: labeling emits its listed messages and metrics
- [ ] 👤 `test_BUG5_*` (gpu): VRAM returns to baseline after unload and after the stage's process exits
- [ ] `test_BUG6_*`: a second process cannot take the GPU lease
- [ ] 👤 `test_BUG2_*` (gpu): an oversized batch produces a WARN with a smaller batch and the stage completes
- [ ] `test_NFR_1_*` (gpu): logged peak VRAM is under 5.5 GB
- [ ] NFR-3 benchmark recorded; target adjusted with the owner if needed
- [ ] `test_NFR_7_*`: the label stage runs with the network disabled after models are pulled
- [ ] `test_NFR_8_*`: no `trust_remote_code` in source; a model outside the allow-list is refused
- [ ] Demo: label 1,000 images and watch it live through the API
- [ ] Docs updated
- [ ] Owner's OK to merge

## M5 — Splits, review, eval report

**Size** M · **Branch** `m5-split-review-eval` · **Serves** DATA-4, LABEL-1.5, EVAL-1.1, DATA-5.1 (in the pipeline), NFR-4

**Before starting:** 👤 DATA-4 written by the owner and reviewed.

**Scope**

- Split stage shell. 👤 Split assignment per DATA-4.
- Review sessions: sampling, decisions, storage, endpoints.
- Report assembly. 👤 Report statistics, including the Wilson interval.
- Evaluate stage; schema validation applied in the filter stage.

**Done when**

- [ ] DATA-4 is in SPEC.md with IDs and criteria, approved
- [ ] 👤 `test_DATA_4_*`: one test per criterion in DATA-4
- [ ] `test_LABEL_1_5_*`: N rows sampled; decisions stored and linked to the version
- [ ] 👤 `test_EVAL_1_1_*`: a report per version with all four parts; the interval checked against known values
- [ ] `test_NFR_4_*`: two runs with the same data, config and seed give the same splits and version IDs
- [ ] Demo: the full pipeline on 1,000 images, a 50-row review, the report
- [ ] Docs updated
- [ ] Owner's OK to merge

## M6 — Phase-1 screens

**Size** L · **Branch** `m6-phase1-ui` · **Serves** MON-4.1, MON-4.2, MON-4.3, the phase-1 slice of UI-2.1; screens for DATA-3, LABEL-1.5, EVAL-1.1

**Before starting:** G0, G1 and G2 approved for SCR-01 to SCR-06 and the SCR-07 slice.

**Scope**

- Frontend scaffold with the stack in SPEC §7.4; tokens from the approved design system; generated TypeScript client.
- SCR-01 app shell, SCR-02 datasets and versions, SCR-03 version detail and lineage, SCR-04 live job view, SCR-05 labeling review, SCR-06 eval report.
- SCR-07 phase-1 slice: jobs list with state and kind filters, open, cancel, retry (owner's choice, 2026-11-15; uses the phase-1 slice of RUN-1.1 and RUN-1.2 already built in M2).

**Done when**

- [ ] Each screen built only after its G2 approval is logged
- [ ] `test_MON_4_1_*`: the live job view renders and updates during a job
- [ ] `test_MON_4_2_*`: the timeline shows every stage; clicking one shows its events and charts
- [ ] `test_MON_4_3_*`: filters by level, stage and job work
- [ ] Per screen: screenshot compared with the approved mockup, differences listed and signed off (G3)
- [ ] Per screen: contrast and keyboard path checked
- [ ] No raw hex colours outside the token files (lint)
- [ ] Frontend typecheck and build pass
- [ ] Demo: run a pipeline and follow it in the browser
- [ ] Docs updated
- [ ] Owner's OK to merge

## M7 — Phase-1 acceptance

**Size** M · **Branch** `m7-phase1-acceptance` · **Serves** the phase-1 done-definition; NFR-1, NFR-2, NFR-3, NFR-4, NFR-5, NFR-11, NFR-12, NFR-13; DATA-2.3 (P2, if time)

**Scope**

- The full 10k run on the chosen dataset.
- `beat` and telemetry downsampling.
- Benchmarks, the kill-test suite, the memory profile.
- Event backfill from spool files.
- 👤 Semantic duplicates (DATA-2.3), if time allows.
- README with the benchmark; phase-2 plan.

**Done when**

- [ ] 10k raw images → captioned, filtered, versioned, split dataset with an eval report
- [ ] `test_NFR_2_*`: memory profile on a 20k-image run stays within budget
- [ ] `test_NFR_5_*`: a kill test for every stage; lost work is bounded
- [ ] `test_NFR_11_*`: monitoring overhead measured on vs off
- [ ] `test_NFR_12_*`: the downsampling job is verified
- [ ] NFR-3 benchmark in the README
- [ ] Meta-test green: every phase-1 P0 has a passing ID-named test
- [ ] All seven `test_BUG*` regression tests green
- [ ] M8–M11 detailed and approved by the owner
- [ ] Owner's OK to merge; tag `phase-1`

---

## Later phases (outline)

Every numbered requirement is assigned to exactly one milestone. Scope and checklists are written at each phase kickoff.

| | Scope | Requirements |
|---|---|---|
| M8 | Training jobs, checkpoints, pause and resume, complete VRAM manager, OOM handling, device choice, registry, retention, inference service | RUN-1.1–1.9; REG-1.1, 1.2, 1.4; REG-2.1–2.11; NFR-14, NFR-15 |
| M9 | Layer stacking, shape inference, cross-field validation, parameter and memory estimates, templates, 👤 from-scratch layers, schema-generated forms, labeled dataset types | BUILD-1.1–1.9; UI-2.3; DATA-5.2, 5.5 |
| M10 | Classical ML, clustering, safe persistence, Optuna studies, 👤 task metrics, tabular and text ingestion and cleaning | ML-1.1–1.5; HPO-1.1–1.5; EVAL-1.2; DATA-1.4, 2.4 |
| M11 | Alerts with actions, bottleneck diagnostics, load/unload VRAM events, job comparison, history, version diff, full dashboard, phase-2 acceptance (a CNN built in the UI, trained, test metrics shown) | MON-2.6, 2.7, 3.2, 3.3, 4.4, 4.5; UI-2.1, 2.2; EVAL-1.3; DATA-3.5 |
| M12 | Local LLM serving, RAG with citations, hit@k evaluation, abstention, image search | LLM-1.1, 2.1–2.4; DATA-5.3; NFR-6 |
| M13 | Router and fallback, tool-calling assistant, caption refinement and comparison, QLoRA | LLM-3.1, 3.2, 4.1, 5.1; LABEL-1.6, 1.7 |
| M14 | Image generation, LoRA fine-tuning, before/after evaluation, remote offload round trip, adapters as deltas | IMG-1.1–1.4; REG-1.3 |
| M15 | Translation serving and fine-tuning, parallel-text type, scratch vs pretrained, chat UI in manual and auto mode | TRANS-1.1, 1.2; DATA-5.4; EVAL-1.4; UI-1.1 |
