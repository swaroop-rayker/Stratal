# ADR-0019 — Tabular and text models end to end: data, fitted preprocessing, classical ML, serving

**Status:** Accepted by the owner on 2026-11-19, as proposed ("Approve all three", M10), including rule 20 (scikit-learn and skops in the `gpu` extra too) and rule 24 (torchmetrics not added). Settles M10's open points 1–7, 9 and 14–17 (MILESTONES.md), as the owner chose at the kickoff (K5). Points 8 and 10 are settled in ADR-0017 rule 6; points 11–13 are requirement DATA-1.5 in SPEC, approved the same day.

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
17. **The model is one scikit-learn `Pipeline`** (ML-1.3): a `ColumnTransformer` (numeric: `SimpleImputer(median)` then `StandardScaler`; categorical: `SimpleImputer(constant)` then `OneHotEncoder(handle_unknown="ignore")`), then the estimator. It is fitted on the train split only; validation and test are only scored. Classical text models are not in M10's scope.
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
