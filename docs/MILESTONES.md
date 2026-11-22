# Stratal — Milestones

| | |
|---|---|
| Status | M0 merged 2026-11-14. M1 merged 2026-11-14. M2 merged 2026-11-15. M3 merged 2026-11-15 (DATA-2.1 and DATA-2.2 written by the agent at the owner's request; OC-5 threshold 8). M4 merged 2026-11-15 (all four owner units written by the agent at the owner's request; Florence-2 `<CAPTION>`; CLIP threshold 0.2). M5 merged 2026-11-16 (DATA-4 drafted, and its units and the EVAL-1.1 statistics written, by the agent at the owner's request; DATA-4.8 moved to M7). M6 merged 2026-11-17 (SCR-01 to SCR-07 slice, each through G3; two contrast rules added to the design system). M7 merged 2026-11-17: phase 1 accepted on a measured 20,000-image run, tagged `phase-1`; DATA-2.3 and DATA-4.8 moved to M11. M8 merged 2026-11-19 (training runtime, registry, retention and inference; SCR-07 v2, SCR-04 v2, SCR-11 and SCR-14 through G3; RUN-1.4 and RUN-1.5 written by the agent at the owner's request). M9 merged 2026-11-19 (the model builder: graph specs checked in plain Python, compiled to PyTorch, templates; SCR-08 and SCR-09 through G3; BUILD-1.8 written by the agent at the owner's request). M10 merged 2026-11-20 (tables and texts, classical ML, EVAL-1.2's task metrics, HPO with Optuna; SCR-09's study part, SCR-10 and SCR-11's task metrics through G3; EVAL-1.2 written by the agent at the owner's request, K1). G0 and G1 approved 2026-11-15; G2 passed for every phase-1 screen (SCR-01 to SCR-06, SCR-07 slice) 2026-11-15. Phase-2 plan (M8–M11) approved 2026-11-17 |
| Planning style | Phases 1 and 2 (M0–M11) are planned in detail; phase 2's detail was approved on 2026-11-17. Phases 3–5 (M12–M15) are outlined and get detailed, with the owner's approval, when the previous phase closes |

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
| M4 | Labeling | 1 | L | Done 2026-11-15 |
| M5 | Splits, review, eval report | 1 | M | Done 2026-11-16 |
| M6 | Phase-1 screens | 1 | L | Done 2026-11-17 |
| M7 | Phase-1 acceptance | 1 | M | Done 2026-11-17 |
| M8 | Training runtime and registry | 2 | L | Done 2026-11-19 |
| M9 | Model builder | 2 | L | Done 2026-11-19 |
| M10 | Classical ML, HPO, task metrics | 2 | L | Done 2026-11-20 |
| M11 | Dashboards and monitoring completion | 2 | L | In progress (kickoff decisions K1–K12, 2026-11-20) |
| M11b | Tabular transforms | 2 | M | Not started (added at the owner's request 2026-11-19; outline) |
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

**Before starting:** phase-1 dataset chosen (open question); model-weight downloads approved. **Dataset chosen 2026-11-15:** a 10k subset of COCO 2017, accepted by the owner. Downloads approved 2026-11-15: COCO annotations and 10,000 images; Florence-2-base, CLIP ViT-B/32 (LAION) and BLIP-large.

**Scope**

- Model allow-list and an explicit pull command; the hub is offline at run time.

**Agent's part built 2026-11-15:** allow-list and pull command; captioner and CLIP adapters (`stratal.label.hf`) with fakes; spike S-06; the GPU lease; label and filter stage shells, run end to end as one job with fakes (`test_M4_pipeline.py`); stubs, strict expected-failure tests and fakes for the four owner units. Until they are written, a pipeline job fails at `label` with a reason naming the missing unit; `"until_stage": "preprocess"` stops before it.
- Captioner interface with Florence-2 and BLIP adapters and a deterministic fake for CPU tests.
- Spike S-06: Florence-2 vs BLIP on 50 images, quality and speed.
- GPU lease primitive; label and filter stage shells.
- Caption batching loop with resume (LABEL-1.1, LABEL-1.2). Owner unit, written by the agent at the owner's request on 2026-11-15 (ADR-0012).
- VRAM manager v1: load under the cap, unload and verify, OOM backoff (slice of RUN-1.4, RUN-1.5). Owner unit, written by the agent at the owner's request.
- CLIP scoring (LABEL-1.3) and the filter rule (LABEL-1.4). Owner units, written by the agent at the owner's request. 👤 The threshold is the owner's: 0.2, chosen 2026-11-15 (OC-5).
- Spike S-07: memory under the container limits. **Done 2026-11-15:** every container under half its limit on 2,000 images; limits kept (RISKS.md).

**Done when**

- [x] S-06 report delivered; owner decides Florence-2 vs BLIP (2026-11-15: Florence-2-base, `<CAPTION>`)
- [x] `test_LABEL_1_1_*`: every accepted row has a caption and a model version (units written by the agent at the owner's request)
- [x] `test_LABEL_1_2_*`: kill at 60%, restart, completes; no finished image is captioned twice
- [x] `test_LABEL_1_3_*`: a score is present for every pair
- [x] `test_LABEL_1_4_*`: rows below the threshold are marked with a reason, not deleted
- [x] 👤 CLIP threshold chosen from the measured distribution (OC-5): 0.2, kept by the owner 2026-11-15 (`test_LABEL_1_4_default_threshold_is_the_owners_choice`)
- [x] `test_MON_3_1_*`: labeling emits its listed messages and metrics (the shell's, with fakes; the caption loop's and VRAM manager's are in the owner's tests)
- [x] `test_BUG5_*` (gpu): VRAM returns to baseline after unload and after the stage's process exits
- [x] `test_BUG6_*`: a second process cannot take the GPU lease (also: a dead holder releases it)
- [x] `test_BUG2_*` (gpu): an oversized batch produces a WARN with a smaller batch and the stage completes
- [x] `test_NFR_1_*` (gpu): logged peak VRAM is under 5.5 GB (test: 3.78 GB reserved + context; demo log: 4.20 GB card-wide)
- [x] NFR-3 benchmark recorded; target adjusted with the owner if needed (README: about 10 img/s captioning, five times the target; no change needed)
- [x] `test_NFR_7_*`: the label stage runs with the network disabled after models are pulled (every socket connection refused)
- [x] `test_NFR_8_*`: no `trust_remote_code` in source; a model outside the allow-list is refused (also: every file pinned by hash, a mismatched file is not kept)
- [x] Demo: label 1,000 images and watch it live through the API (2026-11-15, `job_0006`: 1,000 COCO images through all five stages in about 2 min 20 s; 994 labelled, 9 filtered at 0.2; final version `ds_ef5728d21725`)
- [x] Docs updated (2026-11-15; learning notes §5f and §21)
- [x] Owner's OK to merge (2026-11-15, "Yes, merge and push")

## M5 — Splits, review, eval report

**Size** M · **Branch** `m5-split-review-eval` · **Serves** DATA-4, LABEL-1.5, EVAL-1.1, DATA-5.1 (in the pipeline), NFR-4

**Before starting:** 👤 DATA-4 written by the owner and reviewed. **Done 2026-11-15:** drafted by the agent at the owner's request, v1 approved by the owner and moved into SPEC.md.

**Scope**

- Split stage shell. 👤 Split assignment and leakage grouping per DATA-4.
- Review sessions: sampling, decisions, storage, endpoints.
- Report assembly. 👤 Report statistics, including the Wilson interval.
- Evaluate stage; schema validation applied in the filter stage.

**Done when**

- [x] DATA-4 is in SPEC.md with IDs and criteria, approved (2026-11-15, v1)
- [x] 👤 `test_DATA_4_*`: one test per criterion in DATA-4. **2026-11-16:** the units were written by the agent at the owner's request. 65 unit and 7 integration `test_DATA_4_*` tests pass, covering every criterion of DATA-4.1 to DATA-4.7. The two criteria worded "on the COCO 10k version" were checked on it (`job_0013`, split `ds_3f6fcc420a27`). DATA-4.1.AC1: all 9,838 accepted rows have one split and no other row has one; 7,870 / 984 / 984 against 7,870.4 / 983.8 / 983.8; all 12 strata within one group. DATA-4.2.AC2: 0 of 16,456,416 cross-split pairs within 12 bits or in one duplicate group. DATA-4.8 (P2) moved to M7 with DATA-2.3, which it needs, by the owner's decision of 2026-11-16
- [x] `test_LABEL_1_5_*`: N rows sampled; decisions stored and linked to the version (2026-11-16; 4 unit and 4 integration tests, with `test_DATA_4_7_*` for the split on each item)
- [x] 👤 `test_EVAL_1_1_*`: a report per version with all four parts; the interval checked against known values (2026-11-16; statistics written by the agent at the owner's request; Wilson against Newcombe 1998, table I; a report per version in the integration tests)
- [x] `test_NFR_4_*`: two runs with the same data, config and seed give the same splits and version IDs (2026-11-16; with the owner's DATA-4 units. The test found E-010, fixed)
- [x] Demo: the full pipeline on 1,000 images, a 50-row review, the report (2026-11-16). `job_0012` ran coco1k from its source folder through all seven stages in 35 s, reusing cached work (computed in `job_0006` and `job_0010`). Split `ds_7ffe080f799f`: 788 / 99 / 98, all 12 strata within one group, 0 leaks. Review `rev_0002` (50 rows, seed 143108104) was judged by the agent at the owner's request, from the images: 45 accepted, 90 %, Wilson 95 % interval 78.6–95.7 %; all 5 rejects in train (test 8/8, val 4/4). Report 5 has all four parts, with the review
- [x] Docs updated (2026-11-16: API, DATA_MODEL, DECISIONS, ERRORS, ARCHITECTURE, README; learning notes)
- [x] Owner's OK to merge ("Move it, then merge", 2026-11-16)

## M6 — Phase-1 screens

**Size** L · **Branch** `m6-phase1-ui` · **Serves** MON-4.1, MON-4.2, MON-4.3, the phase-1 slice of UI-2.1; screens for DATA-3, LABEL-1.5, EVAL-1.1

**Before starting:** G0, G1 and G2 approved for SCR-01 to SCR-06 and the SCR-07 slice.

**Scope**

- Frontend scaffold with the stack in SPEC §7.4; tokens from the approved design system; generated TypeScript client.
- SCR-01 app shell, SCR-02 datasets and versions, SCR-03 version detail and lineage, SCR-04 live job view, SCR-05 labeling review, SCR-06 eval report.
- SCR-07 phase-1 slice: jobs list with state and kind filters, open, cancel, retry (owner's choice, 2026-11-15; uses the phase-1 slice of RUN-1.1 and RUN-1.2 already built in M2).

**Done when**

- [x] Each screen built only after its G2 approval is logged (all G2 rows 2026-11-15, before any screen code)
- [x] `test_MON_4_1_*`: the live job view renders and updates during a job (2026-11-17; `test_MON_4_1_live_view_renders_and_updates_during_a_job`, Vitest with a fake event source)
- [x] `test_MON_4_2_*`: the timeline shows every stage; clicking one shows its events and charts (2026-11-17)
- [x] `test_MON_4_3_*`: filters by level, stage and job work (2026-11-17; two Vitest tests, and `test_MON_4_3_events_tail_and_minimum_level` against the API)
- [x] Per screen: screenshot compared with the approved mockup, differences listed and signed off (G3): SCR-01 to SCR-07, 2026-11-16 to 2026-11-17, in `docs/design/g3/` and `docs/design/APPROVALS.md`
- [x] Per screen: contrast and keyboard path checked. Two design-system contrast rules came out of it: light state text on its tint (SCR-04) and dark pills on selected rows (SCR-07)
- [x] No raw hex colours outside the token files (lint: `scripts/no-raw-hex.mjs`, in `check.ps1`)
- [x] Frontend typecheck and build pass (in `check.ps1`; the bundle is 1.5 MB, code-splitting left for later)
- [x] Demo: run a pipeline and follow it in the browser (2026-11-17). From SCR-02, "Continue the pipeline from here" on demo's preprocess version `ds_be4049a8a755` started `job_0024` (label, filter, split, evaluate); SCR-04 followed it live to completion in 18 s (captions reused from cache), and SCR-06 showed report 11: 1,500 of 1,535 rows kept, acceptance not measured. Earlier, SCR-06 built report 10 and followed `job_0021` in place, and SCR-07 cancelled and retried live jobs. The SCR-07 check found two identical jobs racing to commit one version (`job_0022`, reported to the owner)
- [x] Docs updated (2026-11-17: API, DATA_MODEL, DECISIONS, ARCHITECTURE, README, CLAUDE.md, SCREENS, DESIGN-SYSTEM, APPROVALS, the G3 records; learning notes)
- [x] Owner's OK to merge ("OK to merge", 2026-11-17)

## M7 — Phase-1 acceptance

**Size** M · **Branch** `m7-phase1-acceptance` · **Serves** the phase-1 done-definition; NFR-1, NFR-2, NFR-3, NFR-4, NFR-5, NFR-11, NFR-12, NFR-13; DATA-2.3 and DATA-4.8 (P2, if time; moved to M11 on 2026-11-17)

**Scope**

- The full 10k run on the chosen dataset.
- `beat` and telemetry downsampling.
- Benchmarks, the kill-test suite, the memory profile.
- Event backfill from spool files.
- 👤 Semantic duplicates (DATA-2.3), and rows of one semantic cluster joining one split group (DATA-4.8, moved from M5 on 2026-11-16), if time allows. **Moved to M11 by the owner on 2026-11-17**, with ADR-0013 accepted.
- README with the benchmark; phase-2 plan.

**Done when**

- [x] 10k raw images → captioned, filtered, versioned, split dataset with an eval report (2026-11-17: `job_0026`, 20,000 images in 27.4 minutes, split `ds_005e3e700ee8`, report 13; `job_0025` reproduced M5's coco10k versions exactly)
- [x] `test_NFR_2_*`: memory profile on a 20k-image run stays within budget (2026-11-17, `tests/acceptance`: VM RAM peak 3.9 GB; job VRAM peak 3.87 GB)
- [x] `test_NFR_5_*`: a kill test for every stage; lost work is bounded (2026-11-17: seven kill tests; stages without a per-item cache take at most 4.4 minutes on 20k, against N = 10)
- [x] `test_NFR_11_*`: monitoring overhead measured on vs off (2026-11-17, `check.ps1 -Bench`: runtime +0.02 %; monitor 1.58 % of the machine, 25 % of one core. Passes, with "2 % CPU" read as a share of the machine: the owner's reading, 2026-11-17, SPEC OC-6)
- [x] `test_NFR_12_*`: the downsampling job is verified (2026-11-17; the `beat` service ran it on the live stack)
- [x] NFR-3 benchmark in the README (2026-11-17: 8.3 images/s on 9,917 uncached images)
- [x] Meta-test green: every phase-1 P0 has a passing ID-named test (2026-11-17: `test_NFR_9_every_phase_1_p0_requirement_has_a_named_test`, 31 of 31)
- [x] BUG1 to BUG6 regression tests green, BUG3 through `test_NFR_2_*` (2026-11-17: the 23 BUG tests pass, GPU ones included). Reworded by the owner on 2026-11-17 from "all seven": BUG7's guard, the NFR-14 disk audit, needs training and is built in M8
- Moved to M11 by the owner on 2026-11-17, with [ADR-0013](decisions/ADR-0013-semantic-duplicates.md) accepted: 👤 `test_DATA_4_8_*` (P2), two photos of one scene from different angles land in the same split
- [x] M8–M11 detailed and approved by the owner (2026-11-17, "Approved"; the eight decisions listed for M8's kickoff stay open until then)
- [x] Owner's OK to merge; tag `phase-1` ("OK to merge", 2026-11-17)

---

## Phase 2 in detail (M8–M11)

| | |
|---|---|
| Status | **Approved by the owner on 2026-11-17** ("Approved"). The eight decisions below are still open: they are put to the owner at M8's kickoff, and until then each recommendation is only a recommendation |
| Done when (SPEC §2) | The user builds a CNN in the UI, trains it on a dataset version and sees its test metrics |
| Requirements | All 58 phase-2 requirements (32 P0, 23 P1, 3 P2), plus NFR-14 and NFR-15. Each stays in the milestone the outline gave it; nothing is added or dropped. M11 also carries two phase-1 P2 requirements, DATA-2.3 and DATA-4.8, moved from M7 by the owner on 2026-11-17 |

### Decisions needed before M8 starts

**Decided by the owner on 2026-11-17, at M8's kickoff:**

| # | Decision |
|---|---|
| 1 | CIFAR-10 for the CNN (download approved); scikit-learn's bundled datasets for tabular; COCO captions labelled with their image's category for text |
| 2 | `/predict` falls back to the CPU while training holds the GPU; models too large for it fail fast |
| 3 | A `worker-cpu` image target with an `ml-cpu` extra; `api` and `monitor` stay free of ML libraries |
| 4 | All four defaults as proposed (SPEC OC-3, OC-7) |
| 5 | Pause and resume for pipeline jobs too (SPEC OC-8) |
| 6 | Retention settings in the database, edited on SCR-14 (SPEC OC-9) |
| 7 | Classical-ML trials grouped in one job; each deep-learning trial its own job (SPEC OC-10) |
| 8 | **Not split:** M8 stays one milestone, one branch and one merge |

The proposals as they were put to the owner:

1. **Phase-2 datasets.**
   - **Labelled images, for the CNN (needed from M8).** (a) *Recommended:* CIFAR-10, about 170 MB from the University of Toronto: 60,000 images of 32×32 px in 10 classes with clean labels. A small CNN's accuracy on it is well known, so a wrong result points at a bug in the training loop rather than at the data. Ingest's 256 px minimum (DATA-1.3) is lowered for this dataset in its config. (b) COCO 2017 classes derived from the annotations already on disk, no download: each image labelled with the category covering most of it. Multi-object photos make those labels noisy, so a low accuracy would prove little.
   - **Tabular (M10).** *Recommended:* scikit-learn's bundled datasets (breast cancer for classification, diabetes for regression, wine). They are installed with the package, so no download.
   - **Labelled text (M9 schema, M10 ingestion).** *Recommended:* the COCO captions already in `sources/coco10k-meta`, labelled with their image's category. No download.
2. **Inference while a training job holds the GPU** (the open consequence of ADR-0006). Requests can wait, fail fast, or run on the CPU. *Recommended:* run on the CPU for models small enough for it and say so in the response (`"device": "cpu"`); fail fast with a clear reason otherwise.
3. **Where CPU training and classical ML run.** *Recommended:* a `worker-cpu` image target with an `ml-cpu` extra (CPU-only PyTorch, scikit-learn, skops, Optuna). `api` and `monitor` keep no ML libraries, as already decided ("API image has no ML libraries", DECISIONS.md). The CPU queue then runs CPU training (RUN-1.9) and classical ML (M10); the image grows by several hundred MB.
4. **Defaults.**
   - RUN-1.7: T = 300 s without progress before `stalled` (OC-3's remaining proposal).
   - RUN-1.3: a checkpoint every epoch and at least every 5 minutes of training, so a crash loses less than NFR-5's 10 minutes.
   - RUN-1.6: pause at 83 °C, resume below 75 °C. The highest so far is 74 °C, after 20 minutes of captioning in `job_0026`.
   - MON-2.6: "VRAM > 90 %" read as 90 % of the card (NVML, about NFR-1's 5.5 GB); "RAM > 85 %" as 85 % of the VM's 10 GB.
5. **Pause and resume for pipeline jobs too** (RUN-1.2 says "jobs"). *Recommended:* yes. Pipeline stages already resume from their per-batch caches, so a pause is a cooperative stop between batches.
6. **Retention settings.** *Recommended:* stored in the database and edited on SCR-14, reading SPEC's `configs/retention.yaml` example as the shape of the settings rather than a file to edit.
7. **HPO trials (M10).** SPEC §7.2 says each trial is a job; ADR-0003 notes that sub-second classical-ML trials would be grouped. *Recommended:* each deep-learning trial is one job; a classical-ML study runs its trials inside one job, each trial still logged as events and stored by Optuna.
8. **M8's size.** M8 carries 23 requirements, NFR-14, NFR-15, three ADRs and four screens: about twice a phase-1 milestone. *Recommended:* split it into **M8a** (training runtime) and **M8b** (registry, retention and inference), each with its own branch, review and merge. The letters keep M9–M15's numbers. M8 below is written in those two parts either way.

Carried from M7, as the owner decided on 2026-11-17: BUG7's guard, the NFR-14 disk audit, lands in M8 as `test_BUG7_*`; ADR-0013 is accepted, and DATA-2.3 and DATA-4.8 are built in M11; NFR-11's "2 % CPU" is a share of the machine (SPEC OC-6), which M11's re-measurement uses.

Still parked by the owner, not scheduled here: the other OC-3 proposals, the M2 loose ends, dedupe only within a version, version IDs shared across datasets, `caption_vram_estimate_mb` in the label identity, Florence relation errors, and `ubuntu-latest` moving to Ubuntu 26.

### Architecture to settle first

Each ADR is written at the start of its milestone and approved before anything it covers is built.

| ADR | Covers | Milestone |
|---|---|---|
| ADR-0014 Training jobs | Training as a job kind; the model factory (reference models in M8, builder specs from M9); input pipeline; checkpoint contents (model, optimizer, scheduler, random states, data position) and cadence; pause, resume, thermal pause, stall; device choice; the images and queues of decision 3 | M8 |
| ADR-0015 Model registry and weight retention | Tables (DATA_MODEL §9), weight files as safetensors, version states, retention as a job, atomic deletes (NFR-15) | M8 |
| ADR-0016 Inference service | One model at a time, the GPU lease, decision 2, verified unloads, its memory limit (ADR-0007 revised, the 9 GB sum kept) | M8 |
| ADR-0017 Model spec | Layer specs, graph form (BUILD-1.9), compilation to PyTorch, parameter and memory estimates | M9 |
| ADR-0018 HPO execution | Optuna's storage in its own PostgreSQL schema, trials per decision 7, pruning, the single test evaluation | M10 |

### Owner units in phase 2 (👤)

The agent delivers each unit's typed stub, strict expected-failure tests named with its ID, and a fake for the code around it before the milestone that needs it (ADR-0012).

| Unit | Requirement | Needed in | Stub delivered |
|---|---|---|---|
| VRAM manager v2: training models loaded under the cap, unloaded back to baseline | RUN-1.4 | M8 | start of M8 |
| Out-of-memory in training: clean failure with a suggested smaller batch | RUN-1.5 | M8 | start of M8 |
| From-scratch attention block, positional encoding and LSTM cell | BUILD-1.8 | M9 | start of M9 |
| Task metrics by type, CIDEr-D in house (CL-13) | EVAL-1.2 | M10 | start of M8, so the CNN can be scored as soon as the owner has written it |

### New dependencies

Each is named in SPEC §7 and added only when its milestone needs it.

| Package | For | Milestone | Where |
|---|---|---|---|
| safetensors | Weights (REG-1.1, NFR-8) | M8 | worker images; already installed with transformers in the GPU image |
| PyTorch, CPU build (about 200 MB) | CPU training (RUN-1.9) | M8 | `worker-cpu`, if decision 3 |
| torchinfo | Cross-checking parameter counts (BUILD-1.5), tests only | M9 | `dev` |
| React Flow (npm; asked for at the time) | SCR-08 canvas | M9 | `frontend` |
| scikit-learn, skops | ML-1.1 to ML-1.5 | M10 | `worker-cpu` |
| Optuna | HPO-1.2 | M10 | worker images |
| sacrebleu, torchmetrics | EVAL-1.2, only if the owner prefers libraries to own code | M10 | worker images |

Not planned: LightGBM (scikit-learn's gradient boosting meets ML-1.1), torchvision (Pillow and PyTorch cover the input pipeline), optuna-dashboard (SCR-10 shows the study).

### Design gates for phase 2

No screen is built before its design is approved (SPEC §9.2). The gates wait on the owner, so they run ahead of the backend, as in phase 1.

| Gate | Deliverable | Starts | Unblocks |
|---|---|---|---|
| G1 revision | SCREENS.md §3 detail for SCR-04 (training runs), SCR-07 (pause and resume), SCR-08 to SCR-11, SCR-13 and SCR-14; Flow 4 step by step | M8 kickoff | phase-2 G2 |
| G2 | SCR-07 v2, SCR-04 v2, SCR-11, SCR-14 | alongside M8 | M8's screens |
| G2 | SCR-08, SCR-09 | alongside M8 | M9's screens |
| G2 | SCR-10, SCR-09's HPO part | alongside M9 | M10's screens |
| G2 | SCR-13, SCR-03 v2 (distributions, diff and semantic clusters), SCR-11 v2 (comparison) | alongside M10 | M11's screens |
| G3 | Each screen compared with its mockup, contrast and keyboard checked | in the milestone that builds it | – |

- [x] G1 revision approved (SCREENS.md v2, 2026-11-17)
- [x] G2: SCR-04 v2 (low-fi v1 and hi-fi v1 approved 2026-11-17; `docs/design/screens/SCR-04v2_v1.html`)
- [x] G2: SCR-07 v2 (low-fi v1 and hi-fi v1 approved 2026-11-17; `docs/design/screens/SCR-07v2_v1.html`)
- [x] G2: SCR-11 (low-fi v1 and hi-fi v1 approved 2026-11-17; `docs/design/screens/SCR-11_v1.html`)
- [x] G2: SCR-14 (low-fi v1 approved 2026-11-17, hi-fi v1 approved 2026-11-18; `docs/design/screens/SCR-14_v1.html`)
- [x] G2: SCR-08 (low-fi v1 and hi-fi v1 approved 2026-11-19; `docs/design/screens/SCR-08_v1.html`)
- [x] G2: SCR-09 (low-fi v1 and hi-fi v1 approved 2026-11-19; `docs/design/screens/SCR-09_v1.html`; the HPO part with SCR-10, M10)
- [x] G2: SCR-10 and SCR-09's study part (low-fi v1 and hi-fi v1 approved 2026-11-19; `docs/design/screens/SCR-10_v1.html`, `docs/design/screens/SCR-09hpo_v1.html`)

## M8 — Training runtime and registry

**Size** L (one milestone, the owner's decision 8) · **Branch** `m8-training-registry` · **Serves** RUN-1.1–1.9, REG-1.1, REG-1.2, REG-1.4, REG-2.1–2.11, NFR-14, NFR-15; BUG7; NFR-1, NFR-4, NFR-5 and NFR-8 for training

**Before starting:** decisions 1 to 7; ADR-0014, ADR-0015 and ADR-0016 approved; the G1 revision approved; the phase-2 image dataset downloaded if decision 1 is CIFAR-10.

**Scope, part A: training runtime**

- Training as a job kind, one child process per run on the GPU or CPU queue, under the GPU lease and the 0.85 cap with a baseline check (ADR-0003, ADR-0006). Until the builder exists (M9), runs use reference models from code: an MLP and a small CNN.
- Input pipeline from a labelled-images dataset version: the labelled-images part of DATA-5.2, delivered early (the requirement is ticked in M9). Images are read from the blob store, resized once and cached as a derivation; loader workers are derived from the container's memory limit (ADR-0007, decision 4).
- Live metrics (RUN-1.8): loss, accuracy, learning rate, gradient norm, step time and samples/s as progress metrics; VRAM and RAM from the existing telemetry. Accuracy is the only quality metric until EVAL-1.2 exists.
- Checkpoints (RUN-1.3) on the decision-4 cadence, holding everything an exact resume needs; only the latest is kept during a run (REG-2.6).
- Pause and resume (RUN-1.2): the cooperative flag (SPEC §7.2), then a checkpoint, an exit and `paused`; resume queues a new attempt from the checkpoint. Pipeline jobs too, if decision 5.
- Thermal pause and resume (RUN-1.6) and stall detection (RUN-1.7) in the watchdog.
- Device choice (RUN-1.9): `auto` times a few steps on each device for small models and picks the faster; `cpu` and `gpu` force it. The choice and its timings are recorded on the run.
- 👤 VRAM manager v2 (RUN-1.4) and training out-of-memory handling (RUN-1.5).
- Spike S-08: the reference CNN on the chosen dataset, GPU against CPU: step time, peak VRAM and RAM, checkpoint size and save time. It sets the checkpoint cadence, RUN-1.9's threshold and the memory limits.
- Screens: SCR-07 v2 (pause, resume, training jobs) and SCR-04 v2 (loss and accuracy charts, checkpoints on the timeline).

**Scope, part B: registry, retention, inference**

- Registry tables (DATA_MODEL §9: lineages, versions, checkpoints, pins, retention settings). Every run registers a version with its resolved config, dataset version, metrics and weights (REG-1.1), and a state: base, partial or trained (REG-1.2).
- Weights as safetensors under `registry/` (NFR-8). A lineage's original weights are written first and never deleted by retention (REG-2.1).
- Retention as a job (REG-2.2 to REG-2.9): after every completed run and on demand, with a dry run. It logs each deletion and the bytes freed, never deletes weights in use or a parent's (REG-2.8), and deletes atomically per version: an intent row, then the files, then the metadata (NFR-15). Settings are global and per lineage (REG-2.4); pins exempt a version (REG-2.5); lowering N asks for confirmation with the versions and sizes affected (REG-2.10); a version whose weights were deleted keeps its metadata and can be re-trained (REG-2.7).
- Inference service (REG-1.4): the `inference` container (SPEC CL-07) holds at most one model, takes the GPU lease, verifies every unload, and serves `/predict` for every registered version, following decision 2.
- Screens: SCR-11 (versions, states, disk use per lineage, pins, retention dry run and confirmation, a `/predict` try-out) and SCR-14 (retention defaults and overrides, temperature limit, stall timeout).

**Done when**

- [x] Decisions 1 to 7 recorded; ADR-0014, ADR-0015 and ADR-0016 approved (2026-11-17; decision 8 too: M8 not split)
- [x] S-08 report delivered (2026-11-18, `docs/spikes/S-08-training.md`): the GPU is faster for both reference models; no tensor cache; checkpoint cadence kept. ADR-0007's limits not revised; per-container RAM with loader workers is measured with the training stage
- [x] `test_RUN_1_1_*`: a training job moves through every state; invalid transitions are rejected and every transition is logged (2026-11-18: `test_M8_training.py`, `test_M8_pause_resume.py`, `test_M8_watchdog.py`, with the state machine's unit tests)
- [x] `test_RUN_1_2_*`: start, pause, resume and cancel each work on a running job (2026-11-18: `test_M8_pause_resume.py` for pipeline jobs, `test_M8_training.py` for a training run, whose resumed weights match an uninterrupted run)
- [x] `test_RUN_1_3_*`: kill mid-training, resume from the checkpoint; the metrics match an uninterrupted run within NFR-4's tolerance (2026-11-18: the weights match within 1e-6 on the CPU, `test_M8_training.py`)
- [x] 👤 `test_RUN_1_4_*` (gpu): after a training model is unloaded, `torch.cuda.memory_allocated` is back at baseline (2026-11-19, written by the agent at the owner's request, ADR-0012: on the card after Adam steps; a GPU training run's own unload is proven too; inference now uses the GPU, `test_REG_1_4_an_inference_model_unloads_to_baseline_on_the_card`)
- [x] 👤 `test_RUN_1_5_*` (gpu): a deliberately oversized batch fails cleanly with a smaller batch suggested, and nothing crashes (2026-11-19, written by the agent at the owner's request: the CNN on 256-px images at batch 128 fails with `gpu_oom`, "reduce batch size from 128 → 64"; the next run at 64 completes in the same worker)
- [x] `test_RUN_1_6_*`: a simulated high temperature pauses the job; it resumes when the temperature falls (2026-11-18, `test_M8_watchdog.py`: 84 °C pauses the GPU job, 78 °C keeps it paused, 72 °C resumes it; CPU jobs and user pauses are left alone)
- [x] `test_RUN_1_7_*`: a simulated hang becomes `stalled` after T seconds (2026-11-18, `test_M8_watchdog.py`, T shortened to 1 s; it becomes running again when progress returns)
- [x] `test_RUN_1_8_*`: SCR-04 updates live during a training run (2026-11-18: progress events carry the metrics, `Training.test.tsx` derives the charts from them; checked live at SCR-04 v2's Gate 3)
- [x] `test_RUN_1_9_*`: a tiny model defaults to the faster device measured by the benchmark; a forced device is used as given (2026-11-18, `test_M8_training.py`: planted timings send a run to either device, a real measurement keeps the faster and is cached, a forced device skips the benchmark)
- [x] `test_REG_1_1_*`: any trained version traces to its exact dataset version and config (2026-11-18, `test_M8_registry.py`: the version's dataset version, config and hash match the run's; its safetensors load; later runs start from the base; a retry after registration trains nothing; job_0030–0032 backfilled)
- [x] `test_REG_1_2_*`: the three states are shown and filterable (2026-11-18, `test_M8_registry.py`: a paused run is `partial`, then `trained`, and both list filters work; 2026-11-19, `Models.test.tsx`: SCR-11 shows the three labels and filters by state and weights; checked live)
- [x] `test_REG_1_4_*`: `/predict` returns a result for a sample input, also while a training job holds the GPU (2026-11-18, `test_M8_inference.py`: on the CPU while a job holds the lease, `gpu_busy` for a model over the limit, one model at a time, unloads verified and the lease given back, on a fake card; checked live with job_0032's model. The GPU path on the real card waits for RUN-1.4: `tests/gpu/test_REG_1_4_inference_gpu.py`, a strict expected failure)
- [x] `test_REG_2_1_*`: base weights are still present after 10 training runs (2026-11-18, `test_M8_retention.py`: byte-identical after 10 runs)
- [x] `test_REG_2_2_*`: default settings, 3 runs: only base and run-3 weights on disk (2026-11-18, `test_M8_retention.py`; each deletion logged with its bytes)
- [x] `test_REG_2_3_*`: N = 2, 5 runs: base, runs 3 and 4, and run 5 on disk (2026-11-18, `test_M8_retention.py`, with the keep rule's unit tests)
- [x] `test_REG_2_4_*`: global N = 0 with lineage X at N = 3: only X keeps intermediates (2026-11-18, `test_M8_retention.py`: an MLP and a small CNN on one dataset)
- [x] `test_REG_2_5_*`: a pinned run 2 survives N = 0 cleanup (2026-11-18, `test_M8_retention.py`; unpinning deletes nothing by itself)
- [x] `test_REG_2_6_*`: a completed run leaves no checkpoint; a paused run is still resumable after cleanup (2026-11-18, `test_M8_retention.py`; a cancelled paused run's checkpoint is removed)
- [x] `test_REG_2_7_*`: a version whose weights were deleted still shows its metrics and offers re-training (2026-11-18, `test_M8_retention.py`: the re-train has the same config hash, data and base, and the same val loss on the CPU)
- [x] `test_REG_2_8_*`: pruning an in-use or parent version is blocked and logged (2026-11-18, `test_M8_retention.py`: loaded for inference, parent of a fine-tune, and Redis unreachable)
- [x] `test_REG_2_9_*`: the dry run lists what would go; the real run frees the bytes it reported (2026-11-18, `test_M8_retention.py`)
- [x] `test_REG_2_10_*`: the confirmation lists the exact versions and their sizes (2026-11-18, `test_M8_retention.py`: the API's `confirmation_required`; a changed plan is refused with a new one. SCR-11's dialog comes with its Gate 3)
- [x] `test_REG_2_11_*`: disk use per lineage matches the bytes on disk (2026-11-18, `test_M8_retention.py`: all five parts, against a walk of the folders)
- [x] `test_NFR_14_*` and `test_BUG7_*`: after 10 runs with default retention, the disk audit matches base + latest + one in-progress checkpoint (2026-11-18, `test_M8_retention.py`; BUG7's guard adds a cancelled pause and a crash with retry)
- [x] `test_NFR_15_*`: a kill during cleanup, then a rerun: no orphaned files and no dangling registry rows (2026-11-18, `test_M8_retention.py`: killed after marking `deleting`, plus an orphan folder; the retry recovers both)
- [x] `test_NFR_1_*` (gpu) and `test_NFR_8_*` extended to training: peak VRAM under 5.5 GB; weights only as safetensors, never unpickled (2026-11-19, `test_M8_nfr.py`: on the card, the job's logged peak reserved is 0.04 GB for the MLP and 0.23 GB for the CNN at batch 128, 0.79 GB for the CNN at batch 512, each plus 0.09 GB of context. A run and its resume, and a `/predict`, unpickle nothing under a tripwire; checkpoints and the registry hold only safetensors and JSON; a pickle planted as weights or a checkpoint fails with `weights_unreadable` and its payload never runs; `test_NFR_8_no_unpickling.py` scans `src/` and `scripts/` for any call that unpickles)
- [x] G3 for SCR-07 v2, SCR-04 v2, SCR-11 and SCR-14 (SCR-07 v2 and SCR-04 v2 approved 2026-11-18; SCR-11, SCR-14 and the registry additions to SCR-07 v2 and SCR-04 v2 approved 2026-11-19: `docs/design/g3/SCR-11.md`, `SCR-14.md`, the addenda in `SCR-07.md` and `SCR-04.md`)
- [x] Demo: train the reference CNN on the phase-2 image dataset; pause and resume it from the UI; find it in the registry; run retention; get a prediction (2026-11-19, README "Training demo": `job_0051`, 30 epochs on the GPU, 84.3 %; paused and resumed on SCR-07 at the checkpoint's step 3,244; `partial`, then `trained` and latest on SCR-11; retention `job_0052` deleted job_0030's and job_0031's weights and kept the pinned `mv_e80834ce01b0`, as the owner chose; the dry run and disk report on SCR-11 agree; SCR-11's try-out predicted on the CPU in 146 ms)
- [ ] Docs updated (ARCHITECTURE, DATA_MODEL, API, ERRORS for BUG7; learning notes)
- [x] Owner's OK to merge ("Merge M8", 2026-11-19)

## M9 — Model builder

**Size** L · **Branch** `m9-model-builder` · **Serves** BUILD-1.1–1.9, UI-2.3, DATA-5.2, DATA-5.5

**Before starting:** ADR-0017 approved, including the tabular input width and categorical embeddings (M10's open points 8 and 10); G2 for SCR-08 and SCR-09 approved; React Flow approved as a frontend dependency (SPEC §7.4 names it; `@xyflow/react` 13.0.1 is already installed).

**Decided by the owner at M9's kickoff (2026-11-19):** BUILD-1.8 is written by the agent (ADR-0012's log); BUILD-1.9 uses full graphs (nodes and any edges), not a residual wrapper; a tabular network takes its categorical columns through an input block, one-hot by default with an `Embedding` per column as an option (M10's points 8 and 10); torchinfo is added as a test-only dependency in `dev` (BUILD-1.5).

**Scope**

- Layer specs as Pydantic models with validated ranges and defaults (BUILD-1.1, BUILD-1.2): Linear, Conv2d, MaxPool, BatchNorm, Dropout, Flatten, Embedding, RNN, LSTM, GRU, the attention block and activations. Their JSON schemas generate the forms, so a new parameter needs no UI code (UI-2.3).
- Shape inference layer by layer, blocking an invalid stack before training (BUILD-1.4), with the input and output locked from the dataset type (BUILD-1.3).
- Cross-field rules, each with its own test (BUILD-1.6). Live parameter count and memory estimate (weights, gradients, optimizer state), checked against `sum(p.numel())` and torchinfo (BUILD-1.5).
- Compilation of a spec to a PyTorch module for M8's runtime, so built models train exactly like the reference ones.
- Templates (BUILD-1.7). Graph specs with skip connections (BUILD-1.9, P2, if time).
- 👤 The from-scratch attention block, positional encoding and LSTM cell (BUILD-1.8).
- Dataset types (DATA-5.2): labelled images (from M8), tabular and labelled text schemas. Each type declares its compatible model families and default metrics (DATA-5.5), and the UI offers only compatible combinations.
- Screens: SCR-08 (canvas) and SCR-09 (training run setup; its HPO part comes in M10).

**Done when**

- [x] ADR-0017 approved (2026-11-19, "Approved": `docs/decisions/ADR-0017-model-spec.md`)
- [x] `test_BUILD_1_1_*`: every layer type can be added and configured (2026-11-19, `test_BUILD_1_spec.py`: all 23 types in two valid graphs; the schema lists each with its defaults; a graph compiles through the train factory)
- [x] `test_BUILD_1_2_*`: out-of-range values are rejected with a message (2026-11-19: field errors naming the field, unknown types and fields refused; through the API a 422; SCR-09 sends the form's values)
- [x] `test_BUILD_1_3_*`: changing the dataset updates the input and output (2026-11-19: 32- and 64-px splits through the API; on SCR-08 the locked ends change with the split)
- [x] `test_BUILD_1_4_*`: a mismatched linear input is caught at build time (2026-11-19: `shape_mismatch` on its node, in plain Python, through the API, at `POST /jobs` as a 422, and on SCR-08)
- [x] `test_BUILD_1_5_*`: parameter count and memory estimate match `sum(p.numel())` and the formula (2026-11-19, `test_BUILD_1_parity.py`: 300 random specs match PyTorch's shapes, `sum(p.numel())` and torchinfo; the formula per optimizer; SCR-09's VRAM check)
- [x] `test_BUILD_1_6_*`: one test per cross-field rule (`d_model % num_heads == 0`, a conv stack never below 1×1, …) (2026-11-19: 15 rules, one test each, and no cascade)
- [x] `test_BUILD_1_7_*`: a saved template reloads identically (2026-11-19: canonical form and structure hash through the API, however the spec was listed; built-ins locked; SCR-08's start screen)
- [x] 👤 `test_BUILD_1_8_*`: attention, positional encoding and the LSTM cell match PyTorch's outputs within tolerance (2026-11-19, written by the agent at the owner's request: attention within 1e-5 of `nn.MultiheadAttention` with and without a causal mask, the LSTM cell within 1e-6 of `nn.LSTMCell` step by step, positional encoding against the formula; no ready-made layer in a forward)
- [x] `test_BUILD_1_9_*` (P2, if time): a residual block builds and trains (2026-11-19: full graphs, as the owner chose; a residual block's loss halves in 60 steps; a residual graph trains, registers and reloads through the stage)
- [x] `test_UI_2_3_*`: a field added to a config schema appears in its form with no UI change (2026-11-19, `Builder.test.tsx`: a new Conv2d field appears in SCR-08's inspector)
- [x] `test_DATA_5_2_*`: each type validates against its schema (2026-11-19: tabular and labelled-text row validators, the tabular input block; labelled images since M8)
- [x] `test_DATA_5_5_*`: the UI offers only compatible type and model combinations (2026-11-19: `incompatible_family` from the API; SCR-08's palette greys the rest with the reason; templates listed per type)
- [x] G3 for SCR-08 and SCR-09 (approved 2026-11-19, "Approved": `docs/design/g3/SCR-08.md`, `SCR-09.md`)
- [x] Demo: build a CNN on the canvas, train it from SCR-09, follow it on SCR-04, find it in SCR-11 (2026-11-19: `my_model`, eight layers added from the palette, 10,602 parameters; `job_0053` chose the GPU by benchmark, 10 epochs in 65 s, val acc 53.6 %, peak 302 MB; `mv_0cfa1ce88a16` in `my_model · cifar10` on SCR-11. README "Model builder demo")
- [x] Docs updated (learning notes) (2026-11-19: API.md §3.11 and the M9 interfaces, DATA_MODEL §4.14, ARCHITECTURE's code layout, ADR-0017; `docs/learning/` §26 and §5k)
- [x] Owner's OK to merge ("Merge M9", 2026-11-19)

## M10 — Classical ML, HPO, task metrics

**Size** L · **Branch** `m10-ml-hpo-metrics` · **Serves** ML-1.1–1.5, HPO-1.1–1.5, EVAL-1.2, DATA-1.4, DATA-1.5, DATA-2.4

**Decided by the owner at M10's kickoff (2026-11-19):**

| # | Decision |
|---|---|
| K1 | EVAL-1.2 is written by the agent, all of it ("You write em"; ADR-0012's log) |
| K2 | EVAL-1.2 uses libraries in production: sklearn.metrics, sacrebleu for BLEU and chrF; CIDEr-D stays in house (CL-13). Which of torchmetrics' metrics are still needed is settled in ADR-0019 |
| K3 | Text is tokenized into words with a vocabulary from the train split; no BPE owner unit and no tokenizer library (open point 14) |
| K4 | Downloads approved as listed: scikit-learn 1.9.1, scipy 1.18.1, joblib, narwhals, threadpoolctl, skops 0.16.0, prettytable, Optuna 5.1.0 and colorlog in `worker-cpu` (`ml-cpu`); Optuna and colorlog in `worker-gpu` (`gpu`); about 47 MB from PyPI. `api` and `monitor` stay without them; their tests get a marker and run in `worker-cpu`. sacrebleu 2.6.0 (with lxml, about 6 MB) follows from K2 |
| K5 | One new ADR-0019 settles open points 1–7, 9 and 14–17; the CSV labels (11–13) become a proposed requirement, DATA-1.5, in SPEC |

**Before starting:** ADR-0018 approved; G2 for SCR-10 and SCR-09's HPO part approved; the tabular and labelled-text datasets settled (decision 1); and these open points decided, in an ADR of their own or an addendum to ADR-0016 (raised by the owner on 2026-11-18; the owner chose ADR-0019 at the kickoff, K5):

- **Serving tabular models** (REG-1.4, ML-1.5):
  1. Which process serves skops models. `inference` is built from the `worker-gpu` image, which has neither scikit-learn nor skops (ADR-0014 rule 11 puts them in `ml-cpu`). They run on the CPU, so they never need the GPU lease.
  2. The `/predict` request and response for a table. In: one record of named fields, checked against the dataset's column schema. Out, by task: a class with probabilities, a value, a cluster ID or a PCA projection.
  3. The registry storing skops files beside safetensors (ADR-0015): storage, disk use per lineage (REG-2.11) and retention.
  4. Whether batch prediction (a CSV in, predictions out) and serving cluster assignments are in scope.
- **Neural networks on tabular data** (BUILD-1.3, ML-1.3, RUN-1.1):
  5. `train` requests accepting a tabular split, with a loader that turns rows into a feature tensor and a target. Today only `labelled_images` splits are accepted.
  6. How the target column is named: in the request or in the dataset's metadata.
  7. Preprocessing for networks (scaling, encoding, imputation) fitted on the train split only, as ML-1.3 requires of pipelines. It is stored with the weights (for example as JSON beside the safetensors) and applied again by `/predict`.
  8. The input width taken after encoding, since one-hot encoding widens it, so the builder can lock it (BUILD-1.3).
  9. Regression in the training runtime: MSE loss and regression metrics. Today it trains classifiers only.
  10. Categorical columns through `Embedding`: a tabular input block, or graph specs (BUILD-1.9, P2).

  Points 8 and 10 shape the model spec, so they belong in ADR-0017, before M9.

- **Labels for images from a CSV** (approved as DATA-1.5 on 2026-11-19; proposed requirement, added to this plan at the owner's request on 2026-11-19; written into SPEC with an ID and acceptance criteria at M10's kickoff, for the owner's approval). Today ingest takes an image's class only from its first folder (`label_from: folder`). Many ready-made and hand-made datasets instead ship one folder of images and a CSV mapping each file to its label (`path, label`). Proposed: a second source of labels for labelled images, e.g. `label_from: {csv: labels.csv, path_column, label_column}`, alongside DATA-1.4's CSV ingestion. To settle:
  11. Paths in the CSV are relative to the source folder; an image with no row is rejected with a reason code (as `no_label` is now); a row whose file is missing is reported, not silently dropped.
  12. The CSV's content is part of the version's identity (DATA-3.2), so editing a label makes a new version.
  13. Scope: single-label classification only. Several labels per image (multi-label) and a number per image (image regression) need training-runtime changes and are not part of this.
- **Tokenizing text** (open point, added to this plan at the owner's request on 2026-11-19). The builder takes text as integer tokens with a vocabulary size, both locked from the dataset (ADR-0017 rule 5), but nothing on the roadmap turns text into tokens or stores the vocabulary. Training a labelled-text model needs it, and M12's corpora will too. To settle, in an ADR or an addendum to ADR-0017:
  14. The kind of tokenizer: characters, words with a vocabulary from the train split, or subwords (BPE). A library such as `tokenizers` is a new dependency (ask first); writing BPE from scratch could be an owner unit.
  15. Fitted on the train split only (as ML-1.3 requires of preprocessing), with a fixed vocabulary size, reserved IDs (0 for padding, as the built-in text templates assume with `padding_idx: 0`, and one for unknown tokens), and deterministic IDs, so the same data and settings always give the same tokens.
  16. Where it lives and what it is part of: a stage that writes a versioned derivation (so the tokens and vocabulary are part of a version's identity, DATA-3.2), or a step inside training stored with the weights; either way `/predict` must tokenize the same way.
  17. The maximum length L (the builder's locked input), with truncation and padding, and the length distribution shown so L can be chosen (SPEC's "Tokenizing corpus, vocab 12,480" activity).

**Scope**

- Tabular (CSV, Parquet) and text (TXT, JSONL) ingestion into manifests (DATA-1.4). Text cleaning: whitespace and Unicode normalised, empty and over-length rows dropped and logged (DATA-2.4).
- Classical models (ML-1.1) and k-means, DBSCAN/HDBSCAN and PCA (ML-1.2) as scikit-learn pipelines. Preprocessing is fitted on the train split only (ML-1.3); invalid parameter combinations are refused (ML-1.4); models are saved with skops (ML-1.5, NFR-8). They run on the CPU queue and register like any model.
- HPO (HPO-1.1 to HPO-1.5): tunable markers on any schema field; Optuna TPE with a trial count and a time budget; median pruning with the stopping step logged; tuning on validation, or cross-validation for classical ML; the test split used once, on the best config; trial table, best config and parameter importance.
- 👤 Task metrics by type (EVAL-1.2): classification, regression, clustering, LM perplexity, translation BLEU and chrF, captioning CLIP score and CIDEr-D (CL-13). Shown on SCR-11's version page.
- Spike S-09: Optuna on PostgreSQL, the overhead per trial, grouped against one job per trial.
- Screens: SCR-09's HPO part and SCR-10.

**Done when**

- [x] ADR-0018 approved; S-09 report delivered (ADR-0018 approved 2026-11-19, "Approve all three"; `docs/spikes/S-09-hpo.md` 2026-11-19: Optuna stays in its schema, decision 7 confirmed, importance uses fANOVA set explicitly)
- [x] ADR-0019 approved (open points 1–7, 9, 14–17); DATA-1.5 approved or declined (points 11–13) (both approved 2026-11-19, "Approve all three")
- [x] `test_DATA_1_5_*`: a flat image folder with a labels CSV ingests with each image's label; unlisted images, missing files and repeated paths are reported; one changed label gives a new version ID (2026-11-19: `test_DATA_1_4_records.py`, through the API in `test_M10_records_pipeline.py`; conflicting labels rejected, problems with their line numbers in `counts.labels_csv`)
- [x] `test_DATA_1_4_*`: a sample CSV and a sample JSONL ingest without errors (2026-11-19: CSV, Parquet, JSONL and TXT in `test_DATA_1_4_records.py`; whole pipelines in `test_M10_records_pipeline.py`; live, breast cancer, diabetes and wine (`job_0059`–`job_0061`) and the COCO captions (`job_0062`))
- [x] `test_DATA_2_4_*`: planted bad rows are removed and logged (2026-11-19, `test_DATA_2_4_text_clean.py`: empty and over-length texts dropped with their reasons and a WARN naming them; through the API in `test_M10_records_pipeline.py`)
- [x] `test_ML_1_1_*`: each listed model trains on a tabular dataset (2026-11-19, `test_ML_1_classical.py`: every estimator on its tasks, 16 cases; through the API and the runtime, `test_M10_classical.py`, `test_M10_classical_run.py`; live, a random forest on breast cancer, `job_0063`: val accuracy 0.982, test 0.965)
- [x] `test_ML_1_2_*`: k-means, DBSCAN/HDBSCAN and PCA each run and report their metrics (2026-11-19, `test_ML_1_classical.py`: clusters, noise, silhouette and Davies–Bouldin; PCA's explained variance)
- [x] `test_ML_1_3_*`: the scaler's statistics come from the train split (2026-11-19, `test_ML_1_classical.py`: the scaler's mean, the imputer's median and the encoder's categories match train with val and test shifted away)
- [x] `test_ML_1_4_*`: invalid parameter combinations are rejected (2026-11-19, `test_ML_1_4_params.py`: one test per rule, the task and data checks; through the API as 422s naming the field, `test_M10_classical.py`)
- [x] `test_ML_1_5_*`: a skops save and load round-trips; nothing is unpickled (2026-11-19, `test_ML_1_classical.py` and `test_M10_classical_run.py`, under the unpickling tripwire; a file holding a type outside the allow-list is refused as `weights_unreadable`)
- [x] `test_HPO_1_1_*`: the search space builds from the UI's selections (2026-11-20: `test_HPO_1_1_space.py`, markers checked against each field's own range and the fixed fields; through the API, `test_M10_studies.py` and `test_M10_deep_studies.py`; SCR-09's Tune toggles to markers and refusals on their rows, `Builder.test.tsx`)
- [x] `test_HPO_1_2_*`: a study stops at whichever limit comes first (2026-11-20: trial limit and time budget for classical studies, `test_M10_studies_run.py`, and for networks, one job per trial, `test_M10_deep_studies_run.py`; pause, cancel and the watchdog on a waiting study, `test_M10_deep_studies.py`; two studies starting at once)
- [x] `test_HPO_1_3_*`: pruned trials are logged with the step they stopped at (2026-11-20: by fold for classical studies, by epoch for networks, never before 5 trials have finished; SCR-10 says the step and reason, `Study.test.tsx`)
- [x] `test_HPO_1_4_*`: test metrics are computed exactly once per study (2026-11-20: a conditional update refuses a second write, classical and network studies; trials never see the test split; SCR-10's card has no button)
- [x] `test_HPO_1_5_*`: the trial table, best config and parameter importance render (2026-11-20: the study endpoints, `test_M10_studies_run.py`; SCR-10, `Study.test.tsx`; *Start a run from this configuration* prefills SCR-09)
- [x] 👤 `test_EVAL_1_2_*`: the right metric set appears for each task (written by the agent, K1. 2026-11-19, `test_EVAL_1_2_task_metrics.py`: every task's set from `sklearn.metrics`, sacrebleu and CIDEr-D, and every network run scored on validation and test; 2026-11-20, shown on SCR-11's version drawer, `TaskMetrics.test.tsx`, after its own G2 and G3 (approved 2026-11-20); checked live on classification, regression and a 78-class text model)
- [x] G3 for SCR-09 (HPO part) and SCR-10 (approved 2026-11-20, "Approve all four", with the SCR-07 and SCR-04 study addenda; `docs/design/g3/SCR-10.md`)
- [x] Demo: tune a random forest on the tabular dataset and the CNN's learning rate; read both studies; one test evaluation each (2026-11-20, on the live stack, read on SCR-10: a random forest on breast cancer, `st_e06085ead155` (`job_0077`), 40 trials, 13 pruned by fold, best cv_accuracy 0.963, tested once: accuracy 0.965, macro F1 0.961; the Small CNN's learning rate on CIFAR-10 on the GPU, `st_8293da6ebe0e` (`job_0095`), 8 trials of 4 epochs, one pruned at epoch 2, best lr 0.0012 at val accuracy 0.804, registered as `mv_a4a0fb846812` and tested once: accuracy 0.810, macro F1 0.809. Also a two-layer MLP on wine, `st_5869c7673960`)
- [x] Docs updated (learning notes) (2026-11-20: API.md §3.9, §3.3 and §3.12; ADR-0018 "As built" parts A and B; DATA_MODEL §4.15; ARCHITECTURE; DECISIONS; the Gate 3 notes; `docs/learning/Decisions.md` §27 and `sys-design.md` §5l, not committed)
- [x] Owner's OK to merge ("Yes, merge and push", 2026-11-20)

## M11 — Dashboards, monitoring completion, phase-2 acceptance

**Size** L · **Branch** `m11-phase2-acceptance` · **Serves** MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4, MON-4.5, UI-2.1, UI-2.2, EVAL-1.3, DATA-3.5, RUN-1.10; DATA-2.3 and DATA-4.8 (phase 1, P2, moved from M7); the phase-2 done-definition

**Decided by the owner at M11's kickoff (2026-11-20, "Accept all twelve"):**

| # | Decision |
|---|---|
| K1 | DATA-2.3 and DATA-4.8 are written by the agent (ADR-0012's log). The threshold is proposed from a measurement on coco10k in `worker-gpu`: planted crops of one image against unrelated pairs and against pairs of one category; for each candidate threshold the clusters, the rows in them and the largest cluster (union-find chains pairs, so a low threshold can merge a large share of the rows and upset the split's proportions). If that happens, an ADR-0013 amendment is proposed rather than a workaround |
| K2 | DATA-4.8.AC1's "one scene from different angles" is a synthetic pair: two overlapping crops of one COCO photo, one warped in perspective, checked far apart by pHash and close by CLIP. No download. The owner may add two of their own photos of one scene under `sources/` for a live check |
| K3 | A new ADR-0020, alerts and diagnostics (MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4); RUN-1.10 as an addendum to ADR-0014 and ADR-0015. Each is approved before what it covers is built |
| K4 | Alerts are evaluated in `monitor` (NFR-13: if it crashes, alerts stop and jobs go on). Temperature pauses (RUN-1.6), no progress marks the job stalled (RUN-1.7), VRAM > 90 % and RAM > 85 % warn only; an out-of-memory failure after a VRAM warning is linked to it (the smaller-batch action, RUN-1.5). An alert fires after 5 s over its threshold, once per crossing, and re-arms 5 points below it. With no job running it is stored without one and shown on SCR-13. An action records the alert that caused it (a schema change, DATA_MODEL.md) |
| K5 | `process_samples` gains the job process's CPU in cores (a migration). `monitor` compares each running stage's expected resource (the MON-3 table) with what it observes over 30 s; three first rules for a GPU stage with the GPU under 30 %: the job's CPU saturated → data loading is the bottleneck, increase DataLoader workers; high disk reads → disk is the bottleneck; everything low → small batches or waiting on synchronisation. A diagnostic is a WARN event with its reason and action (in SCR-04's log), and SCR-13 has a panel for them |
| K6 | MON-3.3 shows PyTorch's allocated memory (the process's own) and, beside it, the card's NVML figure |
| K7 | MON-4.4 aligns two runs by time since start with pauses removed; two training runs can also be aligned by epoch |
| K8 | EVAL-1.3 compares only versions tested on the same split version, across lineages, classical and network alike; others are greyed with the reason. No new evaluation jobs in M11, with one exception approved with SCR-11 v2's low-fi on 2026-11-20: *Score on test* runs the `test_eval` stage once for a version with weights and no test metrics (trained before M10); a version with test metrics is never scored again, so HPO-1.4's "computed once" holds. Accuracy with a Wilson 95 % interval, per-class F1 side by side. No paired test (it needs per-item predictions, which are not stored) |
| K9 | RUN-1.10: the new version joins the lineage its key gives (same spec, dataset, shape and classes: the same lineage; otherwise a new one whose parent is in another). Retention protects any version that is the parent of a version with weights. Layers are frozen by spec node ID, and a frozen layer is in eval mode, so its BatchNorm statistics stay too. The optimizer starts fresh. One validation pass before the first step, logged as epoch 0 (AC1). Tables reuse the parent's fitted preprocessing; texts are refused unless the vocabulary is identical, naming the mismatch; classical models are refused; in a study `init_from` is a fixed field, not tunable |
| K10 | UI-2.2's distributions and DATA-3.5's diff are computed by the API from the manifests on request, cached by version ID. The diff compares two versions of one dataset by `row_key`; "changed" names the fields that differ. DATA-3.5 and MON-4.5 (P2) stay "if time" |
| K11 | G2 order: SCR-09 v2 (fine-tuning form), SCR-11 v2 (comparison, the parent, *Try it* for table records and texts, M10's open point 2), SCR-03 v2 (distributions, diff, semantic clusters, models trained from the version: UI-2.1's training part), SCR-13. Low-fi, then hi-fi, one screen at a time. SCR-14's monitoring section and SCR-04's alert and diagnostic lines are addenda checked at G3 without a mockup |
| K12 | `inference` 1.0 → 1.25 GB and `worker-gpu` 3.5 → 3.25 GB (the sum stays 9,024 MiB; both peaks checked in the acceptance run). Phase-2 acceptance: the Small CNN template on the CIFAR-10 split, 10 epochs, through the API with the spec SCR-08 produces (SCR-08 itself checked at the demo); NFR-1 and NFR-2 during training; NFR-4 by two runs with one seed, test accuracy within 1 percentage point; NFR-5 by killing the worker mid-run, at most 10 minutes lost; a README section. NFR-11 rerun with `-Bench`, after asking for a quiet machine. M10's open point 3 (a study's stage duration on SCR-04) fixed as BUG8 with `test_BUG8_*`; open point 4 (the pruning test that failed once) watched in every `check.ps1 -Gpu`. No new dependency is expected; any that arises is asked for first |

**Before starting:** G2 for SCR-09 v2 (RUN-1.10), SCR-13, SCR-03 v2 and SCR-11 v2 approved. The alerts and diagnostics add work to `monitor`, so NFR-11 is measured again here.

**Scope**

- Alerts (MON-2.6) at the decision-4 thresholds, each shown with the action taken (pause, fail, smaller batch) in the event log (MON-2.7).
- Bottleneck diagnostics (MON-3.2): the expected and observed resource per stage (the MON-3 table), for example GPU under 30 % with CPU at 100 % gives "data loading is the bottleneck: increase DataLoader workers".
- Load and unload events with VRAM before and after (MON-3.3), from labelling, training and the inference service.
- Two runs' resource curves overlaid (MON-4.4) and trends across past jobs (MON-4.5, P2, if time) on SCR-13.
- Raw against cleaned distributions and rejection reasons per stage (UI-2.2), and the diff of two versions (DATA-3.5, P2, if time), on SCR-03.
- Semantic duplicates (DATA-2.3) and each cluster kept in one split group (DATA-4.8), as [ADR-0013](decisions/ADR-0013-semantic-duplicates.md) sets out. Written by the agent at the owner's request (2026-11-17); the similarity threshold is proposed from a measurement on coco10k and chosen by the owner (as OC-5). Clusters shown on SCR-03.
- Three or more models on one test split, side by side (EVAL-1.3), on SCR-11.
- Fine-tuning a built model (RUN-1.10, added at the owner's request on 2026-11-19): `init_from` a registered version, optional frozen layers, `reset_head` for a different class count; the parent recorded and protected by retention. Its form joins SCR-09 (a G2 revision of SCR-09 before M11's screens) and the parent shows on SCR-11.
- **Choosing the model kind after the split** (UI change for ML-1.1, BUILD-1.3, UI-2.1; requested by the owner on 2026-11-20, "it should be decided after selection of the split or dataset"; draft, the details below await the owner's approval at G1). No requirement changes.
  - *Today.* The only way into SCR-09 is SCR-11's *Build a model…*, then SCR-08 (networks only: a table gets just the Tabular MLP template), then *Continue to training*; SCR-09's *Network | Classical* switch appears only there. A classical model therefore needs a network built first and thrown away, and a direct `/runs/new` without a version shows no switch at all. This happened because classical models joined SCR-09 in M10 (approved with its study part's low-fi, 2026-11-19) while M9's entry path, written for networks, stayed as it was.
  - *Proposed.* One entry, *Train a model…*, on SCR-11 (replacing *Build a model…*) and on SCR-03 for a split version. Step 1: the dataset and its split version, with its type and task. Step 2: the kinds that split allows, each in a line: *Network* (build or start from a template → SCR-08) for every trainable type; *Classical* (estimator → SCR-09 in classical mode, no builder) for tables only, shown greyed with "Classical models train on tables" otherwise, so the reason is visible.
  - SCR-09 receives the kind already chosen: the switch becomes the chosen kind with *Change…*, which goes back to step 2 and says when a network spec would be left behind. `/runs/new?version=` without a kind opens step 2. SCR-08's dataset selects are filled from step 1.
  - Steps 1 and 2 as a drawer shared by SCR-11 and SCR-03, like the *Start pipeline* drawer, not a new screen (the owner may choose a screen at G1). *Train again…* on a lineage and *Start a run from this configuration* on SCR-10 keep going straight to SCR-09.
  - Gates: a G1 revision (SCREENS.md: SCR-03, SCR-08, SCR-09 and SCR-11's actions; Flow 4's steps 1–3), then its G2 folded into SCR-09 v2, the first in K11's order, and its G3 with SCR-09 v2.
- UI-2.1 completed: models and training runs join datasets and pipeline jobs in the dashboard.
- The phase-2 acceptance run, measured, with a README section; phase 3 (M12, M13) detailed for approval.

**Done when**

- [x] ADR-0020 (alerts and diagnostics) approved (K3) (2026-11-22, "Approve as proposed", with both recommendations: `gpu_hot` re-arms at the resume limit; the thermal pause waits for the 5 s hold)
- [x] The RUN-1.10 addendum to ADR-0014 and ADR-0015 approved (K3) (both Addendum A, 2026-11-20, "Approve both")
- [x] G1 revision for choosing the model kind after the split approved (SCREENS.md, Flow 4); its G2 approved with SCR-09 v2 (G1 v3 and G2 approved 2026-11-20)
- [ ] G2 for SCR-09 v2, SCR-11 v2, SCR-03 v2 and SCR-13 approved, in that order (K11) (SCR-09 v2 and SCR-11 v2 passed 2026-11-20, SCR-03 v2 2026-11-21)
- [x] Semantic-duplicate threshold chosen by the owner from the coco10k measurement (K1) (2026-11-21: **0.90**, the recommendation of spike S-10, `docs/spikes/S-10-semantic-threshold.md`; DATA-4.8.AC1's pair: two 90 % opposite-corner crops, one warped 6 %)
- [ ] `test_MON_2_6_*`: each alert fires in a simulated test
- [ ] `test_MON_2_7_*`: an alert and its action are shown together in the event log
- [ ] `test_MON_3_2_*`: the diagnostic fires in a simulated slow-loader test
- [ ] `test_MON_3_3_*`: an unload event shows VRAM before and after
- [ ] `test_MON_4_4_*`: two runs' curves overlay
- [ ] `test_MON_4_5_*` (P2, if time): trends shown for the last N jobs
- [ ] `test_UI_2_1_*`: datasets, lineage and jobs with live metrics render real data, training included
- [x] `test_UI_2_1_*` (training entry, Vitest): a tabular split reaches SCR-09's classical mode without SCR-08; a non-tabular split shows *Classical* greyed with its reason; SCR-09 opened without a kind goes to step 2; G3 passed with SCR-09 v2 (the entry's G3 approved 2026-11-20; fine-tuning's G3 follows with RUN-1.10)
- [ ] `test_UI_2_2_*`: charts for any dataset version
- [x] `test_EVAL_1_3_*`: three or more models compared in one view (2026-11-21: `test_M11_compare.py`, three networks scored on one split with Wilson intervals, refusals and Score on test; Vitest `Compare.test.tsx`. Live: wine's three networks (0.944, 1.000, 1.000 on 18 rows, intervals overlapping) and CIFAR-10's three after Score on test, `job_0108`. Gate 3 for SCR-11 v2 approved 2026-11-21)
- [x] `test_RUN_1_10_*`: a run from a version starts at its weights and records it as parent; frozen layers are unchanged; a shape mismatch is refused unless the head is reset (2026-11-21: `test_M11_fine_tune.py`, 11 tests, and `test_RUN_1_10_config.py`, 8; Vitest `FineTune.test.tsx`. Live on the GPU, `job_0106` fine-tuned `mv_a014046ec3b5` with `n1`–`n12` frozen: epoch 0 val loss 0.7004 = the parent's 0.7004, the 24 frozen tensors unchanged on disk, registered `mv_7f950100ab48` with its parent. Gate 3 for SCR-09's fine-tuning approved 2026-11-21)
- [ ] `test_DATA_3_5_*` (P2, if time): the diff lists the changes correctly
- [ ] `test_DATA_2_3_*` (P2): cropped variants are grouped together and a report is generated
- [ ] 👤 `test_DATA_4_8_*` (P2): two photos of one scene from different angles land in the same split (written by the agent at the owner's request)
- [ ] G3 for SCR-13, SCR-03 v2 and SCR-11 v2 (SCR-11 v2 approved 2026-11-21)
- [ ] Phase-2 acceptance (`tests/acceptance`): a CNN built on SCR-08, trained on a dataset version, test metrics on SCR-11; NFR-1, NFR-2, NFR-4 and NFR-5 measured for training
- [ ] `test_NFR_11_*` rerun with alerts and diagnostics on
- [ ] `test_BUG8_*`: a study job's stage duration on SCR-04 covers all its steps, not the last (M10's open point 3)
- [ ] `inference` at 1.25 GB and `worker-gpu` at 3.25 GB, both peaks measured in the acceptance run (K12)
- [ ] Meta-test green: every phase-2 P0 (32) has a passing ID-named test
- [ ] M12–M13 detailed and approved by the owner
- [ ] Owner's OK to merge; tag `phase-2`

## M11b — Tabular transforms

**Size** M · **Branch** `m11b-tabular-transforms` · **Serves** DATA-7.1–7.5 · Added at the owner's request on 2026-11-19. The letter keeps M12–M15's numbers (as proposed for M8a/M8b). It comes after M11 because it needs M10's tabular ingestion and training. The `phase-2` tag goes on M11's merge, as planned; M11b follows it (the owner's choice, 2026-11-19: M11b carries no phase-2 acceptance criterion).

**Before starting:** an ADR (or an ADR-0019 addendum) for the `transform` stage: the operation list's schema, the expression language for DATA-7.4 (parsed, never `eval`), how encodings reach the table schema (decided 2026-11-19: the schema gives each column's default, a network's input block may still switch a categorical column between `one_hot` and `embedding`, classical pipelines follow the schema with `embedding` as `one_hot`; pointers in ADR-0017 rule 6 and ADR-0019 rule 17). Two points from M10's data layout to settle in that ADR: (1) ingest rejects a record whose target is empty (`missing_target`) before a transform could choose another target, so choosing the target in `transform` (DATA-7.2) needs ingest to keep those rows, or the transform to reconsider them; (2) `record` holds values already coerced to their inferred kind (a code `007` read as numeric is `7.0`), so changing a column's kind in `transform` cannot recover the original text unless ingest keeps it, or the kind is set at ingest (`stages.ingest.table.kinds`). A transform must also recompute each row's `sha256` from its new `record`, which the clean stage's exact duplicates rely on; a G1 revision adding the transform editor as a new screen (SCR-15 proposed) and SCR-03's column view; G2 for both.

**Scope**

- The `transform` stage between `ingest` and `clean` for tabular versions; row-local operations only (DATA-7.1, DATA-7.2, DATA-7.4).
- Per-column encodings in the table schema, used by the input block and the classical pipelines (DATA-7.3).
- SCR-03's column view for any tabular version, and the editor with a preview on sample rows (DATA-7.5).

**Done when**

- [ ] ADR approved; G1 revision and G2 for SCR-15 and SCR-03's column view approved
- [ ] `test_DATA_7_1_*`: raw unchanged, same operations give the same ID, fitted operations refused
- [ ] `test_DATA_7_2_*`: one test per operation; invalid ones refused naming the column; `cast_failed` rows kept and counted
- [ ] `test_DATA_7_3_*`: `ignore`, `embedding` and `ordinal` reach the model's input as chosen
- [ ] `test_DATA_7_4_*` (P2): derived columns and filters correct; anything outside the expression language refused
- [ ] `test_DATA_7_5_*`: the column summary matches the manifest; the preview equals the stage's output
- [ ] G3 for SCR-15 and SCR-03's column view
- [ ] Demo: drop a column of the wine table, change one column's kind, set an encoding, train on the result, and trace it back to raw on SCR-03
- [ ] Owner's OK to merge

---

## Later phases (outline)

Every numbered requirement is assigned to exactly one milestone. Scope and checklists are written at each phase kickoff. M8–M11 were detailed and approved on 2026-11-17 (above), with the requirements their outline rows gave them; those rows are replaced by the detail.

| | Scope | Requirements |
|---|---|---|
| M12 | Local LLM serving, RAG with citations, hit@k evaluation, abstention, image search | LLM-1.1, 2.1–2.4; DATA-5.3; NFR-6 |
| M13 | Router and fallback, tool-calling assistant, caption refinement and comparison, QLoRA | LLM-3.1, 3.2, 4.1, 5.1; LABEL-1.6, 1.7 |
| M14 | Image generation, LoRA fine-tuning, before/after evaluation, remote offload round trip, adapters as deltas | IMG-1.1–1.4; REG-1.3 |
| M15 | Translation serving and fine-tuning, parallel-text type, scratch vs pretrained, chat UI in manual and auto mode | TRANS-1.1, 1.2; DATA-5.4; EVAL-1.4; UI-1.1 |
