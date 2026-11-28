# Stratal — Errors and Bugs

A running log of errors and bugs: what was seen, what caused it, how it was fixed and what proves it stays fixed.

## How to add an entry

Add entries to [Log](#log), newest first, numbered `E-nnn`.

```markdown
### E-nnn — Short title
- **Date:**
- **Seen:** what happened, with the exact error text
- **Where:** milestone, component, requirement ID
- **Cause:**
- **Fix:** what changed, with the commit
- **Guard:** the test that now fails if it comes back
- **Status:** open | fixed | verified
```

An entry is `fixed` when the change is in, and `verified` only when its guard test passes.

## Inherited bugs

These seven come from the owner's input (SPEC §8.2). They describe failures the design must prevent. Each was **prevention designed, not yet verified** until its guard test passed; the last column says in which milestone that happened. All seven are now verified, BUG2 and BUG5 for training too since M8 (`test_RUN_1_5_an_oversized_batch_fails_cleanly_and_the_suggested_batch_fits`, `test_RUN_1_4_memory_allocated_returns_to_baseline_after_training`, both on the card).

| Bug | As written in the input | Design answer | Guard test | Verified in |
|---|---|---|---|---|
| BUG1 | Data after preprocessing and cleaning not being stored, handled and retrieved properly | Immutable manifests, atomic writes, one read path ([ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)) | `test_BUG1_*` | M3 |
| BUG2 | OOM error due to which program crashes | Own process per job, VRAM cap, supervisor reports kernel kills ([ADR-0003](decisions/ADR-0003-job-execution-model.md), [ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG2_*` | M2, M4 |
| BUG3 | RAM contention during data loading, data processing phases | Streaming, bounded queues, per-container limits ([ADR-0007](decisions/ADR-0007-memory-budget.md)) | `test_NFR_2_*` | M7 |
| BUG4 | Training interrupted when laptop goes into sleep mode and improper handling of this situation | Keep-awake, sleep events, per-batch commits, resume in a fresh process ([ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md)) | `test_BUG4_*` | M2 |
| BUG5 | Stale weights persist in the VRAM after training completion, tests, evals, model transfers or changes during inferencing | Process exit is the unload; verified unload for long-lived processes ([ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG5_*` | M4 |
| BUG6 | Improper handling of VRAM occupancy, bloated by stale weights | One GPU lease; free-VRAM check before every load ([ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG6_*` | M4 |
| BUG7 | Data bloat during model training runs, pauses or checkpoints and poor versioning issues | Retention as a job after every run (REG-2), one rolling in-run checkpoint removed on success or cancel, atomic deletes with crash recovery and an orphan sweep, a dry run, and a disk report checked against the bytes on disk ([ADR-0015](decisions/ADR-0015-model-registry-and-retention.md)) | `test_NFR_14_*`, `test_BUG7_*`, `test_NFR_15_*` | M8 (2026-11-18: 10 runs with default retention leave base + latest + one in-progress checkpoint, also after a cancelled pause and a crash with retry; on the live stack the M8 demo's retention freed exactly what its dry run listed) |

## Log

### E-025 — Checking a model spec on the 2-million-row table killed the api

- **Date:** 2026-11-27
- **Seen:** SCR-08 at `/models/build?version=ds_1c0761f1fe23` showed "The spec could not be checked · dependency_unavailable · The Stratal API did not answer". `api` restarted at 18:05:37, 4 s after `GET /model-specs/schema`; its restart count went to 2.
- **Where:** SCR-08's spec check, `POST /model-specs/validate` → `stratal.build.io.version_io`, on a tabular version; NFR-2, BUILD-1.3. The version diff (`GET /versions/{id}/diff`, DATA-3.5), left open by E-024, had the same flaw.
- **Cause:** Two things.
  - To learn the table's input width and classes, the check called `fit(load_tabular(manifest))`: the training preprocessing, which loads every train row's values into Python lists (about 1.4 million rows here). The diff likewise read both manifests whole into dicts keyed by row.
  - Underneath both, PyArrow 26 opens Parquet files with `pre_buffer=True` by default. A streamed scan then reads ahead every row group it will touch, so even batch-by-batch reading held most of the file in memory: a full scan of the 348 MB manifest took 331 MB of Arrow's pool, against 3 MB with `pre_buffer=False`. This is also why E-024's fix still peaked at 265 MiB.
- **Fix:**
  - `build.io.table_shape` gives the builder its ends in one streamed pass that keeps only the train split's distinct categories and classes. The numeric statistics are not needed for a shape, and training still runs the real `fit`.
  - The diff walks both manifests side by side in `row_key` order (manifests are written sorted). Arrow's null counts, a batch at a time, find which columns each side fills.
  - Every streaming read of a manifest or a source table opens it with `pre_buffer=False`.
  - Live, each on a freshly started api: the build screen loads "input vector 2881 → 1 classes" with the api's anonymous memory peaking at 208 MiB; split against its parent compares 2,075,259 rows in 34 s, peaking at 210 MiB (381 MiB before `pre_buffer=False`). No restarts.
- **Guard:** `tests/unit/test_NFR_2_table_shape.py` holds `table_shape` equal to `fit` (classification and regression, with ignored, ordinal, one-hot and embedding columns). `test_NFR_2_the_diff_walks_both_versions_side_by_side` forbids whole-file reads and uses batches of 7 rows.
- **Status:** verified

### E-024 — Opening a 2-million-row table's version killed the api

- **Date:** 2026-11-27
- **Seen:** SCR-03 for `ds_1c0761f1fe23` (the split of the household power table, 2,075,259 rows) showed "Version ds_1c0761f1fe23 could not be loaded · dependency_unavailable · The Stratal API did not answer". Docker logged `oom` and `die 137` for `api`, at its 400 MiB limit, about 20 s after the page's first requests; it restarted on its own.
- **Where:** M11b's tabular data meeting M3/M11's version screen: `GET /versions/{id}/rows` (`stratal.storage.manifest.select_rows`) and `GET /versions/{id}/distributions` (`stratal.data.profile`); NFR-2, UI-2.1, UI-2.2.
- **Cause:** Both read the whole manifest: `select_rows` with `pq.read_table` even for a page of 50 rows, and distributions as a list of one Python dict per row. That was cheap for image datasets (1 to 2 MB per 10,000 rows), but this manifest is 348 MB on disk, and 2 million dicts take several GB. M11b made ingest and the Columns tab stream; these two endpoints were missed.
- **Fix:** `select_rows` counts matches over the filter columns alone, in batches, then reads whole rows in `row_key` order, 2,000 at a time, until the page is full. `distributions` makes one pass over a stream of the 17 columns it uses, keeping counters and each numeric column as 8-byte floats. Batches are decoded on one thread. Live, on a restarted api: the first page in 0.4 s, a filtered page in 0.6 s, distributions in 7 s (then cached); the api's anonymous memory peaked at 265 MiB while the page loaded everything at once, and no restart followed.
- **Guard:** `tests/unit/test_NFR_2_large_manifest_reads.py`: pages and distributions with whole-file reads forbidden and batches of 7 rows, compared with a whole read.
- **Later:** the version diff still read both manifests whole; fixed with E-025, which also found why streaming here still used 265 MiB (Parquet's pre-buffering).
- **Status:** verified

### E-023 — Ollama's runner was killed after about fifteen RAG questions

- **Date:** 2026-11-27
- **Seen:** M12's acceptance test asked the question set's RAG questions in a row; after about fifteen, an answer failed with `llm_unavailable … Ollama refused POST /api/chat: 500 … model runner has unexpectedly stopped, this may be due to resource limitations`. Ollama logged `llama-server process no longer running … signal: killed`; the `ollama` cgroup showed `oom_kill 3` and its peak at the 1,000 MiB limit. With the limit raised to 2,000 MiB for a measurement the runner's anonymous memory reached 1.7 GB and it was killed again.
- **Where:** M12, the `ollama` service (`compose.yaml`); ADR-0021 rule 11, NFR-2.
- **Cause:** The llama-server that Ollama 0.40.16 starts keeps each finished prompt's state in RAM, up to 8,192 MiB by default (`--cache-ram`), and Ollama does not set it. Its log read `cache state: 14 prompts, 1232.685 MiB (limits: 8192.000 MiB …)`: about 80 MB per RAG prompt of 700–800 tokens. S-11's peak of 717 MiB was measured on fewer, shorter prompts. RAG prompts share only their first 63 tokens, so the cache saved nothing.
- **Fix:** `LLAMA_ARG_CACHE_RAM: "0"` on the `ollama` service, which llama-server reads as `--cache-ram 0`. The 1,000 MiB limit is unchanged. Re-run: the runner's anonymous memory peaked at 563 MiB over the whole test, no OOM kill; NFR-6 met 20 of 20.
- **Guard:** `tests/acceptance/test_M12_llm_acceptance.py` (over 60 RAG questions in a row on the live stack).
- **Status:** verified

### E-022 — The dataset list failed once a corpus existed

- **Date:** 2026-11-27
- **Seen:** SCR-02 showed "Datasets could not be loaded · internal_error". The API logged `ResponseValidationError: … 'modality' … Input should be 'image', 'tabular' or 'text' … input: 'text corpus'`.
- **Where:** M12, `GET /datasets` (`stratal.api.schemas.Dataset`); DATA-5.3. Found at SCR-03 for corpora's Gate 3.
- **Cause:** The corpus types store their modality as *text corpus* and *instruction pairs* (SCREENS.md v5 shows them so), but the response schema and DATA_MODEL.md still listed only `image`, `tabular` and `text`.
- **Fix:** The schema and DATA_MODEL.md list both (commit `2658790`).
- **Guard:** `test_DATA_5_3_instruction_pairs_are_ingested_cleaned_and_split` lists the datasets through the API.
- **Status:** verified

### E-021 — A stopped answer vanished from the conversation until a reload

- **Date:** 2026-11-27
- **Seen:** On SCR-12, *Stop* removed the whole turn, question and partial answer, from the conversation; after a reload it was there, with "stopped at –".
- **Where:** M12, SCR-12 (`ChatScreen.tsx`) and `api`'s chat relay (`stratal.api.chat`); LLM-1.1.
- **Cause:** The screen dropped its live copy and refetched the conversation at once, but `api` stores a stopped answer only when it notices the closed stream, which can be later. A stopped answer also never gets `inference`'s `done`, so it had no total time.
- **Fix:** The live copy stays until the stored one is there, the screen polling every second for up to 30 s; `api` times a stopped answer with its own clock (commit `8042f14`).
- **Guard:** `test_LLM_1_1_stop_keeps_the_partial_answer` (Vitest), `test_LLM_1_1_a_stopped_answer_is_stored_with_its_text_and_time`.
- **Status:** verified (checked live with the real model)

### E-020 — A restarted `inference` left the LLM on the card without the lease

- **Date:** 2026-11-27
- **Seen:** After `inference` was rebuilt and restarted with `ollama` still running, the first answer showed "cold start 0.01 s" and the load event read "card 1.8 → 1.8 GB".
- **Where:** M12, `stratal.inference.llm.LlmAdapter`; ADR-0021 rules 4 and 5, BUG6.
- **Cause:** Ollama kept the model loaded across `inference`'s restart (`OLLAMA_KEEP_ALIVE=-1`). The new adapter believed nothing was loaded, so the model sat in VRAM with no lease held, and the next load measured the card with the model on it as its baseline, which the idle unload would then have "returned" to.
- **Fix:** Before its first load, the adapter unloads whatever Ollama holds and waits for it to go (commit `a9a4578`). Live: "card 1.8 → 0.2 GB" at start.
- **Guard:** `test_LLM_1_1_a_restarted_service_unloads_what_ollama_still_holds`.
- **Status:** verified

### E-019 — Stop did not stop the model, and the next question hung for 132 s

- **Date:** 2026-11-27
- **Seen:** After *Stop* on SCR-12, Ollama's log showed the chat call running to its full 512 tokens; no idle unload followed; the next question waited and failed after 132 s with `dependency_unavailable … did not answer (timed out)`.
- **Where:** M12, `inference`'s `/chat` (`stratal.inference.app`); LLM-1.1, ADR-0021 rule 5.
- **Cause:** Starlette runs a plain generator's stream in a worker thread and, when the client disconnects, cancels the task without closing the generator. The generation's `finally`, which closes Ollama's stream and frees the LLM's slot, never ran, so the slot stayed taken.
- **Fix:** `closing_stream` wraps the NDJSON in an async generator that closes the chain on cancellation, and `ndjson` closes its events (commit `a9a4578`). Live: the stopped answer ended at once, and the next answer came in 0.82 s.
- **Guard:** `test_LLM_1_1_stop_closes_the_generation_on_disconnect`, `…_on_cancel`, `test_LLM_1_1_ndjson_closes_its_events`.
- **Status:** verified

### E-018 — Cleaning a corpus removed its headings

- **Date:** 2026-11-27
- **Seen:** The first live index of Stratal's docs had almost every chunk's section equal to the document's title: `chunk` found no `##` headings to cut at.
- **Where:** M12, `clean_text` (`stratal.data.record_clean`) on a `text_corpus`; DATA-5.3, LLM-2.1, ADR-0022 rule 1.
- **Cause:** `clean_text` normalised whitespace by collapsing every run, line breaks included, so a document became one line and its Markdown headings stopped being headings.
- **Fix:** A `keep_line_breaks` setting, on for `text_corpus` only and left out of the config when off, so earlier versions keep their IDs (commit `e9e7535`; ADR-0022's As built).
- **Guard:** `test_LLM_2_1_corpus_clean_keeps_lines_and_old_configs_keep_their_ids`; `test_LLM_2_1_corpus_is_chunked_and_indexed` checks a document's headings after cleaning.
- **Status:** verified

### E-017 — Turning a study on changed SCR-09's estimator under its form

- **Date:** 2026-11-22
- **Seen:** On SCR-09 in Classical mode, on wine's split with `kmeans` selected, *Turn on* (or a Tune toggle on one of k-means's fields) started a study, and `kmeans` left the estimator list. The select then showed `logistic_regression`, while the sentence beside it and the parameter form were still k-means's. Found while checking the owner's report that logistic regression was missing; it is offered only on classification splits, by design.
- **Where:** M11, SCR-09 (`frontend/src/screens/build/RunScreen.tsx`); ML-1.1, DATA-5.5, HPO-1.1.
- **Cause:** The list was filtered, and the estimators that ignore the target were dropped while a study was on. Dropping the selected one left the native select showing the first remaining option, while the page's state still held `kmeans`.
- **Fix:** Every estimator stays listed, disabled with its reason when the split or a study rules it out. While an estimator that ignores the target is selected, *Turn on* is disabled with its reason and its fields have no Tune toggles (SCR-09's estimator-list addendum, approved 2026-11-22).
- **Guard:** `test_DATA_5_5_turning_a_study_on_never_changes_the_selected_estimator` (Vitest, `frontend/src/screens/build/TrainEntry.test.tsx`).
- **Status:** verified (the guard passes; checked live on wine's split, 2026-11-22)

### E-016 — The raw-hex lint read a record's row key as a colour

- **Date:** 2026-11-22
- **Seen:** The full `check.ps1 -Gpu` before M11's merge stopped at its frontend step: `src/screens/models/Compare.test.tsx:121: raw colour #0000021`. The line holds the row key `wine.csv#0000021`, not a colour.
- **Where:** M11, `frontend/scripts/no-raw-hex.mjs` (SPEC §9.6's rule against one-off colours), on SCR-11 v2's Try it test (REG-1.4). The test came in with SCR-11 v2 and had passed in Vitest, but no full check had run since.
- **Cause:** The rule matched any `#` followed by 3 to 8 hex digits. A record's row key is `file#line` with the line zero-padded (M10, `stratal.data.tables`, `texts`), so any such key in the frontend's source or tests looked like a colour.
- **Fix:** A CSS hex colour has 3, 4, 6 or 8 digits and never follows a word character, and the rule now requires both (commit `bc67edc`). Colours in quotes, after a colon, in brackets and in parentheses are still caught.
- **Guard:** `check.ps1`'s frontend step: `npm run lint` scans the test that holds the row key.
- **Status:** verified (`check.ps1`'s frontend step, 2026-11-22)

### E-015 — The pause toast named a step before the one the run paused at

- **Date:** 2026-11-19
- **Seen:** In the M8 demo, SCR-07's toast said `job_0051 paused at epoch 9, step 193`, but the pause's checkpoint was at step 260 of epoch 9 (global step 3,244), and the resume started there.
- **Where:** M8, the train stage's pause (RUN-1.2) and SCR-07's toast, which reads the stage's `step`.
- **Cause:** Progress is reported at most once a second, and the stage's `step` is the last report. The pause checkpointed up to a second of steps later and reported nothing more.
- **Fix:** At a pause, after the checkpoint, the stage sends one last progress report (substage `pause`) with the checkpoint's step and epoch.
- **Guard:** `test_RUN_1_2_a_paused_run_checkpoints_and_resumes_where_it_stopped` checks that the paused stage's `step` and `epoch` are the checkpoint's.
- **Status:** verified (the full `check.ps1 -Gpu`, 2026-11-19)

### E-014 — An unreadable weights file came back as a 500

- **Date:** 2026-11-19
- **Seen:** While writing M8's NFR-8 tests, a pickle saved as a version's `model.safetensors` made `/predict` return `500 internal_error` with safetensors' own message (`Error while deserializing header: header too large`). A run resuming from such a checkpoint failed as `stage_failed`.
- **Where:** M8, every safetensors read: `train/weights.py`, `train/checkpoint.py`, `inference/holder.py`, `train/backfill.py`; NFR-8, ADR-0011.
- **Cause:** `safetensors.torch.load_file` raises `SafetensorError`, which is not a typed error. Nothing ran (safetensors parses no code), but clients got no stable code and no suggested action.
- **Fix:** `weights.read_tensors` turns it into `weights_unreadable` (409) with "Re-train the version" or "Cancel the run and start it again"; every read uses it.
- **Guard:** `test_NFR_8_a_pickle_planted_as_weights_or_a_checkpoint_is_refused_and_never_runs`, `test_NFR_8_a_run_resuming_from_a_planted_checkpoint_fails_typed_and_runs_nothing` (`test_M8_nfr.py`), `test_NFR_8_a_pickle_planted_as_a_versions_weights_is_refused_and_never_runs` (`test_M8_inference.py`).
- **Status:** verified (the full `check.ps1 -Gpu`, 2026-11-19)

### E-013 — A job killed by a signal could not be read through the API

- **Date:** 2026-11-18
- **Seen:** While writing M8's registry tests, `GET /jobs/{id}` for a training job killed by the test crash point returned 500: `ResponseValidationError ... ('response', 'error', 'detail'): Input should be a valid string, input: {'signal': 'SIGKILL'}`.
- **Where:** `describe_exit` in the supervisor (M2) writes `detail` as facts (`{"signal": ...}`, `{"exit_code": ...}`); the response model `JobError.detail` (M6) allowed only text. The job screens render `detail` as text too, so a killed job would also have broken SCR-04 and SCR-07 once the API returned it.
- **Cause:** Two ends of one field written at different times. Earlier kill tests read jobs through the harness, not the API, so none caught it.
- **Fix:** `JobError.detail` is text or an object; the frontend shows an object as words through `errorDetail` (`signal SIGKILL · peak rss mb 2410`).
- **Guard:** `test_REG_1_1_a_retry_after_the_weights_were_registered_trains_nothing` reads a killed job through the API; `errorDetail (E-013)` in `format.test.ts`.
- **Status:** verified (the full `check.ps1`, 2026-11-19)

### E-012 — After Modern Standby, WSL lost the GPU

- **Date:** 2026-11-18
- **Seen:** Starting a GPU container for the M8 training tests failed: `nvidia-container-cli: initialization error: WSL environment detected but no adapters were found`. The GPU worker that had been running for two hours reported `Failed to initialize NVML: Generic operating system error`. Windows itself still saw the RTX 4050 (driver 621.24, 37 °C, P8, 0 MiB used), and Device Manager showed both adapters as OK.
- **Where:** Docker Desktop on WSL2, host side; any GPU work (M8 training, ADR-0006); BUG4
- **Cause:** The laptop was in Modern Standby from 11:09 to 13:17 (System log, events 506 and 507). After it woke, WSL's virtual GPU device no longer had the adapter. Spike S-03 (M0) found no such loss after a short standby, so this depends on how long or deep the standby is, or on the dGPU being powered down meanwhile.
- **Fix:** Restart WSL (`wsl --shutdown`, then start Docker Desktop again). Prevention is ADR-0008's: keep the laptop awake while jobs run (`host/stratal-host.ps1`).
- **Again on 2026-11-19:** after Modern Standby periods from 16:02 to 16:32 (events 506 and 507), recreating the containers for M9's demo failed the same way. A GPU container that was already running (the torch test suite) kept its GPU to the end; only new containers lost it. Fixed with the owner's OK by the same restart; the demo run then trained on the GPU.
- **Guard:** none automatic yet. A GPU job started in this state fails at once with `cuda_unavailable` and says what to do; the supervisor's sleep detection puts the suspend on the timeline (BUG4).
- **Status:** open

### E-011 — Two jobs making the same version at once: the second failed

- **Date:** 2026-11-17
- **Seen:** While checking SCR-07 in M6, two identical demo runs (ingest to preprocess, same source and config) were started together and ran side by side on the CPU workers. Both computed `ds_1fb7a64c25ce`; `job_0022` failed in ingest with `UniqueViolation` on `dataset_versions_pkey`. A retry succeeded, because the version then existed.
- **Where:** `register_version` (M1, DATA-3.3), reached by every stage's commit; BUG1
- **Cause:** Registration read the row, found none, and inserted. Two transactions could both read before either committed. Content addressing makes identical work produce identical IDs, so the second insert was the same version, not a conflict in meaning; only the check-then-insert was unsafe.
- **Fix:** The insert is `ON CONFLICT (version_id) DO NOTHING`. A second transaction waits for the first, inserts nothing, and reads the row back. If the existing row's content or config hash differs, that is a real collision and raises `conflict`. Written by the agent at the owner's request (2026-11-17, M7).
- **Guard:** `test_BUG1_two_jobs_registering_the_same_version_at_once_both_succeed` (two connections; fails on the old code with this exact error), `test_BUG1_a_different_version_under_the_same_id_is_refused`
- **Status:** verified

### E-010 — A version ID depended on where the data folder was

- **Date:** 2026-11-16
- **Seen:** The new NFR-4 test ran the phase-1 chain twice on one source folder, each run in its own data folder. The ingest versions differed (`ds_f68a49483e3b` and `ds_21d99864d836`). The only differing cell was the `reason_detail` of a corrupt file: `cannot identify image file '/tmp/…/d1/blobs/sha256/bc/7f/bc7f15ce…'` against the same text with `d2`.
- **Where:** M1 ingest, and the same pattern in M3 clean and preprocess; NFR-4, DATA-3.2 rule 1 ("on any machine and any run")
- **Cause:** Pillow's and the OS's error messages name the file by its absolute path. The stages copied them into `reason_detail`, which is part of the manifest and so of the content hash. The data folder is always `/data` in the containers, so this did not show on this laptop. It would show on any other layout, and when the source mount moves (`unreadable`).
- **Fix:** Before a message is stored, the path in it is replaced: by the row key at ingest and clean, by `blob <first 12 hex>` at preprocess. Committed with the M5 NFR-4 tests. Existing versions keep their IDs. Re-ingesting a folder with corrupt files gives a new ID once, because the detail text changed.
- **Guard:** `test_NFR_4_rejection_details_name_no_local_path`, `test_NFR_4_two_runs_give_the_same_version_ids_and_splits`
- **Status:** verified

### E-009 — The collector and the supervisor could deadlock on a failing stage

- **Date:** 2026-11-15
- **Seen:** The first CI run on GitHub failed `test_NFR_13_kill_the_job_and_the_monitor_continues` with `psycopg.errors.DeadlockDetected: deadlock detected … while updating tuple (0,2) in relation "job_stages"`. The test passes on the laptop; only the timing differed.
- **Where:** M2 job and event spine; `mon/collector.py`, `runtime/supervisor.py` `finalize`; NFR-13, MON-1.4
- **Cause:** Opposite lock order. `finalize` locks the `jobs` row and then updates the `job_stages` row. The collector, storing a `stage_failed` event, updated the `job_stages` row first (its metrics) and then the job's summary in the `jobs` row. When both ran at the moment a stage ended, each waited for the other, and PostgreSQL killed one: the job's outcome or a batch of events would be rolled back.
- **Fix:** `write_batch` in the collector locks the rows of every job in the batch, in ID order, with `FOR NO KEY UPDATE`, before touching a stage row. Every writer of stage rows now takes the job row first, as the supervisor, the watchdog and the job service already did. The child's `_record` holds only one stage row and cannot close a cycle.
- **Guard:** `test_NFR_13_collector_and_supervisor_lock_job_rows_in_one_order` forces the interleaving: it holds the job lock as `finalize` does, waits until the collector is blocked, then updates the stage. It failed with `DeadlockDetected` before the fix and passed five runs in a row after.
- **Status:** fixed; verified locally, and on GitHub when this reaches `main`

### E-008 — The MON-2.4 GPU test was flaky: NVML counts new VRAM late on WSL

- **Date:** 2026-11-15
- **Seen:** `test_MON_2_4_nvml_and_pytorch_figures_are_recorded_side_by_side` failed 3 times in 4 runs with `assert (565.8046875 - 228.6171875) >= (512 * 0.9)`; it had passed in the M2 check.
- **Where:** `tests/gpu/test_MON_2_gpu.py`; MON-2.4
- **Cause:** On WSL, NVML reports memory from an allocation that has never been written about 100 ms late. Measured straight after `torch.empty` of 512 MB: +337 MB, then the full amount 0.1 s later. `torch.cuda.synchronize()` alone does not change this. Writing the memory does, but only once the write has run: `torch.zeros` without a sync still failed 3 times in 10, because the fill kernel is asynchronous. The test read NVML in the same instant it allocated. The sampler itself is not affected in any way that matters: it reads once a second, so it is at most one sample behind.
- **Fix:** The test allocates with `torch.zeros` and then calls `torch.cuda.synchronize()`.
- **Guard:** The test itself: 20 passes in 20 runs after the fix, and the full `check.ps1 -Gpu`.
- **Status:** verified

### E-007 — A unit test leaked a bound version ID into another test

- **Date:** 2026-11-14
- **Seen:** `test_NFR_10_every_line_is_json_with_job_and_version_id` failed in the full run with `assert 'ds_7f3a9c21b04e' is None`, though it passed alone.
- **Where:** `tests/unit/test_MON_1_events.py`; `JobContext.set_version`
- **Cause:** `set_version` binds the version ID to the logging context for the rest of the process, which is right for a job's child (one job per process) and wrong in a test process that runs many tests. A new M2 test called it and did not unbind.
- **Fix:** The test clears the logging context in a `finally`.
- **Guard:** The NFR-10 test itself: it asserts the IDs are null before any job binds them.
- **Status:** verified

### E-006 — The CPU worker would not start: `No module named 'packaging'`

- **Date:** 2026-11-14
- **Seen:** `worker-cpu` restarted in a loop on its first start; the traceback ended in `kombu/transport/redis.py: from packaging.version import Version`.
- **Where:** `pyproject.toml`; the `api` image, which `worker-cpu` and `monitor` share
- **Cause:** Celery was declared as plain `celery`. Its Redis transport needs extra packages that only the `celery[redis]` extra declares. The dev image happened to contain `packaging` through a test tool, so every test passed and only the production image failed.
- **Fix:** Depend on `celery[redis]`.
- **Guard:** The integration tests run in the dev image, not the image the workers use; the M2 demo on the real stack is what caught it. Since M7, `tests/acceptance` runs a whole pipeline through the running stack, and so through the images `api`, the workers, `monitor` and `beat` use. It runs on request (`-m acceptance`), not in `check.ps1`.
- **Status:** verified on the running stack
### E-005 — Exceeding VRAM does not fail; it silently spills and crawls

- **Date:** 2026-11-13
- **Seen:** In spike S-02, a process allocated 8,192 MB on the 6,140 MB card with no error. Each 256 MB step went from 0.01 s to as much as 1.15 s. NVML reported 5,792 MB used throughout.
- **Where:** M0, spike S-02; relates to BUG2, BUG3 and BUG6
- **Cause:** Under WSL2 the Windows driver satisfies requests beyond dedicated VRAM from shared system memory instead of refusing them. NVML reports only dedicated memory, so the overflow is invisible there. This is a likely contributor to the owner's original symptoms: "VRAM bloat" that monitoring did not explain, RAM contention, and jobs that degrade instead of failing.
- **Fix:** Every GPU process caps PyTorch's allocator at 0.85 of the card, which makes PyTorch raise a catchable `OutOfMemoryError` at 5.1 GB (ADR-0006, items 8 to 10). Verified in the same spike.
- **Guard:** `test_BUG2_an_oom_becomes_cuda_out_of_memory_and_frees_what_it_took` and `test_NFR_1_peak_vram_of_the_label_stage_stays_under_5_5_gb` (M4, on the card). Training sets the same cap (`train/stage.py`, `VRAM_FRACTION`) and `test_NFR_1_peak_vram_of_a_training_run_stays_under_5_5_gb_and_is_logged` measures it (M8); a training OOM fails with a suggested batch that then fits (RUN-1.5, on the card).
- **Status:** verified (label stage, M4; training's cap, M8)

### E-004 — Type checker could not resolve the test client

- **Date:** 2026-11-13
- **Seen:** `pyright` reported 48 errors of the form `Type of "response" is unknown` in the API tests, and pytest warned: `Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
- **Where:** M0, test dependencies
- **Cause:** The installed Starlette builds its test client on `httpx2`. With only `httpx` present it falls back at run time, but the type checker follows the `httpx2` import and finds nothing.
- **Fix:** Replaced the dev dependency `httpx` with `httpx2` (commit `fa80872`).
- **Guard:** `pyright` in strict mode is part of `scripts/check.ps1`.
- **Status:** verified

### E-003 — Docker kept using C: after the disk location was changed

- **Date:** 2026-11-13
- **Seen:** After setting `DataFolder` in Docker Desktop's `settings-store.json`, Docker started normally but still wrote to `C:\Users\…\AppData\Local\Docker\wsl\disk\docker_data.vhdx`.
- **Where:** M0, environment setup, ADR-0001
- **Cause:** `DataFolder` is the location for the Hyper-V backend's disk. For the WSL2 backend the key is `CustomWslDistroDir`. Docker's own log showed the two as `vm.resources.dataFolder` and `vm.resources.wslDataFolder`.
- **Fix:** Removed `DataFolder`, set `CustomWslDistroDir` to `D:\Docker\wsl`. The disk image had been copied there first with Docker stopped, so nothing was lost while the wrong key was in place.
- **Guard:** None automated. Verified by file timestamps (only the D: image changes) and by comparing the image and volume lists before and after: identical, 93 images and 13 volumes.
- **Status:** verified

### E-002 — Containers reject every HTTPS certificate

- **Date:** 2026-11-13
- **Seen:** `uv lock` in a container failed with `invalid peer certificate: UnknownIssuer` for `https://pypi.org/simple/alembic/`. Docker image pulls worked.
- **Where:** M0, dependency installation
- **Cause:** Norton Antivirus inspects HTTPS on the laptop. It re-signs each connection with its own root ("Norton Web/Mail Shield Root"), which Windows trusts and a Linux container does not. Image pulls go through Docker Desktop on the Windows side, which is why they were unaffected.
- **Fix:** The Dockerfile adds any certificate found in `docker/certs/` to the image's trust store and points uv, Python and Node at it. The Norton root, a public certificate exported from the Windows store, is placed there on this machine and ignored by git. Verification is never disabled.
- **Guard:** None automated; the build fails loudly if the certificate is missing on this machine. Model downloads in M4 will go through the same trust store.
- **Status:** verified. The alternative, excluding these sites from Norton's HTTPS scanning, is the owner's decision.

### E-001 — The specified GPU worker settings would never release VRAM

- **Date:** 2026-11-13
- **Seen:** Found in review, before any code. The input configured the GPU queue as `--pool=solo, concurrency=1, worker_max_tasks_per_child=1` and expected a fresh process per job.
- **Where:** kickoff review; SPEC §7.2 RUN; relates to BUG5 and BUG6
- **Cause:** The solo pool runs tasks inside the worker's own process. `max_tasks_per_child` only recycles prefork pool children, and Celery ignores it in other pools without warning. Every job would have shared one process, so weights from one could stay in VRAM for the next.
- **Fix:** The task became a supervisor that starts one child process per job (SPEC CL-01, [ADR-0003](decisions/ADR-0003-job-execution-model.md)).
- **Guard:** `test_BUG5_*` in M4
- **Status:** fixed in design; not verified until the guard test exists and passes
