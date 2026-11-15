# Stratal — Requirements Specification

| | |
|---|---|
| Version | 0.2 (kickoff baseline) |
| Date | 2026-11-13 |
| Status | Approved by the owner at kickoff |
| Source | [SPEC-INPUT-v0.1.txt](SPEC-INPUT-v0.1.txt) — the owner's input, copied byte-for-byte |

This document is the source of truth for what Stratal must do. It restates everything in the v0.1 input and adds the clarifications approved at kickoff. Nothing from the input has been dropped. Every place where the wording differs from the input is flagged and listed in [Appendix A](#appendix-a--change-log-v01--v02).

## How to read this document

- **Requirement IDs (F-IDs).** A functional requirement is identified by its module ID, for example `DATA-2.2`. These are the IDs used in commits, tests and pull requests.
- **Acceptance criteria (AC-IDs).** Each criterion is the requirement ID plus `.AC<n>`, for example `DATA-2.2.AC1`.
- **Tags.** `[P0][1]` means priority P0, delivered in phase 1. P0 is must-have, P1 should-have, P2 nice-to-have.
- **Flags.** `⚑ CL-nn` marks text reworded from the input. `✚ CL-nn` marks text added. `⊕ CL-nn` marks duplicated input blocks merged into one. The number points at the change-log entry that shows the original wording and the reason.
- **Tests.** Every P0 requirement needs at least one automated test named with its ID (NFR-9), for example `test_DATA_2_2_near_duplicates`.
- **Owner-implemented.** Requirements marked 👤 contain core logic the owner writes (see [§9.1](#91-general-rules)). The agent supplies typed stubs and failing ID-named tests for them.

## 1. Overview

**Problem.** Building ML models involves many separate tools: data cleaning, labeling, training, tuning, evaluation and serving. Stratal is a local, end-to-end ML lifecycle platform that runs on consumer hardware. It turns raw data into versioned datasets, trains and fine-tunes models, evaluates them, and serves them through a chat UI and an engineer dashboard.

**Users.**

- End user: chats with models, generates images, translates text
- AI engineer: manages data, builds and trains models, tunes, evaluates, monitors jobs

## 2. Phases

| Phase | Scope | Done when |
|---|---|---|
| 1 | Data ops + image→text auto-labeling + eval | 10k raw images → captioned, filtered, versioned, split dataset with an eval report |
| 2 | Training runtime + model builder + classical ML + HPO | User builds a CNN in the UI, trains it on a dataset version, sees test metrics |
| 3 | LLM: caption refinement, RAG, router, tools | Chat answers grounded in the corpus, with citations; router picks the correct model |
| 4 | Text→image LoRA fine-tune | LoRA trained on the phase-1 dataset; before/after samples + CLIP score |
| 5 | Translation + full chat UI + dashboard polish | All 4 model types reachable from chat (manual + auto mode) |

**Phase-1 slice of phase-2 items** `⚑ CL-04`. Several phase-1 P0 requirements (events for every job, resumable captioning, the live job view) depend on items tagged phase 2. Phase 1 therefore delivers a thin slice of them. The tags below are unchanged, and the full requirements and their tests are still owed in phase 2.

| Tagged phase 2 | Slice delivered in phase 1 |
|---|---|
| RUN-1.1 | Job states with logged, validated transitions, for pipeline jobs |
| RUN-1.2 | Start and cancel. Pause and resume arrive in phase 2 |
| RUN-1.3 | Resumable pipeline stages (finished stages and finished caption batches are never redone). Training checkpoints arrive in phase 2 |
| RUN-1.4, RUN-1.5 | Model load/unload with a baseline check, and OOM handling, for the labeling path only |
| UI-2.1 | App shell, dataset and version list, lineage view, live job view (SCR-01 to SCR-04) |

## 3. Functional requirements

### Module DATA — Data Operations

#### DATA-1 Ingestion

- **DATA-1.1** `[P0][1]` The system shall ingest all files from a source folder into stage 0 (raw), recording path, size, format and dimensions.
  - `DATA-1.1.AC1` 10k-file folder → stage0 manifest has 10k rows.
- **DATA-1.2** `[P0][1]` The system shall reject corrupt or unreadable files and record the reason.
  - `DATA-1.2.AC1` 5 planted corrupt files → 5 rows with status=rejected and a reason.
- **DATA-1.3** `[P1][1]` The system shall reject images below a configurable minimum size (default 256px).
  - `DATA-1.3.AC1` threshold can be changed via config; undersized images flagged.
- **DATA-1.4** `[P1][2]` The system shall ingest tabular (CSV/Parquet) and text (TXT/JSONL) sources.
  - `DATA-1.4.AC1` sample CSV and JSONL ingest without errors.

#### DATA-2 Cleaning & Deduplication

- **DATA-2.1** `[P0][1]` 👤 The system shall remove byte-identical duplicates.
  - `DATA-2.1.AC1` 20 planted exact copies removed.
- **DATA-2.2** `[P0][1]` 👤 The system shall remove near-duplicates using perceptual hashing with a configurable distance threshold.
  - `DATA-2.2.AC1` the same image saved at JPEG 90 and 70 → one kept.
- **DATA-2.3** `[P2][1]` 👤 The system shall detect semantic duplicates by clustering CLIP embeddings.
  - `DATA-2.3.AC1` cropped variants grouped together; report generated.
- **DATA-2.4** `[P1][2]` The system shall clean text: normalize whitespace and Unicode, and drop empty or over-length rows.
  - `DATA-2.4.AC1` planted bad rows removed and logged.

> **Clarification** `✚ CL-17`. "Remove" means excluded from the accepted set. The row stays in the manifest, marked as a duplicate with its duplicate-group ID and the kept item. This matches LABEL-1.4 ("marked with a reason, not deleted") and lets DATA-4 keep a duplicate group on one side of a split.

#### DATA-3 Versioning & Lineage

- **DATA-3.1** `[P0][1]` Each stage shall write a manifest (Parquet) containing per-row status and a content hash.
  - `DATA-3.1.AC1` every stage produces a manifest file.
- **DATA-3.2** `[P0][1]` 👤 Each dataset version shall get a version ID derived from its content hash and config hash.
  - `DATA-3.2.AC1` same input + config → same ID; any change → new ID.
- **DATA-3.3** `[P0][1]` The system shall record lineage (parent version, stage, config, timestamp) for each version.
  - `DATA-3.3.AC1` lineage can be followed from any version back to raw.
- **DATA-3.4** `[P1][1]` The system shall store versions as manifests pointing to files, not as full copies of the data.
  - `DATA-3.4.AC1` a new version adds only manifest bytes plus any new files.
- **DATA-3.5** `[P2][2]` The system shall show a diff between two versions (rows added/removed/changed).
  - `DATA-3.5.AC1` diff report lists the changes correctly.

#### DATA-4 Train/Val/Test Splits

> **Not yet written — owner's exercise.** 👤 The input says: "Your exercise: write this section yourself. Include stratification, reproducibility, and leakage prevention."
>
> The owner writes this section and sends it for review. It is needed by milestone M5. The review will check that it answers:
>
> 1. What is stratified on when image–caption rows have no class label?
> 2. How do near or semantic duplicates of each other stay on one side of the split?
> 3. Does adding 500 images in a later version reshuffle existing assignments?
> 4. Where does the split sit relative to the CLIP filter and manual review?
>
> **2026-11-15:** at the owner's request the agent wrote draft v1, now in the owner's review: [drafts/DATA-4_v1.md](drafts/DATA-4_v1.md). This placeholder stays until a reviewed version is approved.

#### DATA-5 Dataset Types

- **DATA-5.1** `[P0][1]` The system shall support the image–caption pairs dataset type, with schema validation.
  - `DATA-5.1.AC1` rows missing a caption or with a missing file are rejected with a report.
- **DATA-5.2** `[P0][2]` The system shall support labeled images, tabular, and labeled text types.
  - `DATA-5.2.AC1` each validates against its schema.
- **DATA-5.3** `[P1][3]` The system shall support text corpus and instruction pairs types.
  - `DATA-5.3.AC1` validation + adapter work on a sample.
- **DATA-5.4** `[P1][5]` The system shall support parallel text (src, tgt, languages).
  - `DATA-5.4.AC1` empty or mismatched pairs rejected.
- **DATA-5.5** `[P0][2]` Each dataset type shall declare its compatible model families and default metrics.
  - `DATA-5.5.AC1` UI only offers compatible combinations.

#### DATA-6 Preprocessing `✚ CL-05`

- **DATA-6.1** `[P1][1]` The system shall write a preprocessed copy of every accepted image, resized so its longer side does not exceed a configurable maximum, keep the original, and record the transform in the version's config.
  - `DATA-6.1.AC1` every accepted row points at a preprocessed file that exists and respects the maximum; originals are still present; re-running with the same config adds no new files; changing the maximum produces a new version ID.

### Module LABEL — Auto-labeling

- **LABEL-1.1** `[P0][1]` 👤 The system shall caption images in batches using a configurable image→text model (default Florence-2-base).
  - `LABEL-1.1.AC1` every row gets a caption + model_version.
- **LABEL-1.2** `[P0][1]` 👤 Captioning shall checkpoint progress and resume after a crash without redoing finished images.
  - `LABEL-1.2.AC1` kill at 60% → restart → completes, no duplicate work.
- **LABEL-1.3** `[P0][1]` 👤 The system shall compute a CLIP image–text similarity score for each pair.
  - `LABEL-1.3.AC1` score column present for every row.
- **LABEL-1.4** `[P0][1]` 👤 The system shall filter pairs below a configurable CLIP threshold.
  - `LABEL-1.4.AC1` filtered rows marked with a reason, not deleted.
- **LABEL-1.5** `[P1][1]` The system shall support manual review: sample N rows and record accept/reject.
  - `LABEL-1.5.AC1` review results stored and linked to the version.
- **LABEL-1.6** `[P1][3]` The system shall refine captions with an LLM into a structured schema (subject, attributes, setting, style).
  - `LABEL-1.6.AC1` output validates against the Pydantic schema; raw caption kept alongside.
- **LABEL-1.7** `[P2][3]` The system shall compare CLIP scores for raw vs refined captions.
  - `LABEL-1.7.AC1` comparison report generated.

### Module RUN — Training Runtime

- **RUN-1.1** `[P0][2]` `⚑ CL-06` The system shall run training jobs from a queue with states (queued, running, paused, completed, failed, cancelled, stalled).
  - `RUN-1.1.AC1` state transitions logged; invalid transitions rejected.
- **RUN-1.2** `[P0][2]` The user shall be able to start, pause, resume and cancel jobs.
  - `RUN-1.2.AC1` each action works on a running job.
- **RUN-1.3** `[P0][2]` Jobs shall checkpoint every N steps/epochs and resume from the latest checkpoint.
  - `RUN-1.3.AC1` kill mid-training → resume continues from the checkpoint with matching metrics.
- **RUN-1.4** `[P0][2]` 👤 The VRAM manager shall keep at most one large model on the GPU and fully free memory on unload.
  - `RUN-1.4.AC1` torch.cuda.memory_allocated returns to baseline after unload.
- **RUN-1.5** `[P0][2]` 👤 The system shall detect OOM, catch it, mark the job failed with a clear reason, and suggest a smaller batch size.
  - `RUN-1.5.AC1` a deliberately oversized batch → clean failure, no crash.
- **RUN-1.6** `[P1][2]` The system shall monitor GPU temperature and pause jobs above a configurable limit, resuming when it cools.
  - `RUN-1.6.AC1` simulated high temperature → job pauses then resumes.
- **RUN-1.7** `[P1][2]` The system shall detect hung jobs (no progress for T seconds) and mark them stalled.
  - `RUN-1.7.AC1` simulated hang → stalled status.
- **RUN-1.8** `[P0][2]` Jobs shall stream live metrics: loss, accuracy, step time, VRAM, RAM.
  - `RUN-1.8.AC1` dashboard updates live during a run.
- **RUN-1.9** `[P1][2]` The runtime shall choose CPU or GPU per job (auto or manual).
  - `RUN-1.9.AC1` tiny models default to the faster device, based on a benchmark.

### Module BUILD — Model Builder

- **BUILD-1.1** `[P0][2]` The user shall build models by stacking layers in the UI. Supported: Linear, Conv2d, MaxPool, BatchNorm, Dropout, Flatten, Embedding, RNN, LSTM, GRU, custom attention block, activations.
  - `BUILD-1.1.AC1` each layer type can be added and configured.
- **BUILD-1.2** `[P0][2]` Every layer and training parameter shall be configurable, with validated ranges and defaults.
  - `BUILD-1.2.AC1` out-of-range values rejected with a message.
- **BUILD-1.3** `[P0][2]` Input shape and output size shall be derived from the dataset type and shown as locked.
  - `BUILD-1.3.AC1` changing the dataset updates input/output automatically.
- **BUILD-1.4** `[P0][2]` The system shall infer each layer’s output shape and block invalid stacks before training.
  - `BUILD-1.4.AC1` a mismatched linear input is caught at build time.
- **BUILD-1.5** `[P0][2]` The UI shall show live parameter count and estimated memory (weights + gradients + optimizer state).
  - `BUILD-1.5.AC1` values match sum(p.numel()) and the formula.
- **BUILD-1.6** `[P0][2]` The system shall validate across fields (e.g., d_model % num_heads == 0; the conv stack can’t shrink the image below 1×1).
  - `BUILD-1.6.AC1` each rule has a test.
- **BUILD-1.7** `[P1][2]` Users shall be able to save and load model specs as templates.
  - `BUILD-1.7.AC1` saved spec reloads identically.
- **BUILD-1.8** `[P1][2]` 👤 The attention block, positional encoding and one LSTM cell shall be implemented from scratch.
  - `BUILD-1.8.AC1` unit tests match PyTorch reference outputs within tolerance.
- **BUILD-1.9** `[P2][2]` Graph (non-sequential) architectures with skip connections.
  - `BUILD-1.9.AC1` a residual block builds and trains.

### Module ML — Classical ML

- **ML-1.1** `[P0][2]` The system shall support Linear/Ridge/Lasso, Logistic Regression, SVM, Decision Tree, Random Forest, Gradient Boosting, KNN, Naive Bayes.
  - `ML-1.1.AC1` each trains on a tabular dataset.
- **ML-1.2** `[P1][2]` The system shall support k-means, DBSCAN/HDBSCAN and PCA.
  - `ML-1.2.AC1` each runs and reports its metrics.
- **ML-1.3** `[P0][2]` Preprocessing (scaling, encoding, imputation) shall be part of the model pipeline and fitted on train only.
  - `ML-1.3.AC1` test shows the scaler’s statistics come from the train split.
- **ML-1.4** `[P0][2]` The system shall validate parameter combinations (e.g., LogReg l1 needs a compatible solver).
  - `ML-1.4.AC1` invalid combinations rejected.
- **ML-1.5** `[P1][2]` Models shall be saved in a safe format (skops), never unpickled from unknown sources.
  - `ML-1.5.AC1` save/load round-trip works with skops.

### Module HPO — Hyperparameter Search

- **HPO-1.1** `[P0][2]` The user shall mark parameters as tunable, with ranges or choices (int, float, log-float, categorical).
  - `HPO-1.1.AC1` search space builds from the UI selections.
- **HPO-1.2** `[P0][2]` The system shall run Optuna studies (TPE) with a trial count and time budget.
  - `HPO-1.2.AC1` study stops at whichever limit is hit first.
- **HPO-1.3** `[P1][2]` Bad trials shall be pruned early.
  - `HPO-1.3.AC1` pruned trials logged with the step at which they were stopped.
- **HPO-1.4** `[P0][2]` Tuning shall optimize on validation (or CV for classical ML); test is used once, on the best config.
  - `HPO-1.4.AC1` test metrics computed exactly once per study.
- **HPO-1.5** `[P1][2]` Results shall show a trial table, best config and parameter importance.
  - `HPO-1.5.AC1` all three render.

### Module REG — Model Registry & Inference

#### REG-1 Registry and inference `⊕ CL-14`

- **REG-1.1** `[P0][2]` Each trained model shall be versioned with its full resolved config, dataset version, metrics and checkpoint.
  - `REG-1.1.AC1` any model can be traced to its exact data + config.
- **REG-1.2** `[P0][2]` Models shall have states: base, partial (paused), fully trained.
  - `REG-1.2.AC1` states displayed and filterable.
- **REG-1.3** `[P1][4]` Fine-tunes shall store only adapters/deltas (e.g., LoRA weights), not full weight copies.
  - `REG-1.3.AC1` a fine-tune version is MBs, not GBs.
- **REG-1.4** `[P0][2]` The system shall expose inference via FastAPI endpoints for each registered model.
  - `REG-1.4.AC1` /predict returns results for a sample input.

#### REG-2 Weight Retention Policy

Default rule: per model lineage, store ONLY

1. the ORIGINAL weights (base / pretrained model, or the initial weights of a from-scratch model), and
2. the MOST RECENT trained / fine-tuned weights.

Everything in between is deleted unless the user opts to keep it.

- **REG-2.1** `[P0][2]` The system shall always retain the original (base) weights of every model lineage. They can never be deleted by retention.
  - `REG-2.1.AC1` base weights still present after 10 training runs.
- **REG-2.2** `[P0][2]` After a training / fine-tuning run completes, the system shall retain its weights as the new "latest" and delete the previous latest, unless it is kept by REG-2.3 or pinned (REG-2.5).
  - `REG-2.2.AC1` default config, 3 runs -> only base + run-3 weights on disk.
- **REG-2.3** `[P0][2]` The user shall configure how many intermediate versions to keep: keep_intermediate = N (default 0 = keep none; "all" = keep every version). Retained = the N most recent versions before "latest".
  - `REG-2.3.AC1` N=2, 5 runs -> base + runs 3, 4 (intermediate) + run 5 (latest) on disk.
- **REG-2.4** `[P1][2]` keep_intermediate shall be settable globally and overridable per model lineage.
  - `REG-2.4.AC1` global N=0, lineage X N=3 -> only X keeps intermediates.
- **REG-2.5** `[P1][2]` The user shall be able to pin any version; pinned versions are exempt from retention until unpinned.
  - `REG-2.5.AC1` pinned run-2 survives N=0 cleanup.
- **REG-2.6** `[P0][2]` In-run checkpoints (RUN-1.3) are temporary: during a run keep only the latest checkpoint (for resume); delete it when the run completes successfully. A paused/partial run keeps its checkpoint until it is resumed or cancelled.
  - `REG-2.6.AC1` completed run leaves no checkpoint files; paused run still resumable after cleanup.
- **REG-2.7** `[P0][2]` When weights are deleted, metadata is kept: config, dataset version, metrics, lineage, and a "weights_deleted" status, so the version can be inspected or re-trained (reproducible via NFR-4).
  - `REG-2.7.AC1` deleted version still shows in registry with metrics and a "re-train" action.
- **REG-2.8** `[P0][2]` Retention shall never delete weights that are: (a) in use by a running/queued job or loaded for inference, (b) the parent of an existing adapter/fine-tune (e.g. the base model a LoRA depends on).
  - `REG-2.8.AC1` attempt to prune an in-use or parent version is blocked and logged.
- **REG-2.9** `[P1][2]` Retention runs automatically after each completed run and on demand; it logs every deletion (version, size freed) as a MON event, with a dry-run preview option.
  - `REG-2.9.AC1` dry-run lists what would be deleted; real run frees the reported bytes.
- **REG-2.10** `[P1][2]` Changing keep_intermediate to a smaller N shall show a confirmation with the versions and disk space affected before deleting.
  - `REG-2.10.AC1` confirmation lists exact versions + MB/GB.
- **REG-2.11** `[P1][2]` The registry UI shall show disk usage per lineage (base / latest / intermediate / pinned / checkpoints).
  - `REG-2.11.AC1` totals match actual bytes on disk.

Config example (`configs/retention.yaml`):

```yaml
retention:
  keep_intermediate: 0        # 0 = only base + latest; "all" = keep every version
  overrides:
    sd15_lora_captions: 3     # keep 3 intermediates for this lineage
  delete_checkpoints_on_success: true
  dry_run: false
```

### Module EVAL — Evaluation

- **EVAL-1.1** `[P0][1]` 👤 Phase-1 report: rejection counts per stage, CLIP score distribution, filter rate, manual-review acceptance with a 95% Wilson CI.
  - `EVAL-1.1.AC1` report generated per dataset version.
- **EVAL-1.2** `[P0][2]` 👤 Task metrics by type: classification (accuracy, F1, confusion matrix), regression (MSE, MAE, R²), clustering (silhouette, Davies–Bouldin), LM (perplexity), translation (BLEU, chrF), captioning (CLIP score, CIDEr).
  - `EVAL-1.2.AC1` correct metric set appears for each task.
- **EVAL-1.3** `[P1][2]` Comparison panel: multiple models on the same test split, side by side.
  - `EVAL-1.3.AC1` ≥3 models compared in one view.
- **EVAL-1.4** `[P1][5]` Scratch vs pretrained comparison on the same split.
  - `EVAL-1.4.AC1` tiny seq2seq vs MarianMT, both BLEU scores shown.

### Module LLM — Language Model, RAG, Router

- **LLM-1.1** `[P0][3]` The system shall serve a 1–3B LLM locally (4-bit, via Ollama/llama.cpp).
  - `LLM-1.1.AC1` chat responses within latency NFR.
- **LLM-2.1** `[P0][3]` RAG: ingest a text corpus, chunk it, embed it, index it (FAISS), retrieve top-k, generate an answer with citations.
  - `LLM-2.1.AC1` answers cite the correct source chunks.
- **LLM-2.2** `[P0][3]` RAG retrieval shall be evaluated with hit@k on a hand-labeled question set.
  - `LLM-2.2.AC1` hit@5 reported per index version.
- **LLM-2.3** `[P1][3]` The system shall answer “I don’t know” when retrieval relevance is below a threshold.
  - `LLM-2.3.AC1` out-of-corpus questions get an abstention.
- **LLM-2.4** `[P1][3]` Image search: text → CLIP → nearest dataset images.
  - `LLM-2.4.AC1` “dogs at night” returns relevant images.
- **LLM-3.1** `[P0][3]` Router (LangGraph): classify each request and dispatch it to LLM / RAG / image-gen / captioner / translator.
  - `LLM-3.1.AC1` ≥90% correct routing on a labeled test set of 50 requests.
- **LLM-3.2** `[P1][3]` Router fallback: if a model fails, return a clear error or use an alternative.
  - `LLM-3.2.AC1` simulated model failure is handled.
- **LLM-4.1** `[P2][3]` Suite assistant with tool calling (query runs, configs, metrics).
  - `LLM-4.1.AC1` “best F1 on dataset v3?” answered correctly from metadata.
- **LLM-5.1** `[P2][3]` QLoRA fine-tuning of the LLM on instruction pairs.
  - `LLM-5.1.AC1` held-out loss improves over base.

### Module IMG — Text→Image

- **IMG-1.1** `[P0][4]` Generate images from text with an SD 1.5-class model.
  - `IMG-1.1.AC1` 512px image generated within the VRAM limit.
- **IMG-1.2** `[P0][4]` LoRA fine-tuning on a phase-1 dataset version (fp16, gradient checkpointing, 8-bit Adam).
  - `IMG-1.2.AC1` training completes locally or on a remote GPU.
- **IMG-1.3** `[P1][4]` Before/after evaluation: CLIP score of generated images vs prompts, base vs LoRA.
  - `IMG-1.3.AC1` comparison report generated.
- **IMG-1.4** `[P1][4]` Remote offload: export a job + dataset bundle for Kaggle/Colab, and import the resulting LoRA back.
  - `IMG-1.4.AC1` round trip works end to end.

### Module TRANS — Translation

- **TRANS-1.1** `[P0][5]` Serve a pretrained translation model (MarianMT / NLLB-600M distilled).
  - `TRANS-1.1.AC1` translates the supported language pairs.
- **TRANS-1.2** `[P1][5]` Fine-tune MarianMT on a parallel-text dataset version.
  - `TRANS-1.2.AC1` BLEU on test improves over base.

### Module UI

- **UI-1.1** `[P0][5]` Chat UI with manual model selection and auto (router) mode.
  - `UI-1.1.AC1` both modes work for all model types.
- **UI-2.1** `[P0][2]` Engineer dashboard: datasets and versions, lineage graph, jobs with live metrics.
  - `UI-2.1.AC1` all three views render real data.
- **UI-2.2** `[P1][2]` Raw vs cleaned data analysis: distributions and rejection reasons per stage.
  - `UI-2.2.AC1` charts for any dataset version.
- **UI-2.3** `[P0][2]` Model builder and HPO forms generated from Pydantic JSON schemas.
  - `UI-2.3.AC1` adding a new parameter needs no UI code change.

### Module MON — Monitoring & Observability

**Goal:** At any moment, the AI engineer can see which stage is running, what the software is doing right now, how far along it is, how healthy it is, and what it’s costing in CPU, RAM, GPU and VRAM. Every number is tied to a job ID and stage, so spikes can be traced to their cause.

#### MON-1 Event Model (what the software is doing)

- **MON-1.1** `[P0][1]` Every job shall emit structured stage events: stage_started, stage_progress, stage_completed, stage_failed, each with job ID, dataset/model version, stage, step and timestamp.
  - `MON-1.1.AC1` an event log can rebuild the full timeline of a job.
- **MON-1.2** `[P0][1]` Every job shall emit activity messages that say in plain language what it’s doing right now, including the key parameters.
  - `MON-1.2.AC1` messages like “Captioning batch 42/313 · Florence-2-base · bs=16 · fp16” appear live.
- **MON-1.3** `[P0][1]` Events shall have levels (DEBUG, INFO, WARN, ERROR); warnings and errors include the reason and a suggested action.
  - `MON-1.3.AC1` an OOM event shows “reduce batch size from 32 → 16”.
- **MON-1.4** `[P0][1]` `⚑ CL-02` Events shall be stored durably (PostgreSQL, with a Parquet/DuckDB archive) and streamed live to the UI (WebSocket or SSE).
  - `MON-1.4.AC1` the UI updates within 1 s; events survive an app restart.
- **MON-1.5** `[P1][1]` Each stage shall report ETA from a moving average of throughput.
  - `MON-1.5.AC1` ETA within ±20% once 10% is complete.

Event schema:

```json
{
  "ts": "2026-11-20T14:03:11.204Z",
  "job_id": "job_0142",
  "stage": "labeling",
  "substage": "caption_batch",
  "version_id": "ds_7f3a9c",
  "level": "INFO",
  "step": 42, "total": 313,
  "message": "Captioning batch 42/313 · Florence-2-base · bs=16 · fp16",
  "metrics": {"img_per_s": 2.4, "batch_ms": 6650}
}
```

#### MON-2 Resource Telemetry

- **MON-2.1** `[P0][1]` The system shall sample CPU (overall % and per core), RAM (used, available, process RSS), GPU (utilization %, memory controller %), VRAM (used, total, process allocated vs reserved), GPU temperature, GPU power draw, and disk I/O (read/write MB/s).
  - `MON-2.1.AC1` all metrics visible live during a job.
- **MON-2.2** `[P0][1]` Sampling: once per second during jobs, once every 10 s when idle; interval configurable.
  - `MON-2.2.AC1` sample timestamps match the configured interval.
- **MON-2.3** `[P0][1]` Every sample shall be tagged with the active job ID and stage.
  - `MON-2.3.AC1` resource charts can be filtered by stage.
- **MON-2.4** `[P0][1]` VRAM shall be reported both from NVML (driver view) and from PyTorch (memory_allocated vs memory_reserved).
  - `MON-2.4.AC1` both shown side by side; the gap explains the caching allocator.
- **MON-2.5** `[P1][1]` Peak values per stage (peak VRAM, peak RAM, max temperature) shall be saved in the job summary.
  - `MON-2.5.AC1` summary lists peaks for every stage.
- **MON-2.6** `[P1][2]` Thresholds with alerts: VRAM > 90%, RAM > 85%, GPU temp > configurable limit, no progress for T seconds.
  - `MON-2.6.AC1` each alert fires in a simulated test.
- **MON-2.7** `[P1][2]` Alerts shall link to the action taken (pause, fail, smaller batch) per RUN-1.5 to RUN-1.7.
  - `MON-2.7.AC1` alert + action shown together in the event log.

**Tooling:** psutil for CPU/RAM/disk; nvidia-ml-py (NVML) for GPU utilization, VRAM, temperature and power; torch.cuda for allocator stats. Monitor only the RTX 4050, not the integrated Radeon GPU.

**Interpretation note for the UI:** NVML “GPU utilization” means the % of time at least one kernel was running. It doesn’t measure how much of the GPU is actually used. A job can show 100% while using the GPU inefficiently [Established]. Add a tooltip saying so.

#### MON-3 Stage-Specific Monitoring

Each pipeline stage shows what it’s doing, its own metrics, and the resources it should be stressing. That last one tells the engineer where the bottleneck is.

| Stage | “What it’s doing” messages | Stage metrics | Main resource (expected bottleneck) |
|---|---|---|---|
| Ingestion | “Scanning folder… 8,412 files found” · “Reading metadata 4,200/8,412” | Files/s, accepted vs rejected, rejection reasons | Disk I/O, CPU |
| Cleaning | “Hashing images (pHash) 3,100/8,000” · “Comparing hashes: 212 near-duplicate groups found” | Duplicates removed (exact / near / semantic), rejected by reason | CPU (hashing), RAM (hash index) |
| Preprocessing | “Resizing to 384px · normalizing” · “Tokenizing corpus, vocab 12,480” | Items/s, output shape stats, vocab size, length distributions | CPU, RAM |
| Labeling | “Loading Florence-2-base to GPU (0.9 GB)” · “Captioning batch 42/313” · “CLIP scoring batch 10/313” · “Unloading captioner, freeing VRAM” | Img/s, batch latency, CLIP score running mean, filter rate | GPU, VRAM |
| Staging | “Writing manifest stage2.parquet” · “Computing version hash” · “Recording lineage → ds_7f3a9c” | Rows per stage, version ID, bytes written | Disk I/O |
| Training | “Epoch 7/20 · step 140/250” · “Saving checkpoint ckpt_e7” · “Early stopping: val loss stalled for 5 epochs” | Train/val loss, accuracy/F1, LR, gradient norm, step time, samples/s | GPU/VRAM (or CPU for tiny models) |
| Fine-tuning | “Loading base SD 1.5 + LoRA rank 8” · “Gradient checkpointing on” · “Step 400/2000 · saving adapter” | Loss, LR, VRAM headroom, step time, adapter size, sample generations every N steps | VRAM (tightest), GPU temp |
| Inference | “Request r_918 → router → captioner” · “Loading LLM (cold start 3.2 s)” · “Generating 128 tokens” | Latency (p50/p95), tokens/s or img/s, cold vs warm starts, queue depth | GPU/VRAM, RAM (LLM offload) |
| Evaluation | “Running test split (1,200 items)” · “Computing BLEU/chrF” · “Writing eval report” | Metric values, CIs, per-class breakdown, comparison vs baseline | GPU or CPU, depending on model |

- **MON-3.1** `[P0][1]` Each stage shall emit the activity messages and stage metrics listed for it above.
  - `MON-3.1.AC1` running each stage produces its messages and metrics.
- **MON-3.2** `[P1][2]` The dashboard shall flag mismatches between expected and observed bottleneck (e.g., labeling with GPU < 30% while CPU is at 100% → “data loading is the bottleneck: increase DataLoader workers”).
  - `MON-3.2.AC1` the diagnostic fires in a simulated slow-loader test.
- **MON-3.3** `[P1][2]` Model load/unload events (RUN-1.4) shall show VRAM before and after.
  - `MON-3.3.AC1` event shows e.g. “VRAM 4.1 → 0.3 GB after unload”.

#### MON-4 Dashboard Views

- **MON-4.1** `[P0][1]` Live job view: current stage, activity message, progress bar with ETA, and live charts for CPU, RAM, GPU, VRAM and temperature.
  - `MON-4.1.AC1` renders and updates during a job.
- **MON-4.2** `[P0][1]` Pipeline timeline: a horizontal bar per stage showing duration and status, color-coded. Clicking a stage shows its events and resource charts.
  - `MON-4.2.AC1` a full phase-1 run shows all stages.
- **MON-4.3** `[P1][1]` Activity log: a filterable stream of events (by level, stage, job).
  - `MON-4.3.AC1` filters work.
- **MON-4.4** `[P1][2]` Job comparison: two runs’ resource curves overlaid (e.g., bs=16 vs bs=32).
  - `MON-4.4.AC1` overlay renders.
- **MON-4.5** `[P2][2]` Historical view: throughput and peaks across all past jobs.
  - `MON-4.5.AC1` trends shown for the last N jobs.

Live job view layout:

```text
┌ job_0142 · LABELING · ds_7f3a9c ─────────────────────────── ETA 14m 32s ┐
│ ▶ Captioning batch 42/313 · Florence-2-base · bs=16 · fp16              │
│ [████████░░░░░░░░░░░░░░░░░░░░░░] 13%     2.4 img/s                       │
├─────────────── Timeline ────────────────────────────────────────────────┤
│ ingest ✔ 1m12s │ clean ✔ 3m40s │ preprocess ✔ 0m50s │ label ▶ │ stage ○   │
├──── CPU 38% ────┬──── RAM 7.2/16 GB ──┬── GPU 94% ───┬── VRAM 3.9/6 GB ─┤
│  ▁▂▃▂▃▂▃▂▂▃▂▃   │  ▅▅▅▅▅▆▆▆▆▆▆▆       │  ▇▇▇█▇▇█▇▇▇  │  ▆▆▆▆▆▆▆▆▆▆▆     │
│                 │                     │  71°C · 48 W │  alloc 3.1 · rsv 3.6 │
├──── Activity log ───────────────────────────────────────────────────────┤
│ 14:03:11 INFO  Captioning batch 42/313                                  │
│ 14:02:58 INFO  Loaded Florence-2-base to GPU (0.9 GB, 2.1 s)            │
│ 14:02:55 WARN  212 near-duplicate groups removed (2.6% of input)        │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Architecture note

```text
 job process ──events──►┐
                        ├──► collector ──► PostgreSQL (durable)      ⚑ CL-02
 sampler (psutil+NVML) ─┘         │
   tags samples with              └──► WebSocket/SSE ──► dashboard (live)
   active job_id + stage
```

The sampler runs as a separate process/thread from the jobs (this is what NFR-13 requires). It learns which job and stage is active from the event stream, so every resource sample can be tagged without the training loop knowing anything about monitoring.

> **Clarification** `⚑ CL-11`. NVML and psutil numbers come from the sampler. PyTorch's allocated and reserved figures (MON-2.4) can only be read inside the process that owns them, so the job runtime wrapper reports those. The training loop itself still knows nothing about monitoring.

## 4. Non-Functional Requirements

- **NFR-1** VRAM: peak usage < 5.5 GB for any local job.
  - `NFR-1.AC1` logged peak per job.
- **NFR-2** `⚑ CL-03` RAM: peak < 9 GB across all Stratal containers, with the WSL VM capped at 10 GB; datasets are streamed, never fully loaded.
  - `NFR-2.AC1` memory profile on a 20k-image run.
- **NFR-3** Throughput: captioning ≥ 2 img/s on the RTX 4050 (benchmark, then adjust the target).
  - `NFR-3.AC1` benchmark in README.
- **NFR-4** Reproducibility: same data + config + seed → same splits, same version IDs, and metrics within tolerance.
  - `NFR-4.AC1` two runs compared automatically.
- **NFR-5** Resilience: no job loses more than N minutes of work to a crash.
  - `NFR-5.AC1` kill tests in the test suite.
- **NFR-6** Latency: LLM first token < 2 s; RAG answer < 8 s on local hardware.
  - `NFR-6.AC1` measured on 20 queries.
- **NFR-7** Local-first: works fully offline after models are downloaded; remote is optional.
  - `NFR-7.AC1` phase 1–3 features run with the network disabled.
- **NFR-8** Security: no unsafe deserialization of untrusted files; model downloads only from configured sources.
  - `NFR-8.AC1` code review checklist.
- **NFR-9** Testability: every P0 requirement has at least one automated test named with its ID.
  - `NFR-9.AC1` test_<ID>_* exists for each P0.
- **NFR-10** Observability: structured logs (JSON) with job ID and version ID on every line.
  - `NFR-10.AC1` logs can be filtered by ID.
- **NFR-11** `⊕ CL-15` Monitoring overhead: monitoring shall use < 2% CPU and add < 1% to job runtime.
  - `NFR-11.AC1` benchmark with monitoring on vs off.
- **NFR-12** `⊕ CL-15` Telemetry retention: per-second samples kept 7 days, then downsampled to 1-minute averages.
  - `NFR-12.AC1` retention job verified.
- **NFR-13** `⊕ CL-15` Independence: if the monitoring process crashes, jobs must not crash, and the reverse.
  - `NFR-13.AC1` kill the monitor mid-job → job continues.
- **NFR-14** Storage: with default retention (keep_intermediate = 0), disk use per lineage <= base weights + latest weights + one in-progress checkpoint.
  - `NFR-14.AC1` disk audit after 10 runs matches the bound.
- **NFR-15** Deletion safety: retention deletes are atomic per version (metadata updated only after files are removed; a crash mid-delete leaves a recoverable, consistent state).
  - `NFR-15.AC1` kill during cleanup -> rerun completes without orphaned files or dangling registry rows.

## 5. Out of Scope (all phases)

- Multi-user accounts, authentication, multi-tenancy
- Cloud deployment and distributed / multi-GPU training
- Training large models (>100M params) from scratch
- SDXL-class training
- Production-grade serving (autoscaling, load balancing)

## 6. Open Questions

| Question (as written in the input) | Status |
|---|---|
| Which image dataset for phase 1 (license, size, domain)? | **Resolved 2026-11-15:** a 10k subset of COCO 2017, accepted by the owner (DECISIONS.md, M4) |
| Florence-2 vs BLIP: decide after a 50-image quality + speed test. | **Resolved 2026-11-15:** Florence-2-base with the `<CAPTION>` task, chosen by the owner after spike S-06 (RISKS.md) |
| Translation language pairs (EN<->HI? EN<->KN?). | Open. Needed in phase 5 |
| Environment: DECIDED — Docker Compose on WSL2 (required by Celery and Redis). Verify GPU passthrough works on day 1 before any pipeline code. | Decided. WSL-level passthrough verified 2026-11-13; the container-level check is spike S-01 in M0 |

The input ends this section with: "Next: write DATA-4 yourself and send it to me for review. Then start building DATA-1." DATA-4 remains the owner's exercise. DATA-1 is milestone M1.

### Technical constraints

- Target hardware and Dev environment : RTX 4050 laptop (6 GB VRAM) dgpu, 16 GB RAM, Ryzen 7 7840HS with radeon 780M igpu. Optional offload to free remote GPUs (Kaggle/Colab) for heavy fine-tuning jobs.
- Deploy target: Docker + local
- Budget: free tier only
- External services/DB/APIs: has to be fully free. ask if whether email/key is required.

## 7. Tech Stack

Constraints driving choices: local-first, 6 GB VRAM, Windows laptop, resume relevance. Pin exact versions in the lockfile.

### 7.1 Foundation

| | |
|---|---|
| Language | Python 3.11 / 3.12 (verify wheel support per library) |
| Env + deps | uv (lockfile -> reproducibility, NFR-4) |
| Quality | ruff (lint+format), pyright, pre-commit |
| Tests | pytest (+ pytest-xdist); GPU tests behind a marker |
| Config | Pydantic v2 + YAML; pydantic-settings for env |
| Logging | structlog (JSON) -> NFR-10 |

### 7.2 By module

- **DATA** : Pandas + PyArrow, Parquet manifests, DuckDB (SQL on Parquet), Pillow, imagehash (pHash), xxhash/hashlib, PyMuPDF (PDFs)
- **LABEL** : PyTorch, HF transformers (Florence-2 / BLIP, CLIP), open_clip (alt). `⚑ CL-08` Use the native transformers Florence-2 implementation (`florence-community/Florence-2-base`) with a pinned model revision and without `trust_remote_code`.
- **RUN** : Celery workers with Redis as broker.
  - `⚑ CL-01` Queues: "gpu" (prefork pool, concurrency=1) and "cpu" (prefork) for hashing, classical ML, eval. The Celery task only supervises: every job runs in its own child process, which exits when the job ends, so VRAM is fully released.
  - Never initialize CUDA before a fork.
  - Pause/resume: cooperative Redis flag checked every N steps -> checkpoint -> exit; resume = new task from checkpoint. `⚑ CL-01` Hard cancel is the supervisor killing the job's child process; `revoke()` is not relied on.
  - Celery Beat for scheduled jobs (telemetry downsampling, NFR-12).
  - Runs inside Docker/WSL2 (Celery has no official Windows support).
- **BUILD** : PyTorch nn.Module registry, Pydantic layer configs, torchinfo (to verify your own shape/param counts)
- **ML** : scikit-learn (incl. HDBSCAN, HistGradientBoosting), LightGBM, skops (safe persistence)
- **HPO** : Optuna (TPE + MedianPruner); optuna-dashboard optional. Each trial = a job in the RUN queue.
- **EVAL** : torchmetrics, sklearn.metrics, sacrebleu (BLEU/chrF), statsmodels (Wilson CI). `⚑ CL-13` CIDEr is implemented in-house as CIDEr-D, with no Java dependency, and is computed only for datasets that have reference captions.
- **REG** : `⚑ CL-02` PostgreSQL registry tables + checkpoint files; safetensors for weights (cannot execute code, unlike pickle .pt) -> NFR-8
- **LLM** : Ollama (llama.cpp) serving, LangChain + LangGraph, sentence-transformers, FAISS (faiss-cpu)
- **LLM FT** : PEFT (LoRA/QLoRA), bitsandbytes (4-bit), TRL (SFTTrainer), accelerate. Verify bitsandbytes Windows+CUDA support; else WSL2.
- **IMG** : diffusers (SD 1.5), PEFT LoRA, accelerate, 8-bit Adam
- **TRANS** : HF transformers (MarianMT / NLLB-distilled-600M), sentencepiece
- **MON** : psutil, nvidia-ml-py (NVML), torch.cuda memory stats
- **STORE** : PostgreSQL (jobs, events, versions, registry, configs as JSONB) via SQLAlchemy 2.x / SQLModel + Alembic migrations; LISTEN/NOTIFY optional for change events. Redis: Celery broker + pub/sub fan-out of live events and telemetry to WebSockets + job control flags. Parquet + DuckDB: dataset manifests and telemetry archive.

### 7.3 API

FastAPI + Uvicorn; WebSockets (job streams), SSE (one-way feeds); Pydantic -> OpenAPI -> auto-generated TypeScript client (openapi-typescript) so frontend types always match backend.

> **OC-2, accepted by the owner 2026-11-14:** job streams are delivered over SSE, not WebSockets. Where §7.2 and §7.7 say "fan-out to WebSockets", read "fan-out to SSE subscribers". See Appendix B and ADR-0005.

### 7.4 Frontend (React, not Streamlit)

| | |
|---|---|
| Base | React + TypeScript + Vite |
| Styling | Tailwind CSS + shadcn/ui |
| Data fetching | TanStack Query |
| Schema forms | react-jsonschema-form (rjsf) -> UI-2.3 |
| Graph canvas | React Flow (model builder, lineage graph, router view) |
| Charts | Apache ECharts (echarts-for-react) for live streaming |
| Chat | custom components + SSE token streaming |
| Streamlit | throwaway prototypes only |

### 7.5 Ops / tooling

- **CI** : GitHub Actions — ruff, pyright, pytest (CPU), frontend build + typecheck
- **Containers** : Docker Compose on WSL2 from day 1: postgres, redis, api (FastAPI), worker-gpu, worker-cpu, beat, frontend. GPU passthrough via NVIDIA Container Toolkit on WSL2. `⚑ CL-07` Added services: `monitor` (sampler + collector, phase 1), `inference` (phase 2), `ollama` (phase 3). A small PowerShell host script on Windows handles keep-awake and sleep/resume events.
- **Docs** : README, docs/ (this spec, DECISIONS.md, Mermaid architecture diagram)

### 7.6 Deliberately NOT using

| | |
|---|---|
| MLflow / DVC / W&B | they ARE what this project builds; optional "export to MLflow" later |
| RabbitMQ | Redis already serves as broker + pub/sub |
| Airflow/Prefect/Kubeflow | distributed-team orchestration; out of scope |
| Kubernetes | single laptop |
| vLLM | Linux/multi-user throughput focus; Ollama is simpler locally |

### 7.7 Why Postgres + Redis + Celery

"Designed so workers and users can scale out; each component has a specific job":

- **Postgres** : concurrent durable state (sampler + workers + API write simultaneously), JSONB configs/events, migrations.
- **Redis** : Celery broker, live pub/sub fan-out to WebSockets, cooperative job-control flags.
- **Celery** : isolates long GPU jobs from the API, separate gpu/cpu queues, retries, Beat scheduling.

## 8. Engineering standards, known bugs, documentation `⚑ CL-16`

### 8.1 Engineering standards

- Typed everywhere, strict mode; linter + formatter enforced in CI
- Tests: unit for domain logic, integration for API + DB; no mocking the DB in integration tests
- Error handling: `⚑ CL-10` typed errors mapped to one JSON error shape (RFC 9457 problem details plus a stable `code`). See [ADR-0011](decisions/ADR-0011-error-model.md).
- No new dependency without justification
- Conventional commits; one PR per milestone

### 8.2 Known bugs

These are failure modes the design must prevent. Each has a design answer in [ARCHITECTURE.md](ARCHITECTURE.md#10-known-bugs-and-what-prevents-each) and an entry in [ERRORS.md](ERRORS.md).

- **BUG1** - Data after preprocessing and cleaning not being stored,handled and retrieved properly.
- **BUG2** - OOM error due to which program crashes
- **BUG3** - RAM contention during data loading,data processing phases(ram-cpu-ram-gpu-vram) or (ram-cpu-ram)
- **BUG4** - Training interrupted when laptop goes into sleep mode and improper handling of this situation.
- **BUG5** - Stale weights persist in the VRAM after training completion,tests,evals,model transfers or changes during inferencing.
- **BUG6** - improper handling of vram occupancy ,bloated by stale weights.
- **BUG7** - Data bloat during model training runs,pauses or checkpoints and poor versioning issues.

### 8.3 Documentation (only after final approval)

As listed in the input:

| File | Purpose |
|---|---|
| docs/KICKOFF.md | Right after approval, to kickstart the implementation |
| docs/SPEC.md | Everything from PROJECT INPUT plus clarifications, with F-IDs and AC-IDs (F1.AC1...). Nothing from my input may be dropped; flag anything you reworded. |
| docs/ARCHITECTURE.md | Components, data flow diagram, cross-cutting concerns. |
| docs/DATA_MODEL.md | Schema, relations, indexes, invariants. |
| docs/API.md | Interfaces with request/response/error shapes. |
| docs/decisions/ADR-000N-<slug>.md | One per major decision: Context / Decision / Alternatives / Consequences / Status. |
| docs/MILESTONES.md | M0..Mn, each with scope, linked F/AC-IDs, size, and a done-checklist (tests pass, docs updated, demoable). |
| docs/RISKS.md | Risks and spike plans. |
| docs/ERRORS.md | Errors/bugs encountered and solved/fixed |
| docs/DESIGN-SYSTEM.md | (Gate 0 deliverable) |
| docs/SCREEN.md or docs/PAGES.md | (Gate 1 deliverable) |
| CLAUDE.md | Agent instructions |

Inside docs a separate folder (this is not be committed to sent to GitHub, it remains locally on my device ONLY) called learning is for the owner so that I can learn while you build about decisions you and I took and why, how everything is implemented, the structure and workings of the whole system and technical reviews of what algos, codes, dependencies, libraries, models, design was used and why and brief on how they work. Then few questions (an interviewer might ask about my project) with answers.

- docs/learning/Decisions.md
- docks/learning/sys design.md

> **File names used in this repo.** The F-ID scheme is `DATA-2.2.AC1`, not `F1.AC1` `✚ CL-09`. The screen inventory is `docs/SCREENS.md`. The second learning file is `docs/learning/sys-design.md` (typo and space fixed). `docs/learning/` is in `.gitignore`.

### 8.4 Doc maintenance rules (copy into CLAUDE.md)

- Docs are the source of truth. If code and docs disagree, stop and ask.
- Any change to schema, interfaces, or architecture → update the relevant doc in the same change; architectural changes need a new ADR first.
- After each milestone: tick MILESTONES.md, update affected docs.
- Never silently change a requirement; propose the change and wait.

### 8.5 Conventions

- Commit messages reference IDs: `feat(DATA-2.2): pHash near-dup removal`
- Tests reference IDs: `test_DATA_2_2_near_duplicates`
- Every component gets 3 lines in DECISIONS.md: What / How / Why

## 9. AI Agent Working Instructions

(Mirror this section into AGENTS.md / CLAUDE.md at the repo root so coding agents load it automatically.)

### 9.1 General rules

- Every change references a requirement ID (commit, PR, test name).
- Do NOT add a dependency unless a requirement needs it; state which.
- Human owns core logic (dedupe, versioning, batching/VRAM handling, scoring, evaluation, from-scratch layers). Agent may write plumbing (routes, configs, Docker, UI wiring, plots) and must explain any code it writes on request.
- Never mark a requirement done without a passing test_<ID>_* test.
- Record every non-trivial decision in DECISIONS.md (What/How/Why).

### 9.2 UI/UX workflow — DESIGN BEFORE CODE (mandatory gates) `⚑ CL-16`

No UI page or component is implemented until its design is APPROVED by the human. Order:

- GATE 0 Design system -> must be approved before ANY screen design.
- GATE 1 Screen inventory + user flows -> approved.
- GATE 2 Per-screen mockup -> approved (one screen at a time).
- GATE 3 Implementation -> visually matches approved mockup.

Approval protocol:

- After producing a design artifact, the agent STOPS and asks: "Screen <ID> ready for review: <link/path>. Approve, or list changes?"
- Only an explicit "approved" from the human passes the gate. Silence, "looks ok-ish", or partial feedback = NOT approved.
- Each revision is versioned (v1, v2...) and its changes listed.
- Approved mockups are stored in docs/design/screens/<ID>_vN.(html|png) and logged in docs/design/APPROVALS.md: `<date> | <screen ID> | vN | approved by | notes`
- If implementation needs to deviate from an approved mockup, the agent asks first; it never silently diverges.

### 9.3 GATE 0 — Design system (docs/design-system.md + code tokens) `⚑ CL-16`

The agent writes the design system FIRST, covering:

a) **Principles**

- Information-dense but calm: an engineering tool, not a marketing site. Data first, chrome second.
- Status must be readable at a glance (running/paused/failed).
- Avoid generic template look: distinct type pairing, deliberate color, consistent iconography.
- Every screen has defined empty, loading, error, success states.

b) **Tokens** (defined once; exposed as CSS variables + Tailwind theme)

- Color: neutral scale (bg, surface, border, text-primary/secondary/muted), brand/accent, semantic (success, warning, danger, info), job-state colors (queued, running, paused, completed, failed, cancelled, stalled).
- Data-viz palette: categorical (8 colors), sequential, diverging; resource colors fixed app-wide (CPU, RAM, GPU, VRAM, temp).
- Light + dark themes from the same tokens (dark is default for a monitoring tool; both must pass contrast).
- Typography: UI font + monospace font (logs, IDs, metrics); type scale; tabular numerals for all live numbers.
- Spacing scale (4px base), radii, borders, elevation/shadows, z-index layers.
- Motion: durations/easings; respect prefers-reduced-motion.
- Breakpoints: desktop-first (>=1280), usable at 1024.

c) **Components** (each with variants + states: default, hover, focus, active, disabled, loading, error)

Buttons, inputs, selects, toggles, sliders, tabs, tables (sortable, virtualized), cards, badges/status pills, progress bar with ETA, toasts, modals, drawers, tooltips, code/log viewer, metric tile, sparkline, live chart, timeline bar, lineage node, layer node (model builder), chat bubble, empty state, error state.

d) **Patterns**

Forms generated from JSON schema (UI-2.3) styling rules; live-updating numbers (no layout shift); long-running job feedback; destructive-action confirmation; keyboard shortcuts.

e) **Accessibility**

WCAG 2.1 AA contrast; full keyboard navigation; visible focus rings; never encode meaning by color alone (icons/text on status); aria-live for activity log and job status updates.

f) **Deliverables for approval**

- docs/design-system.md
- docs/design/system-preview.html (all tokens + components rendered, light and dark)
- tokens.css / tailwind theme file (implemented only AFTER approval)

### 9.4 GATE 1 — Screen inventory & flows (docs/design/screens.md)

The agent lists every screen with ID, purpose, requirement IDs served, key data shown, and main actions; plus 3-5 core user flows.

Initial inventory:

| ID | Screen | Requirements |
|---|---|---|
| SCR-01 | App shell & navigation | (all) |
| SCR-02 | Datasets list + versions | (DATA-3, UI-2.1) |
| SCR-03 | Dataset version detail + lineage | (DATA-3.3, UI-2.1, UI-2.2) |
| SCR-04 | Pipeline run: live job view | (MON-4.1, MON-4.2, MON-4.3) |
| SCR-05 | Labeling review (accept/reject) | (LABEL-1.5) |
| SCR-06 | Eval report | (EVAL-1.1, EVAL-1.2) |
| SCR-07 | Jobs list / queue control | (RUN-1.1, RUN-1.2) |
| SCR-08 | Model builder canvas | (BUILD-1.*, UI-2.3) |
| SCR-09 | Training config + HPO setup | (BUILD-1.2, HPO-1.*) |
| SCR-10 | HPO results | (HPO-1.5) |
| SCR-11 | Model registry + comparison | (REG-1.*, EVAL-1.3) |
| SCR-12 | Chat (manual + auto/router mode) | (UI-1.1, LLM-*) |
| SCR-13 | System monitor (resources/history) | (MON-2, MON-4.4, MON-4.5) |
| SCR-14 | Settings | (config, thresholds) |

Core flows to diagram: raw folder -> versioned dataset; dataset -> build model -> train -> evaluate; job failure -> diagnose -> resume; chat request -> router -> model -> answer.

### 9.5 GATE 2 — Per-screen mockups

For each screen, in priority order (Phase-1 screens first: SCR-01, 02, 03, 04, 05, 06):

- Low-fi wireframe first (layout + hierarchy only) -> approve.
- Then hi-fi mockup using ONLY design-system tokens/components, with realistic data (real-looking IDs, numbers, log lines).
- Show all states: empty, loading, populated, error; plus dark/light.
- Annotate: which API endpoint feeds each region, what updates live.
- Format: static HTML (preferred, uses real tokens) or PNG export.

### 9.6 GATE 3 — Implementation

- Build only approved screens, using design-system components; no one-off colors/spacing (lint for raw hex values).
- After building, the agent captures a screenshot and compares it to the approved mockup; lists any differences for human sign-off.
- Accessibility check (contrast, keyboard path) before marking done.

END OF SPEC

---

## Appendix A — Change log v0.1 → v0.2

Every difference from the input. All entries were approved by the owner on 2026-11-13 unless marked otherwise.

| ID | Where | Original wording | Now | Why |
|---|---|---|---|---|
| CL-01 | §7.2 RUN | `Queues: "gpu" (--pool=solo, concurrency=1, worker_max_tasks_per_child=1 -> fresh process per job, VRAM fully released)` and `revoke() only for hard cancel.` | gpu queue uses the prefork pool with concurrency 1; the task supervises one child process per job; hard cancel kills the child | The solo pool runs jobs inside the worker process and Celery ignores `max_tasks_per_child` outside prefork, so VRAM was never released. [ADR-0003](decisions/ADR-0003-job-execution-model.md) |
| CL-02 | MON-1.4, §7.2 REG, architecture note | `stored durably (SQLite/DuckDB)`; `SQLite registry tables`; `collector ──► SQLite/DuckDB (durable)` | PostgreSQL is the system of record; Parquet + DuckDB hold manifests and the telemetry archive | The input named two different stores; §7.2 STORE and §7.7 choose PostgreSQL. [ADR-0002](decisions/ADR-0002-postgres-system-of-record.md) |
| CL-03 | NFR-2 | `RAM: peak < 12 GB; datasets are streamed, never fully loaded.` | peak < 9 GB across all Stratal containers, WSL VM capped at 10 GB | The laptop has 15.2 GB usable and WSL saw 7.4 GB by default. [ADR-0007](decisions/ADR-0007-memory-budget.md) |
| CL-04 | §2 Phases | (no slice table) | Phase-1 slice of RUN-1.1–1.5 and UI-2.1 | Phase-1 P0s depend on them. Tags unchanged |
| CL-05 | DATA-6 | (absent) | DATA-6.1 added | MON-3 and the live-view mock show a preprocess stage and BUG1 concerns it, but no requirement defined it |
| CL-06 | RUN-1.1 | `states (queued, running, paused, completed, failed, cancelled)` | `stalled` added as the seventh state | RUN-1.7 and the design tokens already use it |
| CL-07 | §7.5 Containers | `postgres, redis, api (FastAPI), worker-gpu, worker-cpu, beat, frontend` | plus `monitor`, `inference` (phase 2), `ollama` (phase 3) and a Windows host script | NFR-13 needs monitoring in its own process; a per-job GPU worker cannot serve `/predict`; containers cannot see Windows sleep events. [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md), [ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md) |
| CL-08 | §7.2 LABEL | `Pin Florence-2 model revision (trust_remote_code).` | Native transformers implementation, pinned revision, no `trust_remote_code` | Removes remote code execution (NFR-8). [ADR-0009](decisions/ADR-0009-florence-2-native-transformers.md) |
| CL-09 | ID scheme | `F-IDs and AC-IDs (F1.AC1...)` | Module IDs are the F-IDs; criteria are `<ID>.AC<n>` | The input's own conventions use `DATA-2.2` in commits and tests. [ADR-0010](decisions/ADR-0010-requirement-ids-and-traceability.md) |
| CL-10 | §8.1 | `Error handling: <pattern, e.g. typed errors → consistent JSON error shape>` | Typed errors → RFC 9457 problem details with a stable `code` | The placeholder was unfilled. [ADR-0011](decisions/ADR-0011-error-model.md) |
| CL-11 | MON architecture note | (allocator stats source unstated) | PyTorch allocator stats are reported by the job runtime wrapper | Another process cannot read them |
| CL-12 | Storage (no requirement text changed) | (unstated) | Datasets live on a Linux-side Docker volume; raw files are copied in once at ingest | Windows-mounted folders are slow from Linux. [ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md) |
| CL-13 | §7.2 EVAL | `pycocoevalcap (CIDEr — needs Java; else implement CIDEr-D yourself or demote to P2)` | In-house CIDEr-D, computed only where reference captions exist | Avoids a Java runtime; CIDEr is undefined without references |
| CL-14 | REG-1 | Two REG-1 blocks. The first reads: `REG-1.1 Version every trained model with full resolved config, dataset version, metrics, checkpoint. -- AC: any model traceable to exact data + config.` `REG-1.2 Model states: base, partial (paused), fully trained. -- AC: states shown and filterable.` `REG-1.3 Fine-tunes store only adapters/deltas (e.g. LoRA), not full weight copies. -- AC: fine-tune version is MBs, not GBs.` `REG-1.4 FastAPI inference endpoint per registered model. -- AC: /predict returns results for sample input.` | One REG-1 block, using the second block's full-sentence wording | Same meaning stated twice |
| CL-15 | NFR-11 to NFR-13 | Stated three times, in two wordings. Long form (twice): `NFR-11 Overhead: monitoring shall use < 2% CPU and add < 1% to job runtime. — AC: benchmark with monitoring on vs off.` `NFR-12 Retention: per-second samples kept 7 days, then downsampled to 1-minute averages. — AC: retention job verified.` `NFR-13 Independence: if the monitoring process crashes, jobs must not crash, and the reverse. — AC: kill the monitor mid-job → job continues.` Short form (once): `NFR-11 Monitoring overhead: < 2% CPU, < 1% added job runtime. -- AC: benchmark monitoring on vs off.` `NFR-12 Telemetry retention: per-second samples kept 7 days, then downsampled to 1-minute averages. -- AC: retention job verified.` `NFR-13 Independence: monitor crash must not crash jobs, and vice versa. -- AC: kill monitor mid-job -> job continues.` | One statement each, in §4, using the long form's sentence and the short form's label | Same meaning stated three times |
| CL-16 | Section numbers | Section 8 absent; "UI/UX workflow" and "GATE 0" unnumbered | Numbered §8, §9.2 and §9.3 | So every part can be cited |
| CL-17 | DATA-2 | (unstated) | "Remove" means mark and keep, with duplicate-group IDs | Needed for leakage-safe splits in DATA-4 |

## Appendix B — Open clarifications (not yet approved)

These are the agent's interpretations. They do not change any requirement text, and each will be confirmed with the owner before the milestone that depends on it.

| ID | Topic | Proposed reading | Needed by |
|---|---|---|---|
| OC-1 | MON-3 "Staging" row | **Resolved 2026-11-14, accepted by the owner:** every stage ends with a commit step (write manifest, compute version ID, record lineage) that emits the Staging messages; it is not a separate pipeline stage | done |
| OC-2 | Job streams: WebSocket or SSE | **Resolved 2026-11-14, accepted by the owner:** job streams use SSE, not the WebSockets listed in §7.3 (MON-1.4 allows either). The feed is one-way and SSE resumes after a disconnect. [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md) | done |
| OC-3 | Unspecified parameter defaults | **DATA-6.1 part resolved 2026-11-15, accepted by the owner:** the preprocess maximum side defaults to 768 px (MON-3's "384px" is an example message). Still proposed: NFR-5 N = 10 minutes; RUN-1.7 T = 300 s | M3 done / phase 2 |
| OC-4 | Version ID length | **Resolved 2026-11-14:** twelve hex characters, set when DATA-3.2 was implemented at the owner's request. The examples' six (`ds_7f3a9c`) collide too easily | done |
| OC-5 | Thresholds owned by the owner | pHash distance (DATA-2.2) and CLIP cut-off (LABEL-1.4) are chosen by the owner from measured distributions. **pHash part resolved 2026-11-15:** 8 of 64 bits, chosen by the owner (measurements in DECISIONS.md). **CLIP part resolved 2026-11-15:** 0.2 on the plain-cosine scale, chosen by the owner from the M4 demo's distribution | M3 done / M4 done |

## Appendix C — Requirement counts

| Phase | Numbered requirements |
|---|---|
| 1 | 31, plus DATA-4 (unwritten) and DATA-6.1 (added) |
| 2 | 58 |
| 3 | 12 |
| 4 | 5 |
| 5 | 5 |
| Total from the input | 111, plus NFR-1 to NFR-15 |
