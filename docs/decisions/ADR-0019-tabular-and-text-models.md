# ADR-0019 — Tabular and text models end to end: data, fitted preprocessing, classical ML, serving

**Status:** Accepted by the owner on 2026-11-19, as proposed ("Approve all three", M10), including rule 20 (scikit-learn and skops in the `gpu` extra too) and rule 24 (torchmetrics not added). Settles M10's open points 1–7, 9 and 14–17 (MILESTONES.md), as the owner chose at the kickoff (K5). Points 8 and 10 are settled in ADR-0017 rule 6; points 11–13 are requirement DATA-1.5 in SPEC, approved the same day. **Addendum B** (tabular transforms, DATA-7, M11b) accepted by the owner on 2026-11-24, as proposed.

## Context

- M10 serves DATA-1.4 (tabular and text ingestion), DATA-2.4 (text cleaning), ML-1.1 to ML-1.5 (classical ML), EVAL-1.2 (task metrics) and, with ADR-0018, HPO. REG-1.4 asks for `/predict` on every registered model, skops models included.
- The owner's kickoff decisions (2026-11-19): word-level tokens with a vocabulary from the train split, no tokenizer library (K3); scikit-learn, skops and Optuna approved for the worker images (K4); EVAL-1.2 on libraries in production, CIDEr-D in house (K2); one ADR for these points (K5).
- Decision 1 (M8 kickoff): scikit-learn's bundled datasets for tabular (breast cancer, diabetes, wine); the COCO captions in `sources/coco10k-meta`, labelled with their image's category, for text.
- What M9 left in place: the `tabular` and `labelled_text` schemas and validators (`stratal.data.dataset_types`: a `TableSchema` with exactly one `target` column; a text row has `text` and `label`), the tabular input block (`stratal.build.tabular`, ADR-0017 rule 6), and the builder's locked ends (ADR-0017 rule 5: `vector (F,)` for tables; `tokens (L,)` with a vocabulary size for text). Only `labelled_images` can be trained today, and only as classification.
- The `inference` service is built from the `worker-gpu` image, which has neither scikit-learn nor skops (ADR-0014 rule 11), and has a 1.0 GB memory limit (ADR-0016 rule 7). The containers' limits sum to 9 GB and have no room for another service.
- Manifests are Parquet with fixed nullable columns (DATA_MODEL §5); adding a nullable column changes no existing version ID (DATA-3.2), as M8's `label` showed.

## Decision

### A. Data in

1. **Tabular ingestion (DATA-1.4).** A `tabular` dataset's source folder holds one table, a `.csv` or a `.parquet` file (pyarrow, already a core dependency, reads both). Each record becomes one manifest row, keyed `<file>#<row number>`. The ingest config names the `target` column and may set any column's kind; the others are inferred (numeric if every non-empty value is a finite number, else categorical). The resulting `TableSchema` is stored in the manifest's file metadata (`stratal.table_schema`); it follows from the config and the data, so it is part of the version's identity. A record's values go in a new nullable column `record` (canonical JSON, keys sorted); the target goes in `label` for a categorical target and in a new nullable float column `target_value` for a numeric one.
2. **The target is in the dataset, not the request** (open point 6). It is named at ingest, kept in the schema, and its kind sets the **task**: a categorical target is classification, a numeric one regression. Changing the target makes a new version. A run request never names it.
3. **Text ingestion (DATA-1.4).** A `labelled_text` source holds `.jsonl` files (one `{"text", "label", "group"?}` per line) or `.txt` files in class folders (one text per line, labelled like images with `label_from: folder`). New nullable manifest columns: `text` and `text_group`.
4. **Text cleaning (DATA-2.4)**, a `clean` stage for text: Unicode NFKC, whitespace runs collapsed and trimmed; a row empty after that is rejected as `empty_text`, one over `max_chars` (default 10,000, the validator's limit) as `text_too_long`; exact duplicates after normalisation are marked `exact_duplicate` (DATA-2.1's rule, applied to the normalised text). Every removal keeps its row and reason, so it is logged and counted like an image's.
5. **Exact-duplicate records** in a table are marked `exact_duplicate` by its `clean` stage, since a record on both sides of a split is leakage.
6. **Splits.** Classification stratifies by `label`; regression by the target's quintile. A text row's `text_group` puts rows that must stay together in one split group (DATA-4.2): the five captions of one COCO image share it.
7. **The phase-2 datasets** are written to `sources/` by two scripts, with no download: `scripts/datasets/sklearn_tables.py` exports breast cancer, diabetes and wine from the installed scikit-learn as CSV files (run in `worker-cpu`); `scripts/datasets/coco_captions_text.py` writes `coco10k-captions/captions.jsonl` from `sources/coco10k-meta`, each caption labelled with its image's category covering the largest area, and grouped by its image.

