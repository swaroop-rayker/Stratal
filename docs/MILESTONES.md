# Stratal — Milestones

| | |
|---|---|
| Status | M0 merged 2026-11-14. M1 merged 2026-11-14. M2 merged 2026-11-15. M3 merged 2026-11-15 (DATA-2.1 and DATA-2.2 written by the agent at the owner's request; OC-5 threshold 8). M4 merged 2026-11-15 (all four owner units written by the agent at the owner's request; Florence-2 `<CAPTION>`; CLIP threshold 0.2). M5 merged 2026-11-16 (DATA-4 drafted, and its units and the EVAL-1.1 statistics written, by the agent at the owner's request; DATA-4.8 moved to M7). M6 merged 2026-11-17 (SCR-01 to SCR-07 slice, each through G3; two contrast rules added to the design system). M7 merged 2026-11-17: phase 1 accepted on a measured 20,000-image run, tagged `phase-1`; DATA-2.3 and DATA-4.8 moved to M11. G0 and G1 approved 2026-11-15; G2 passed for every phase-1 screen (SCR-01 to SCR-06, SCR-07 slice) 2026-11-15. Phase-2 plan (M8–M11) approved 2026-11-17 |
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
| M8 | Training runtime and registry | 2 | L | Not started (detailed, approved 2026-11-17) |
| M9 | Model builder | 2 | L | Not started (detailed, approved 2026-11-17) |
| M10 | Classical ML, HPO, task metrics | 2 | L | Not started (detailed, approved 2026-11-17) |
| M11 | Dashboards and monitoring completion | 2 | L | Not started (detailed, approved 2026-11-17) |
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
- [ ] G2: SCR-04 v2 (low-fi v1 approved 2026-11-17; hi-fi pending)

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
- [ ] S-08 report delivered; ADR-0007's limits revised from it if needed, still summing to 9 GB or less
- [ ] `test_RUN_1_1_*`: a training job moves through every state; invalid transitions are rejected and every transition is logged
- [ ] `test_RUN_1_2_*`: start, pause, resume and cancel each work on a running job
- [ ] `test_RUN_1_3_*`: kill mid-training, resume from the checkpoint; the metrics match an uninterrupted run within NFR-4's tolerance
- [ ] 👤 `test_RUN_1_4_*` (gpu): after a training model is unloaded, `torch.cuda.memory_allocated` is back at baseline
- [ ] 👤 `test_RUN_1_5_*` (gpu): a deliberately oversized batch fails cleanly with a smaller batch suggested, and nothing crashes
- [ ] `test_RUN_1_6_*`: a simulated high temperature pauses the job; it resumes when the temperature falls
- [ ] `test_RUN_1_7_*`: a simulated hang becomes `stalled` after T seconds
- [ ] `test_RUN_1_8_*`: SCR-04 updates live during a training run
- [ ] `test_RUN_1_9_*`: a tiny model defaults to the faster device measured by the benchmark; a forced device is used as given
- [ ] `test_REG_1_1_*`: any trained version traces to its exact dataset version and config
- [ ] `test_REG_1_2_*`: the three states are shown and filterable
- [ ] `test_REG_1_4_*`: `/predict` returns a result for a sample input, also while a training job holds the GPU
- [ ] `test_REG_2_1_*`: base weights are still present after 10 training runs
- [ ] `test_REG_2_2_*`: default settings, 3 runs: only base and run-3 weights on disk
- [ ] `test_REG_2_3_*`: N = 2, 5 runs: base, runs 3 and 4, and run 5 on disk
- [ ] `test_REG_2_4_*`: global N = 0 with lineage X at N = 3: only X keeps intermediates
- [ ] `test_REG_2_5_*`: a pinned run 2 survives N = 0 cleanup
- [ ] `test_REG_2_6_*`: a completed run leaves no checkpoint; a paused run is still resumable after cleanup
- [ ] `test_REG_2_7_*`: a version whose weights were deleted still shows its metrics and offers re-training
- [ ] `test_REG_2_8_*`: pruning an in-use or parent version is blocked and logged
- [ ] `test_REG_2_9_*`: the dry run lists what would go; the real run frees the bytes it reported
- [ ] `test_REG_2_10_*`: the confirmation lists the exact versions and their sizes
- [ ] `test_REG_2_11_*`: disk use per lineage matches the bytes on disk
- [ ] `test_NFR_14_*` and `test_BUG7_*`: after 10 runs with default retention, the disk audit matches base + latest + one in-progress checkpoint
- [ ] `test_NFR_15_*`: a kill during cleanup, then a rerun: no orphaned files and no dangling registry rows
- [ ] `test_NFR_1_*` (gpu) and `test_NFR_8_*` extended to training: peak VRAM under 5.5 GB; weights only as safetensors, never unpickled
- [ ] G3 for SCR-07 v2, SCR-04 v2, SCR-11 and SCR-14
- [ ] Demo: train the reference CNN on the phase-2 image dataset; pause and resume it from the UI; find it in the registry; run retention; get a prediction
- [ ] Docs updated (ARCHITECTURE, DATA_MODEL, API, ERRORS for BUG7; learning notes)
- [ ] Owner's OK to merge

## M9 — Model builder

**Size** L · **Branch** `m9-model-builder` · **Serves** BUILD-1.1–1.9, UI-2.3, DATA-5.2, DATA-5.5

**Before starting:** ADR-0017 approved; G2 for SCR-08 and SCR-09 approved; React Flow approved as a frontend dependency (SPEC §7.4 names it).

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

- [ ] ADR-0017 approved
- [ ] `test_BUILD_1_1_*`: every layer type can be added and configured
- [ ] `test_BUILD_1_2_*`: out-of-range values are rejected with a message
- [ ] `test_BUILD_1_3_*`: changing the dataset updates the input and output
- [ ] `test_BUILD_1_4_*`: a mismatched linear input is caught at build time
- [ ] `test_BUILD_1_5_*`: parameter count and memory estimate match `sum(p.numel())` and the formula
- [ ] `test_BUILD_1_6_*`: one test per cross-field rule (`d_model % num_heads == 0`, a conv stack never below 1×1, …)
- [ ] `test_BUILD_1_7_*`: a saved template reloads identically
- [ ] 👤 `test_BUILD_1_8_*`: attention, positional encoding and the LSTM cell match PyTorch's outputs within tolerance
- [ ] `test_BUILD_1_9_*` (P2, if time): a residual block builds and trains
- [ ] `test_UI_2_3_*`: a field added to a config schema appears in its form with no UI change
- [ ] `test_DATA_5_2_*`: each type validates against its schema
- [ ] `test_DATA_5_5_*`: the UI offers only compatible type and model combinations
- [ ] G3 for SCR-08 and SCR-09
- [ ] Demo: build a CNN on the canvas, train it from SCR-09, follow it on SCR-04, find it in SCR-11
- [ ] Docs updated (learning notes)
- [ ] Owner's OK to merge

## M10 — Classical ML, HPO, task metrics

**Size** L · **Branch** `m10-ml-hpo-metrics` · **Serves** ML-1.1–1.5, HPO-1.1–1.5, EVAL-1.2, DATA-1.4, DATA-2.4

**Before starting:** ADR-0018 approved; G2 for SCR-10 and SCR-09's HPO part approved; the tabular and labelled-text datasets settled (decision 1).

**Scope**

- Tabular (CSV, Parquet) and text (TXT, JSONL) ingestion into manifests (DATA-1.4). Text cleaning: whitespace and Unicode normalised, empty and over-length rows dropped and logged (DATA-2.4).
- Classical models (ML-1.1) and k-means, DBSCAN/HDBSCAN and PCA (ML-1.2) as scikit-learn pipelines. Preprocessing is fitted on the train split only (ML-1.3); invalid parameter combinations are refused (ML-1.4); models are saved with skops (ML-1.5, NFR-8). They run on the CPU queue and register like any model.
- HPO (HPO-1.1 to HPO-1.5): tunable markers on any schema field; Optuna TPE with a trial count and a time budget; median pruning with the stopping step logged; tuning on validation, or cross-validation for classical ML; the test split used once, on the best config; trial table, best config and parameter importance.
- 👤 Task metrics by type (EVAL-1.2): classification, regression, clustering, LM perplexity, translation BLEU and chrF, captioning CLIP score and CIDEr-D (CL-13). Shown on SCR-11's version page.
- Spike S-09: Optuna on PostgreSQL, the overhead per trial, grouped against one job per trial.
- Screens: SCR-09's HPO part and SCR-10.

**Done when**

- [ ] ADR-0018 approved; S-09 report delivered
- [ ] `test_DATA_1_4_*`: a sample CSV and a sample JSONL ingest without errors
- [ ] `test_DATA_2_4_*`: planted bad rows are removed and logged
- [ ] `test_ML_1_1_*`: each listed model trains on a tabular dataset
- [ ] `test_ML_1_2_*`: k-means, DBSCAN/HDBSCAN and PCA each run and report their metrics
- [ ] `test_ML_1_3_*`: the scaler's statistics come from the train split
- [ ] `test_ML_1_4_*`: invalid parameter combinations are rejected
- [ ] `test_ML_1_5_*`: a skops save and load round-trips; nothing is unpickled
- [ ] `test_HPO_1_1_*`: the search space builds from the UI's selections
- [ ] `test_HPO_1_2_*`: a study stops at whichever limit comes first
- [ ] `test_HPO_1_3_*`: pruned trials are logged with the step they stopped at
- [ ] `test_HPO_1_4_*`: test metrics are computed exactly once per study
- [ ] `test_HPO_1_5_*`: the trial table, best config and parameter importance render
- [ ] 👤 `test_EVAL_1_2_*`: the right metric set appears for each task
- [ ] G3 for SCR-09 (HPO part) and SCR-10
- [ ] Demo: tune a random forest on the tabular dataset and the CNN's learning rate; read both studies; one test evaluation each
- [ ] Docs updated (learning notes)
- [ ] Owner's OK to merge

## M11 — Dashboards, monitoring completion, phase-2 acceptance

**Size** L · **Branch** `m11-phase2-acceptance` · **Serves** MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4, MON-4.5, UI-2.1, UI-2.2, EVAL-1.3, DATA-3.5; DATA-2.3 and DATA-4.8 (phase 1, P2, moved from M7); the phase-2 done-definition

**Before starting:** G2 for SCR-13, SCR-03 v2 and SCR-11 v2 approved. The alerts and diagnostics add work to `monitor`, so NFR-11 is measured again here.

**Scope**

- Alerts (MON-2.6) at the decision-4 thresholds, each shown with the action taken (pause, fail, smaller batch) in the event log (MON-2.7).
- Bottleneck diagnostics (MON-3.2): the expected and observed resource per stage (the MON-3 table), for example GPU under 30 % with CPU at 100 % gives "data loading is the bottleneck: increase DataLoader workers".
- Load and unload events with VRAM before and after (MON-3.3), from labelling, training and the inference service.
- Two runs' resource curves overlaid (MON-4.4) and trends across past jobs (MON-4.5, P2, if time) on SCR-13.
- Raw against cleaned distributions and rejection reasons per stage (UI-2.2), and the diff of two versions (DATA-3.5, P2, if time), on SCR-03.
- Semantic duplicates (DATA-2.3) and each cluster kept in one split group (DATA-4.8), as [ADR-0013](decisions/ADR-0013-semantic-duplicates.md) sets out. Written by the agent at the owner's request (2026-11-17); the similarity threshold is proposed from a measurement on coco10k and chosen by the owner (as OC-5). Clusters shown on SCR-03.
- Three or more models on one test split, side by side (EVAL-1.3), on SCR-11.
- UI-2.1 completed: models and training runs join datasets and pipeline jobs in the dashboard.
- The phase-2 acceptance run, measured, with a README section; phase 3 (M12, M13) detailed for approval.

**Done when**

- [ ] `test_MON_2_6_*`: each alert fires in a simulated test
- [ ] `test_MON_2_7_*`: an alert and its action are shown together in the event log
- [ ] `test_MON_3_2_*`: the diagnostic fires in a simulated slow-loader test
- [ ] `test_MON_3_3_*`: an unload event shows VRAM before and after
- [ ] `test_MON_4_4_*`: two runs' curves overlay
- [ ] `test_MON_4_5_*` (P2, if time): trends shown for the last N jobs
- [ ] `test_UI_2_1_*`: datasets, lineage and jobs with live metrics render real data, training included
- [ ] `test_UI_2_2_*`: charts for any dataset version
- [ ] `test_EVAL_1_3_*`: three or more models compared in one view
- [ ] `test_DATA_3_5_*` (P2, if time): the diff lists the changes correctly
- [ ] `test_DATA_2_3_*` (P2): cropped variants are grouped together and a report is generated
- [ ] 👤 `test_DATA_4_8_*` (P2): two photos of one scene from different angles land in the same split (written by the agent at the owner's request)
- [ ] G3 for SCR-13, SCR-03 v2 and SCR-11 v2
- [ ] Phase-2 acceptance (`tests/acceptance`): a CNN built on SCR-08, trained on a dataset version, test metrics on SCR-11; NFR-1, NFR-2, NFR-4 and NFR-5 measured for training
- [ ] `test_NFR_11_*` rerun with alerts and diagnostics on
- [ ] Meta-test green: every phase-2 P0 (32) has a passing ID-named test
- [ ] M12–M13 detailed and approved by the owner
- [ ] Owner's OK to merge; tag `phase-2`

---

## Later phases (outline)

Every numbered requirement is assigned to exactly one milestone. Scope and checklists are written at each phase kickoff. M8–M11 were detailed and approved on 2026-11-17 (above), with the requirements their outline rows gave them; those rows are replaced by the detail.

| | Scope | Requirements |
|---|---|---|
| M12 | Local LLM serving, RAG with citations, hit@k evaluation, abstention, image search | LLM-1.1, 2.1–2.4; DATA-5.3; NFR-6 |
| M13 | Router and fallback, tool-calling assistant, caption refinement and comparison, QLoRA | LLM-3.1, 3.2, 4.1, 5.1; LABEL-1.6, 1.7 |
| M14 | Image generation, LoRA fine-tuning, before/after evaluation, remote offload round trip, adapters as deltas | IMG-1.1–1.4; REG-1.3 |
| M15 | Translation serving and fine-tuning, parallel-text type, scratch vs pretrained, chat UI in manual and auto mode | TRANS-1.1, 1.2; DATA-5.4; EVAL-1.4; UI-1.1 |
