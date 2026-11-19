# ADR-0018 — HPO execution: studies, trials, pruning and the single test evaluation

**Status:** Proposed, 2026-11-19 (M10). Spike S-09 checks the numbers it assumes before anything it covers is built.

## Context

- HPO-1.1 to HPO-1.5 (phase 2). SPEC §7.2: Optuna with TPE and the median pruner; optuna-dashboard optional (not planned: SCR-10 shows the study).
- Decision 7 (SPEC OC-10, 2026-11-17): a classical-ML study runs its trials inside one job; each deep-learning trial is its own job.
- ADR-0003: one child process per stage; Postgres is the truth for job state; the watchdog recovers lost jobs from heartbeats. ADR-0006: one GPU job at a time. `worker-cpu` runs two stages at once (`-c 2`) within 2.5 GB.
- DATA_MODEL §9 planned Optuna's own tables in a separate schema, and the job kinds `hpo_study` and `hpo_trial`.
- `api` has no ML libraries, so it cannot import Optuna. SCR-10 (SCREENS.md) needs the trials, the best configuration, the optimisation history, parameter importance and the test evaluation.
- The owner's kickoff decisions K4: Optuna in both worker images, scikit-learn only in `worker-cpu` (ADR-0019 proposes adding it to `worker-gpu` for serving).
- Every trial trains a model. Registering each one would put up to a hundred versions, and for architecture searches a hundred lineages each with a base kept forever (REG-2.1), into the registry.

## Decision

### 1. A study is a job of kind `hpo_study`

`POST /jobs` with kind `hpo_study` takes:

- **`base`**: a complete run request, exactly what a `train` job takes (dataset version, model, train config, device), for a network or a classical model (ADR-0019 rule 1). It must be valid on its own.
- **`space`**: the tunable markers (rule 2).
- **`objective`**: a validation metric of the dataset type's task, with its direction implied (`val_loss` and `val_rmse` minimise; accuracy, macro F1 and R² maximise).
- **`limits`**: `n_trials` (1–200, default 20) and `timeout_s` (60 s to 24 h, default 1 h). The study stops at whichever comes first (HPO-1.2).
- **`pruning`**: on by default (rule 5); `cv_folds` for classical models (rule 4, default 5); `seed` (default the base's).

A row in a new table, `studies`, holds what SCR-10 reads; trials are rows in `study_trials` (rule 3). Study IDs are `st_` and 12 hex characters.

### 2. Tunable markers point into the request (HPO-1.1)

A marker is `{path, type, low, high, step?, choices?}`. `path` is a JSON pointer into `base`, for example `/train/learning_rate`, `/model/nodes/c1/params/out_channels` or `/model/params/max_depth`. Types: `int`, `float`, `log_float`, `categorical`.

The API checks the space in plain Python (`stratal.hpo.space`, no Optuna) against the field's own JSON schema (ADR-0017 rule 2, ADR-0019 rule 2): the path must exist; the type must suit the field; `low` and `high` must lie inside the field's own range and `low < high`; `log_float` needs `low > 0`; every choice must be a valid value. A bad space is a `422 validation_error` naming the marker.

Cross-field rules (BUILD-1.6, ML-1.4) depend on the sampled values together, so they are checked per trial: before a trial trains, its sampled request is validated by the same models as a normal run, and its memory estimate compared with the GPU cap (ADR-0017 rule 7). A sampled request that fails becomes a **failed trial** with the code `invalid_combination` or `over_vram`, logged and counted against `n_trials`, and never trained. The API also checks each marker's two ends against the fixed fields, so a marker none of whose values could ever be valid (say, tuning logistic regression's penalty between `l1` choices while the solver is fixed to `lbfgs`) is refused at the start.

### 3. Optuna's storage in its own schema; our tables are what the API reads

- **Optuna** uses `RDBStorage` on the same PostgreSQL, in a schema `optuna`, through its own connection URL with `search_path=optuna`. Our migration creates the empty schema; Optuna creates and upgrades its own tables, including its own `alembic_version`, inside it. Our Alembic does not look at that schema (autogenerate covers `public` only), so the two never touch. Optuna's tables are the sampler's memory: TPE reads every finished trial from them.
- **`studies` and `study_trials`** are ours, written by the workers as each trial starts and ends. `study_trials` holds the trial number, its job (deep learning only), parameters, state (`running`, `complete`, `pruned`, `failed`), value, the step it was pruned at and why (`median` or `budget`), the intermediate values per step, duration and error. The API serves them from there; it never reads Optuna's tables, whose layout changes between Optuna versions.
- Each trial's start and end are also **events** on the study job, so they appear in its activity log and history (ADR-0005).