### B. Text into tokens (open points 14–17)

8. **Words, with a vocabulary from the train split** (K3). Casefolded text is split by the pattern `\w+|[^\w\s]` (words and single punctuation marks). The vocabulary is the train split's tokens seen at least `min_freq` times (default 2), most frequent first, capped at `vocab_size` (default 20,000). IDs: **0 is padding** (as the built-in text templates assume, `padding_idx: 0`), **1 is unknown**, then the vocabulary in order of count, ties broken alphabetically, so the same data and settings always give the same IDs.
9. **Tokenizing is a pipeline stage, `tokenize`**, after `split`, on the CPU queue. It fits the vocabulary on the train rows only, writes it as a blob (JSON), and writes a new version whose rows gain `tokens` (a list of int32, truncated and padded to L) and `token_count` (the length before truncation). The vocabulary's hash, `vocab_size`, `min_freq` and L are the stage's config and output, so they are part of the version's identity (DATA-3.2). ADR-0017 rule 5 locks the builder's input from this version: `tokens (L,)` and the vocabulary's size.
10. **The maximum length L** is set in the stage's config; left empty, it is the train split's 95th-percentile length rounded up to a multiple of 8, at most 512. Longer texts are truncated at the end and shorter ones padded on the right. The stage reports the length distribution and the share truncated (SPEC's "Tokenizing corpus, vocab 12,480" activity), and SCR-03 shows it, so L can be chosen with the data in view.
11. **`/predict` tokenizes the same way**: the model version records its dataset version, which names the vocabulary blob and L.

A tokenizer fitted inside training and stored with the weights was the alternative. It would make L and the vocabulary size properties of a run, not of the data, and the builder could not lock them before the run (ADR-0017 rule 5); M12's corpora would each need their tokens built again per run.

### C. Networks on tables and text (open points 5, 7, 9)

12. **`train` accepts `tabular` and `labelled_text` split versions** (point 5), with one loader per type: a table's rows become the input block's vector and the target; a text's rows become `tokens` and the class index. Images keep their loader.
13. **A network's fitted preprocessing is stored with its weights** (point 7). For a table: per numeric column the train split's mean, standard deviation and median (missing values are imputed with the median, then standardised); per categorical column its categories in sorted order (missing and unseen values go to the unknown slot); for regression the target's mean and standard deviation. Fitted on the train split only (ML-1.3), written as `preprocess.json` beside the safetensors (no pickling, NFR-8), applied in the same code by training, evaluation and `/predict`. The figures are plain numbers, so they need no scikit-learn and run in any worker.
14. **Regression in the training runtime** (point 9). The task comes from the dataset (rule 2). For regression the locked head has width 1, the loss is MSE on the standardised target, and the live and epoch metrics are RMSE, MAE and R² on the original scale (EVAL-1.2). Predictions are returned on the original scale. Images and text stay classification.

### D. Classical models (ML-1.1 to ML-1.5)

15. **A classical model is a `train` job** whose `model` is `{"kind": "sklearn", "estimator": <name>, "params": {...}}`, on the CPU queue (`device: cpu` only). The estimators are ML-1.1's and ML-1.2's: `linear` (ordinary, `ridge` or `lasso` for regression), `logistic_regression`, `svm`, `decision_tree`, `random_forest`, `gradient_boosting` (scikit-learn's `HistGradientBoosting*`), `knn`, `naive_bayes`; `kmeans`, `dbscan`, `hdbscan`, `pca`. Each name maps to the classifier or the regressor by the dataset's task; the unsupervised four ignore the target.
16. **Parameters are Pydantic models** with ranges and defaults (in plain Python, in `stratal.ml.params`, so `api` validates them and SCR-09's form is generated from their schema, UI-2.3), plus **cross-field rules, each with its own test** (ML-1.4), refused before the job is queued with the field and a reason, e.g. logistic regression's `l1` penalty needs the `liblinear` or `saga` solver; `elasticnet` needs `saga` and an `l1_ratio`; `dual` only with `l2` and `liblinear`; DBSCAN's `eps > 0`; PCA's `n_components` at most the input width.
17. **The model is one scikit-learn `Pipeline`** (ML-1.3): a `ColumnTransformer` (numeric: `SimpleImputer(median)` then `StandardScaler`; categorical: `SimpleImputer(constant)` then `OneHotEncoder(handle_unknown="ignore")`), then the estimator. It is fitted on the train split only; validation and test are only scored. Classical text models are not in M10's scope. *Forward pointer (the owner's choice, 2026-11-19, DATA-7.3, M11b):* the categorical encoder then follows each column's encoding in the table schema (`ignore` drops the column, `ordinal` encodes in the given order, `embedding` is one-hot here); the details go in M11b's ADR.
18. **Saved with skops** (ML-1.5, NFR-8) as `model.skops`. Loading uses `skops.io.load` with an explicit allow-list of the pipeline, transformer and estimator types above; any other type in a file is refused as `weights_unreadable`, as a planted pickle is today. Nothing is unpickled.
19. **Lineages and the base.** A lineage is one canonical model spec on one dataset type, as for networks (ADR-0015 rule 1). A classical model has no initial weights, so its base is the unfitted pipeline, a skops file of a few KB, saved before fitting; this keeps INV-13 (one base per lineage) without a special case.

### E. Registry and serving (open points 1–4)

20. **The `inference` service serves skops models too** (point 1). scikit-learn and skops are added to the `gpu` extra as well, so the `worker-gpu` image, which `inference` is built from, has them (about 150 MB more on that image; the same packages as K4, so no new source). A classical model counts as the one loaded model (ADR-0016 rule 2), runs on the CPU and never takes the GPU lease; its response says `"device": "cpu"`. The service's memory is measured with a classical model loaded before the change is accepted; its 1.0 GB limit stays unless that measurement says otherwise.
21. **`/predict` takes the input of the version's dataset type** (point 2): `image_b64` or `blob` for images (unchanged), `record` for a table, `text` for text; any other is `422 wrong_input`. A `record` is checked against the version's `TableSchema`: every feature present (a value may be null and is then imputed), the target absent, no unknown fields, numbers finite. The response keeps today's fields and adds `task`:

    | Task | Result |
    |---|---|
    | classification | `top`: classes with probabilities (today's shape). An estimator without probabilities (an SVM without `probability`) returns its class with a probability of null |
    | regression | `value`, on the target's scale |
    | clustering | `cluster`: k-means only. DBSCAN and HDBSCAN cannot assign a new point, so their versions are refused as `not_servable`; their training clusters and metrics are on SCR-11 |
    | projection | `components`: PCA's projection of the record |

22. **The registry stores skops files beside safetensors** (point 3): `registry/<lineage>/<version>/` holds `model.skops` (classical) or `weights.safetensors` and `preprocess.json` (networks), always with `spec.json`. Retention, the delete protocol, the disk audit (REG-2.11) and the folder checks (INV-14) work on folders and bytes, so they apply unchanged; the disk report counts every file in the folder.
23. **Not in M10's scope** (point 4): batch prediction (a CSV in, predictions out), which no requirement asks for; serving DBSCAN and HDBSCAN assignments (rule 21).

### F. Task metrics (EVAL-1.2, K2)

24. **Libraries in production, CIDEr-D in house.** Classification, regression and clustering use `sklearn.metrics`; BLEU and chrF use sacrebleu (2.6.0, about 6 MB with lxml, PyPI; in both worker images); CIDEr-D is our own code (CL-13). Perplexity is the exponential of the mean negative log-likelihood the stub's contract already takes; the captioning CLIP score is the mean of LABEL-1.3's CLIP scores, from the scorer we have. **torchmetrics is therefore not added**: with scikit-learn in both worker images (rule 20), nothing left needs it. If the owner prefers it for a metric, it is added for that metric (1.0 MB plus lightning-utilities, PyPI).
25. `stratal.eval.task_metrics` keeps its stub's contracts; `metric_set(task)` stays importable without the libraries, so `api` can list a task's metrics.

## As built (part A and B, 2026-11-19)

- **Stage names (rules 4 and 5, Consequences).** The clean stages for records are `clean_table` and `clean_text`, registered beside the image `clean`, rather than `clean` itself. Each has its own config; giving the image stage text settings would have changed its config's hash, and so the IDs of image versions cleaned from now on. Pipelines: `tabular` runs `ingest → clean_table → split`, `labelled_text` `ingest → clean_text → split → tokenize`.
- **`tokens` is a string** of space-separated IDs, not a list of int32 (rule 9): the version hash (DATA-3.2) takes scalar values only, and changing it would touch the identity code.
- **`sha256` holds the row's content hash**: the canonical record, or the text (normalised after `clean_text`). Exact duplicates are found by the same unit as for images. The source table itself is not copied into the blob store: the manifest holds every value.
- **The split** keeps its stage and config for records, with `stratum_keys: []` and `leakage_distance: 0` as the types' defaults, and takes its own path inside: groups by `text_group`, strata by class or target quintile (scikit-learn's diabetes: q1 to q5), the owner's `assign_splits`, and a leakage check by group and content hash in linear time. Continuing an earlier split (DATA-4.5) works as for images, by content hash.
- **A records clean stage's WARN carries a suggested action** (MON-1.3); `ListSink`, the test sink, now refuses a WARN without one, as the runtime does.
- **Measured on the live stack:** breast cancer 569 rows (455 / 57 / 57), diabetes 442, wine 178 (`job_0059` to `job_0061`); the COCO captions 49,585 of 9,912 images in 78 classes, 575 repeated captions marked, vocabulary 5,243, L = 16 with 3.9 % of train texts cut (`job_0062`).

## As built (part C, 2026-11-19)

- **One train stage for every input.** Images, tables and token sequences are each a dataset object with a builder `Shape` (`image`, `vector`, `tokens`); the loop, checkpoints, pause, resume and device choice are shared. Tables and token IDs are held in memory, with no loader workers. Texts train on the `tokenize` version, which carries the tokens and the vocabulary; tables on the `split` version.
- **`stratal.train.table_prep`** (plain Python) is the fitted encoding of rule 13, used by the builder's locked width, by training and, in step 5, by `/predict`: one function, so they cannot disagree. Every categorical column is one-hot for now. ADR-0017 rule 6's `Embedding` option per column is not built yet: with DATA-7.3 (M11b) the table schema will carry each column's encoding and the input block may override it (the owner's choice, 2026-11-19), and the option will come with that.
- **Every network run ends with EVAL-1.2's scores on validation and test** (images included), as `val_`/`test_` metrics and the version's `metrics.detail`. Until now the image runs reported only validation accuracy and loss.
- **Regression** reports RMSE on the target's own scale per epoch (`train_rmse`, `val_rmse`) where classification reports accuracy.
- **Measured live:** the Tabular MLP template on wine, test accuracy 1.00 (`job_0065`); on diabetes (regression), test R² 0.56, RMSE 54 (`job_0066`); the LSTM text template on the COCO captions on the GPU, 5 epochs in 25 s, test accuracy 0.68 and macro F1 0.31 over 78 imbalanced classes (`job_0067`).

## As built (part E, 2026-11-20)

- **One holder, three kinds of model.** The version's `model.json` says what it takes: a skops pipeline or a network with a `vector` input takes a `record`, a `tokens` input a `text`, anything else an image. A classical model loads with `stratal.ml.persist` under the lineage's lock, counts as the one loaded model and never touches the GPU lease. A table network loads its `preprocess.json` and a text network its dataset version's vocabulary, so `/predict` encodes with the very code training used (`stratal.train.table_prep`, `stratal.data.tokenize`); a text is normalised as `clean_text` does first.
- **New error codes:** `wrong_input` (422) and `not_servable` (409) (ADR-0011: added, never renamed). A table network's `model.json` now carries its `table_schema`, so a record is checked the same way for both kinds of table model.
- **`Prediction`** gains `task`, `value`, `cluster` and `components`; `top` is null except for classification, and a class's `probability` may be null.
- **Memory (rule 20's measurement, 2026-11-20).** From a fresh start, the service with the breast-cancer random forest loaded peaks at 481 MiB (cgroup `memory.peak`), against 397 MiB serving `small_cnn` on the CPU in M8. After it had also loaded networks on the GPU, the peak was 870 MiB of the 1,000 MiB limit: the CUDA context and PyTorch's GPU libraries live in process memory, and GPU inference is on since 2026-11-19 (RUN-1.4). The limit is kept, as rule 20 says, but the headroom is 13 %. Raising it to 1.25 GB, taken from `worker-gpu`'s 3.5 GB (measured peak 1.6 GB, ADR-0016), is the owner's call.
- **Older versions.** A table network trained before its `model.json` carried `table_schema` is checked against its dataset version's schema instead.
- **Measured live:** the breast-cancer random forest (skops, CPU) predicted a held-out malignant record as malignant (0.96); the wine MLP (GPU) its record's class; the diabetes network a value on the target's scale; the captions LSTM "A man riding a wave on top of a surfboard." as person (0.92), then surfboard.

## As built (part D, 2026-11-19)

- **The request.** A classical run is `POST /jobs` with `kind: train` and `classical: {model: {kind: "sklearn", estimator, params}, seed}` beside `config`, exactly one of them (rule 15's `model` is inside `classical`). A union on `config` would have changed every network request's error locations, which SCR-09 maps onto its fields. The job's one stage is `train_classical`, on the CPU queue.
- **The lineage key is the estimator alone** (rule 19): `{kind: "sklearn", estimator}`, so a study's trials, re-runs and differently tuned runs share one lineage and its base.
- **Data to scikit-learn as PyArrow tables** with named columns. scikit-learn 1.9 takes them, and pandas, which is not installed, is not needed.
- **scikit-learn's current API.** 1.8 deprecated `LogisticRegression(penalty=…)`: our `penalty` field (the one ML-1.4's example rule names) is translated to `l1_ratio` and `C`. 1.9 deprecated `SVC(probability=True)`: the SVM has no probability option, and `/predict` returns its class with a null probability, as rule 21 already allows. The library tests run with deprecation warnings as errors.
- **The allow-list is enforced from the file's own schema** (rule 18). skops trusts scikit-learn's estimators by default, so its own list of untrusted types would not show an estimator Stratal never builds. `stratal.ml.persist` reads every `module.Class` from the archive's `schema.json` and refuses the file if one is not on `TRUSTED`; the list was taken from the types our fourteen estimators actually write.
- **Tests that need the libraries** carry the marker `ml` and run in `worker-cpu` (check.ps1 steps "ml" and "integration (ml)"); that image has no `httpx`, so the run test creates jobs through the job service, not the API's test client.

## Alternatives

| Option | Why not |
|---|---|
| A separate CPU inference container for skops models | About 400 MB more of the 9 GB budget, which has no room (ADR-0007), for models of a few MB |
| Serve skops models from `worker-cpu` through a Celery task per request | A process start and model load per request (rejected for networks in ADR-0016) |
| Serve them from `api` | Puts scikit-learn in the API image, which keeps no ML libraries |
| Tokenize inside training, stored with the weights | L and the vocabulary would belong to a run; the builder could not lock them first (rule 11's note) |
| Subword tokens (BPE), from scratch or from `tokenizers` | The owner chose words (K3); COCO captions are short with a small vocabulary |
| scikit-learn transformers for networks' preprocessing too | Would need scikit-learn and skops wherever a network is evaluated or served; the input block's statistics are a few numbers per column |
| The target named in each run request | The same version could then mean two tasks; the schema already has a `target` role |
| The table as one Parquet column per feature | Every table would change the manifest's schema; one canonical-JSON column keeps the manifest's columns fixed |
| torchmetrics for classification and regression | Redundant with sklearn.metrics once scikit-learn is in both worker images |

## Consequences

- **Dependencies:** scikit-learn and skops in the `gpu` extra as well as `ml-cpu` (rule 20, needs the owner's OK: about 150 MB more on `worker-gpu` and `inference`, no new source); sacrebleu in both (K2). torchmetrics not added.
- **Manifest columns** (nullable, so no version ID changes): `record`, `target_value`, `text`, `text_group`, `tokens`, `token_count`. **Reason codes:** `empty_text`, `text_too_long` (and DATA-1.5's, if approved).
- **New stage** `tokenize`; text `clean`; tabular `clean`. Pipelines per type: `tabular` runs `ingest → clean → split`; `labelled_text` runs `ingest → clean → split → tokenize`.
- **API:** `POST /jobs` with kind `train` accepts `tabular` and `labelled_text` versions and `sklearn` models; `GET /ml/estimators/schema` for SCR-09's forms; `/predict` gains `record` and `text`. `GET /dataset-types` reports `tabular` and `labelled_text` as trainable, with their task.
- **New code:** `stratal.data` (table and text ingest, text clean, tokenize), `stratal.ml` (params in plain Python; pipelines and skops in the workers), the type-specific loaders in `stratal.train`, and the inference service's table and text paths.
- ARCHITECTURE, DATA_MODEL §5, API.md and ADR-0016 (a pointer to rule 20) are updated when this is built.


## Addendum B — Tabular transforms (DATA-7.1 to DATA-7.5, M11b)

**Status:** Accepted by the owner on 2026-11-24, as proposed ("approved as proposed"). It follows the owner's M11b kickoff decisions K1 to K9 (MILESTONES.md, M11b, "Accept all nine"): this addendum rather than a new ADR (K1).

### Context

- **DATA-7.1** an ordered list of row-local operations on a tabular raw version, in a `transform` stage between `ingest` and `clean`, giving a new version whose parent is the raw one; the raw manifest unchanged; the same operations give the same ID; operations fitted on the data refused. **DATA-7.2** drop, rename, reorder, change a column's kind, choose the target; each refused before the run when invalid, naming the column; a value that will not convert is rejected as `cast_failed`, kept and counted. **DATA-7.3** each feature's encoding (`one_hot`, `embedding`, `ordinal`, `ignore`) in the table schema, used by the input block and the classical pipelines. **DATA-7.4** (P2) derived columns and row filters in a restricted expression language, parsed, never evaluated as code. **DATA-7.5** any tabular version as a table of columns, and a preview of the operations on sample rows that equals the stage's output.
- **What exists (rules 1, 5, 13 and 17).** Ingest coerces every value to its column's kind and stores a record's features as canonical JSON in `record`, its target in `label` (categorical) or `target_value` (numeric); a row whose target is empty is rejected as `missing_target` but keeps its features. The `TableSchema` (columns with `kind` and `role`) is the manifest's `stratal.table_schema` metadata. A network's input block takes each categorical column one-hot by default or through an `Embedding` (ADR-0017 rule 6); a classical pipeline one-hot encodes every categorical column. Ingest reads a table whole (rule 1: "tables here are small").
- **K2 to K9:** transforms apply to a raw version only; re-admitting rows whose old target was empty (K3); kinds changed on already-coerced values (K4); `set_encoding` and the schema's `encoding` (K5); the expression language through `ast.parse` and a whitelist (K6); no owner unit (K7); SCR-15 and SCR-03's *Columns* tab, the preview computed in the API by the stage's own functions (K8); streaming ingest and transform (K9).

### Decision

25. **An optional `transform` stage for tables.** A `tabular` pipeline runs `ingest → transform → clean_table → split` when the request gives `stages.transform.operations`, and `ingest → clean_table → split` otherwise, so every existing pipeline and version ID is unchanged. It runs on the CPU queue, memoized like every stage, so the same input and operations reuse the version (DATA-7.1.AC1). Its input must be an `ingest` version of a `tabular` dataset; anything else, a transform's output included, is refused with `validation_error` (K2). To change the operations, the list is edited and run again on the raw version.
26. **Operations** are a list of objects, each with an `op` (a discriminated union, so the form is generated from its schema, UI-2.3), applied in order:

    | `op` | Fields | Effect |
    |---|---|---|
    | `drop` | `columns` | Removes the columns; the target cannot be dropped |
    | `rename` | `from`, `to` | Renames a column; `to` must be new |
    | `reorder` | `columns` | The full order; every column once |
    | `set_kind` | `column`, `kind` | `numeric` ↔ `categorical` (rule 27) |
    | `set_target` | `column` | The old target becomes a feature |
    | `set_encoding` | `column`, `encoding`, `order` | Rule 29 |
    | `derive` (P2) | `name`, `expression`, `kind` | A new column from the row's columns (rule 30) |
    | `filter` (P2) | `expression` | Keeps the rows where it is true (rule 30) |

    - **Checked before the run** against the input's schema, operation by operation: an unknown column, dropping or duplicating the target, a rename onto an existing name, an incomplete `reorder`, an encoding the column's kind does not allow, an expression outside the language. Each is a `validation_error` whose `errors[].loc` is `["stages", "transform", "operations", i, field]` and whose message names the column (DATA-7.2.AC1).
    - **Fitted operations are refused by name** (DATA-7.1.AC2): `scale`, `standardize`, `normalize`, `impute`, `fill_mean`, `fill_median`, `target_encode` and `bin` get "is fitted on the data, so it belongs in the model's preprocessing, fitted on the train split (ML-1.3)", not a generic "unknown operation".
27. **Rows.** The stage rebuilds each row's values from `record` and the old target (`label` or `target_value`), applies the operations, and writes `record` (the new features, canonical JSON), `label` or `target_value` (the new target) and, recomputed, `sha256` and `size_bytes`, since `clean_table`'s exact duplicates compare `sha256` (open point 2's note).
    - `set_kind` to numeric parses the text as a number; a value that will not parse is rejected as **`cast_failed`**, kept in the manifest and counted (DATA-7.2.AC1). To categorical, a number becomes its canonical text (`7.0` → `"7"`): ingest's coercion is not undone (K4; to keep `007`, set the kind at ingest with `stages.ingest.table.kinds`).
    - **A row rejected at ingest only as `missing_target`** is taken back when the new target has a value; a row whose new target is empty is rejected as `missing_target` (K3). Rows rejected for other reasons keep their status and reason (INV-06).
    - A `filter` marks the rows it drops `filtered` with **`transform_filter`**; an expression that cannot be computed for a row (a missing value, a division by zero, the logarithm of a non-positive number) gives null: a derived value is then missing, and a filter drops the row, counted apart as `transform_null` in the stage's counts.
28. **The schema changes with the operations** and is stored as the output's `stratal.table_schema`, so it is part of the version's identity (DATA-3.2). The stage reports per operation what it changed: columns dropped, renamed, recast with the number of `cast_failed`, rows re-admitted, rows filtered.
29. **Encodings (DATA-7.3, K5).** A schema column gains `encoding`: for a categorical column `one_hot` (the default), `embedding` or `ordinal` with its `order` (every category in order; a value outside it, or missing, is treated as unknown); for any feature `ignore`. A default is not written into the schema's JSON, so existing versions' schemas, and their IDs, are unchanged.
    - **The network's input block** takes the schema's encoding as its default: `ignore` leaves the column out; `ordinal` becomes one numeric feature, the category's position, standardised on the train split like any number (unknown → the median position); `one_hot` and `embedding` as today, the switch between them still offered on SCR-08 and SCR-09 (decided 2026-11-19).
    - **The classical pipeline** drops `ignore` columns, encodes `ordinal` with an `OrdinalEncoder` in the given order (unknown → −1, then imputed), and one-hot encodes `one_hot` and `embedding` columns, as rule 17's pointer said.
    - **`/predict`** accepts a record without its `ignore` columns.
30. **The expression language (DATA-7.4, K6)** is Python's expression syntax, parsed with `ast.parse(text, mode="eval")` and accepted only if every node is on a whitelist; our own walker then computes it on a row. Nothing is ever passed to `eval`, `exec` or `compile`.
    - **Allowed:** numbers, strings, `True`, `False`; column names (a column whose name is not an identifier is written `col("name")`); `+ - * /` and unary minus on numbers; `== != < <= > >=`; `and`, `or`, `not`; `isnull(x)`, `notnull(x)`, `abs`, `log`, `sqrt`, `round(x, n)`, `min`, `max`.
    - **Refused, naming the construct:** attribute access, subscripts, calls to anything else, comprehensions, lambdas, assignments, names that are not columns. At most 500 characters and 100 nodes.
    - Arithmetic needs numeric columns; comparing a categorical column is with strings. A derived column's kind is given or inferred (numeric for arithmetic, categorical for a string).
31. **Streaming (NFR-2, K9).** Ingest and the transform read a table in record batches of 65,536 rows and write the manifest as they go. A Parquet file keeps its own column types; a CSV's kinds are inferred in a first pass that keeps, per column, only whether every value parses as a number. The memory of ingesting and transforming `sources/household-power/power.parquet` (2,075,259 rows) is measured from `worker-cpu`'s cgroup (`memory.peak`) and recorded; the target is under half its 2.5 GB limit.
32. **Preview and columns (DATA-7.5, K8).** The operations and the row step are a pure module, `stratal.data.transform`, in plain Python, which the stage and the API share.
    - `POST /versions/{id}/transform/preview` with `{operations, limit ≤ 50}` checks the operations (rule 26) and returns the resulting schema and the first `limit` rows after the operations, with their status and reason. It equals the first rows of the version the stage then produces (DATA-7.5.AC1, tested).
    - `GET /versions/{id}/columns` returns, for any tabular version, each column's kind, role, encoding, missing count, distinct count and distribution (a histogram on numbers, the top categories otherwise), from the manifest, cached by version ID like the version's profile (M11, K10).
33. **Screens (K8).** **SCR-15**, the transform editor at `/versions/{id}/transform` for a raw tabular version, and a ***Columns* tab on SCR-03** for any tabular version, each through Gate 1 and Gate 2 before being built.

### Tests

- `test_DATA_7_1_*`: the raw manifest is byte-identical after a transform; the same operations give the same version ID (a cache hit); one changed operation gives a new one; a transform of a transform is refused; `scale` and `impute` refused with the pointer to the model's preprocessing.
- `test_DATA_7_2_*`: one test per operation; each invalid case of rule 26 refused before the run, naming the column, with its `loc`; `cast_failed` rows kept and counted; a new target re-admits a row rejected only for the old one; `sha256` recomputed, so `clean_table` finds a duplicate the transform created.
- `test_DATA_7_3_*`: an `ignore` column is absent from the input block and the classical pipeline; an `embedding` choice reaches the input block; `ordinal` keeps the order given, in both.
- `test_DATA_7_4_*` (P2): derived values match the formula on sample rows; a filter keeps exactly the matching rows and marks the rest `transform_filter`; each refused construct of rule 30 refused, and no expression reaches `eval` (a scan of the module, as NFR-8's no-unpickling scan does).
- `test_DATA_7_5_*`: the column summary matches the manifest; the preview equals the stage's first rows.
- `power.parquet` ingested and transformed with its peak memory recorded (rule 31).

### Alternatives

| Option | Why not |
|---|---|
| Edit a version in place | Versions are immutable (ADR-0004); DATA-7.1 asks for a new version with the raw one as parent |
| Store each value's raw text beside the coerced one | Changes every tabular version's identity, for the rare `007` case; setting the kind at ingest already covers it (K4) |
| A library for expressions (e.g. `simpleeval`, `numexpr`) | A new dependency for a few operators; the whitelist over `ast` is small and testable (K6) |
| `pandas.eval` or `DataFrame.query` | Evaluates code paths we do not control, and pandas is not in the CPU image |
| Fitted operations in the transform | Fitting on all rows leaks validation and test into training (ML-1.3); they stay in the model's preprocessing |

### Consequences

- **Manifest and schema:** the schema's columns gain `encoding` and `order` (written only when not the default); new reason codes `cast_failed`, `transform_filter`; the new stage `transform` (DATA_MODEL.md, ARCHITECTURE.md §3).
- **API:** `stages.transform` in `POST /jobs`; `POST /versions/{id}/transform/preview`; `GET /versions/{id}/columns` (API.md).
- **The input block, the classical pipeline, `table_prep` and `/predict`** read the schema's encodings (rule 29), so models trained on transformed versions honour them; versions without encodings behave exactly as before.
- **Ingest streams** (rule 31), so a large table no longer has to fit in memory; the ingest config and its version IDs are unchanged.
- **Screens:** SCR-15 and SCR-03's *Columns* tab, through G1 and G2 (SCREENS.md v4).

### As built (M11b, 2026-11-25)

- **Rule 29's network embedding, corrected (the owner's decision, 2026-11-25, "Yes, as proposed").** Rule 29 said a network takes `one_hot` and `embedding` "as today", with the switch "still offered on SCR-08 and SCR-09". Neither existed: a table reached a network as a vector already encoded by `table_prep`, every category one-hot, and the graph spec had no per-column input block (M10's *As built*, part C). Built in M11b: the schema's `embedding` reaches the network. `table_prep` hands an embedding column over as its category's index (the train split's categories sorted; unknown and missing take one more slot); a small input layer in front of the compiled network looks each index up in an `Embedding` of `min(50, ⌈n / 2⌉)` features and joins the result to the other columns. The builder's locked input width and parameter count follow ADR-0017 rule 6's input block. The layer is added only when a version has embedding columns, so the weights of every existing model load unchanged. **Not built:** a per-model switch on SCR-08 between `one_hot` and `embedding`; it has no approved design and is left for later. The schema's encoding is the only one.
- **The schema in the identity (rule 28), corrected (the owner's choice, 2026-11-25, "Schema in config").** Rule 28 said the stored schema is part of the version's identity; it was not: an ID hashes the rows and the config (DATA-3.2, `stratal.data.versioning`), and manifest metadata is left out. A transform that changes only the schema (`set_encoding`, `reorder`) leaves every row as it was, so the `clean_table` and `split` after it got the IDs of those made straight from raw, with another parent, and registration failed (found by `test_M10_networks` run after `test_M11b_encodings`). Now `clean_table` and `split` record their input's schema hash in their config (`input_schema`, read-only, filled by the stage, refused if a request sets another value), so the schema reaches the identity through the config. `versioning.py` is unchanged. Tabular versions made before M11b keep their IDs; running their pipeline again makes new `clean_table` and `split` versions once, since the config now has the field. Tested by `test_DATA_7_1_a_schema_only_transform_gives_its_own_downstream_versions`.
- **Where refusals sit (rule 26).** Our own checks give `loc` = `["stages", "transform", "operations", i, field]`. Two kinds come from the config model before them: a fitted operation's refusal has `loc` ending at the operation's index, and a structural error (a missing field) carries the operation's tag before the field (`[…, "operations", 1, "rename", "to"]`). SCR-15 maps both to the operation's card.