### 4. Classical-ML studies: one job, trials in one process (decision 7)

- The study job has one stage, `study`, on the CPU queue. Its child process loads the train split once, creates or reopens the Optuna study and calls `optimize` until a limit is reached.
- **Each trial is cross-validated on the train split** (HPO-1.4): `StratifiedGroupKFold` for classification and `GroupKFold` for regression, with `cv_folds` folds, grouped by the split's leakage group (DATA-4), so near-duplicates never sit on both sides of a fold. Each fold fits the whole pipeline on its training part (ML-1.3). The trial's value is the folds' mean; each fold's score is reported as an intermediate value at step = fold number, which is what the pruner reads.
- The validation split is not used for tuning. When the study ends, the best configuration is fitted on the train split, scored on validation (reported beside the CV value, as a single run would be) and registered (rule 7). The train-only fit keeps ML-1.3's acceptance criterion literal: the registered scaler's statistics come from the train split.
- **Pause** is cooperative between folds. Finished trials are already in Optuna's storage, so a resume reopens the study and continues with what is left of both limits. A trial that was running at a crash or pause is marked failed with the code `interrupted` and does not count against `n_trials`.

### 5. Deep-learning studies: one job per trial, advanced in short steps (decision 7)

- **Each trial is a job of kind `hpo_trial`:** a `train` run with its sampled request, its study and its trial number. It runs exactly like a `train` job (ADR-0014: device choice, checkpoints, pause, resume, thermal pause, out-of-memory handling) on the GPU queue, one at a time (ADR-0006). After every epoch it reports the validation objective to Optuna as an intermediate value at step = epoch, and asks whether to prune.
- **No process waits for the trials.** The study job's `study` stage runs in short steps, each a child process on the CPU queue: *tell* Optuna the finished trial's result, check the limits, *ask* for the next configuration, validate it (rule 2) and create its trial job. The trial's supervisor enqueues the next step when the trial ends, keyed by (study, trial number), so a duplicate delivery does nothing. Between steps the stage is `waiting` (a new stage status) and the job stays `running`.
- **The watchdog checks a waiting study through its trial**, not its heartbeat: if the current trial job is still queued, running or paused, the study is healthy; if it ended and no step is queued (a lost message), the watchdog enqueues the step itself.
- **Pause and cancel** on the study act on its current trial (a checkpoint, then `paused`; or cancelled) and stop the steps. Resume resumes the trial, and the study continues when it ends. Cancelling a study registers nothing and evaluates nothing.
- Trials are sequential: the card runs one job at a time, and TPE, given every previous result before each suggestion, is at its best that way.

A long-lived coordinator process would hold one of `worker-cpu`'s two slots for the whole study, hours for a deep-learning one, and block pipeline stages and retention behind it.

### 6. Pruning and the time budget (HPO-1.2, HPO-1.3)

- **Median pruner** (`MedianPruner`): a trial stops when its intermediate value at a step is worse than the median of earlier trials at the same step. Not before 5 trials have finished (`n_startup_trials`), and not before step 2 for networks (two epochs) or step 1 for classical models (one fold, `n_warmup_steps`). A pruned trial keeps its last intermediate value; `study_trials` and an event record the step it stopped at and the reason `median` (HPO-1.3.AC1).
- **The time budget** is wall-clock time spent running, excluding time paused. When it runs out, no new trial starts, and a running trial stops at its next step (epoch or fold), recorded as pruned with the reason `budget`. `n_trials` counts trials started, failed ones included; `interrupted` ones are not counted.
- **TPE** (`TPESampler`) is seeded with the study's seed, so the same space, seed and results suggest the same configurations.

### 7. Only the best trial is registered; trial weights are temporary

- A deep-learning trial writes its initial and final weights to its own job folder, not to the registry. When a trial becomes the best so far, the previous best's folder is deleted; when it does not, its own is. Each deletion is an event with the bytes freed. At most two trials' weights are on disk at once, plus the running trial's checkpoint (NFR-14's bound, per study).
- When the study ends, the best trial is registered as an ordinary version of its lineage (ADR-0015: its base from its initial weights if the lineage is new, then its trained weights), with a new nullable column `model_versions.study_id`. The other trials are visible in the study, not in the registry.
- For a classical study the refit of rule 4 is what is registered.

### 8. The test split is used once, on the best configuration (HPO-1.4)

- **Trials never see the test split.** A trial's loader is built from the train and validation partitions only, and a test of that boundary is part of HPO-1.4's suite.
- When the study ends (a limit reached, at least one trial complete), the study state becomes `closed`: no further trial can be asked for. A last stage, `test_eval`, on the queue that suits the model (the GPU for a network), loads the registered best version and computes the task's test metrics (EVAL-1.2) once.
- **Once is enforced by the database:** `studies.test_metrics` is written only by `test_eval`, guarded by a conditional update on a null `test_evaluated_at` and the job's `attempt` (ADR-0003's fencing token). A `test_eval` that crashes before writing is retried; it uses the same model and data, so its result is the same, and the retry count is recorded with it. No path recomputes test metrics for a study that has them (HPO-1.4.AC1).
- The test metrics are also the registered version's test metrics, shown on SCR-11.

### 9. Results (HPO-1.5)

- **Trial table and optimisation history** come from `study_trials`, live, through `GET /studies/{id}` and `GET /studies/{id}/trials`, and over the study job's event stream.
- **Best configuration:** Optuna's best trial, copied to `studies` after every trial.
- **Parameter importance** is computed once, when the study closes, by Optuna's default fANOVA evaluator, which needs scikit-learn; for a deep-learning study this runs in the last `study` step on `worker-cpu`. It needs at least two complete trials and at least two tuned parameters; otherwise SCR-10 says why there is none. Stored in `studies.importance`.
- **Start a run from the best configuration** returns `base` with the best parameters filled in, for SCR-09 to prefill.

## Spike S-09 (before building)

On the running stack, with Optuna installed in a scratch container:

1. Optuna's `RDBStorage` in the `optuna` schema next to our Alembic: its tables and `alembic_version` stay in that schema, `alembic upgrade head` and autogenerate ignore them, and `optuna storage upgrade` works.
2. Ask and tell on PostgreSQL: milliseconds per call, and rows and bytes per trial with 10 parameters and 50 intermediate values.
3. A classical trial (a random forest with 5-fold CV on breast cancer) inside one process, against the same trial as its own job: process start, imports and data load per job, to confirm decision 7 with a number.
4. A deep-learning trial as a job: the time from the step that creates it to its first training step, and from its end to the next trial's start (rule 5's steps), against an epoch of the reference CNN.
5. fANOVA's time on 20 and 200 trials.

The report goes in `docs/spikes/S-09-hpo.md`. If a number contradicts this ADR, the ADR is revised before building.

## Alternatives

| Option | Why not |
|---|---|
| Each classical trial its own job | A process start, imports and a data load per sub-second trial; decision 7 chose otherwise |
| All deep-learning trials inside one job | One process for hours: a crash, pause or out-of-memory loses the study's place, and ADR-0003's per-job release of VRAM no longer holds between trials. Decision 7 chose otherwise |
| A long-lived coordinator process waiting for trial jobs | Holds one of two CPU slots for the whole study (rule 5) |
| The API reads Optuna's tables directly | Couples the API to Optuna's internal layout, which changes between versions; puts Optuna in `api` if done through its client |
| Optuna's tables in the `public` schema | Its migrations and ours would share `alembic_version` and autogenerate would see its tables |
| A separate SQLite or journal file for Optuna | A second store to back up and keep consistent; PostgreSQL is already the system of record (ADR-0002) |
| Register every trial | Up to `n_trials` versions per study, and a lineage with a permanent base per architecture tried (REG-2.1) |
| Tune classical models on the validation split | HPO-1.4 asks for cross-validation for classical ML; a single holdout is noisier on small tabular data |
| Cross-validate on train ∪ validation and refit on both | More data for the final fit, but the registered scaler's statistics would no longer come from the train split alone (ML-1.3.AC1), and the validation score would no longer be a held-out figure |
| optuna-dashboard | Another web service and port; SCR-10 shows the same things in the design system |

## Consequences

- **New job kinds** `hpo_study` and `hpo_trial`, and a stage status `waiting`, with a migration for the check constraints. The watchdog gains rule 5's check.
- **New tables** `studies` and `study_trials`, a column `model_versions.study_id`, and the empty schema `optuna`.
- **New endpoints:** `GET /studies`, `GET /studies/{id}`, `GET /studies/{id}/trials`, `GET /studies/{id}/best-config`, and `POST /jobs` with kind `hpo_study`. Pause, resume and cancel are the job's.
- **New code:** `stratal.hpo.space` (plain Python, in `api` and the workers) and `stratal.hpo.study`, `stratal.hpo.trial` (Optuna, workers only).
- **Disk:** a study never holds more than two trials' weights and one checkpoint; the rest is deleted as it runs and logged.
- **Screens:** SCR-09's study part and SCR-10, through G2 first.
- A deep-learning study waits between trials for a CPU step (seconds, measured by S-09).
