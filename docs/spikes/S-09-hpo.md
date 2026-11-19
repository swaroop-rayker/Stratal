# Spike S-09: Optuna on PostgreSQL, and the cost per trial

**Run:** 2026-11-19, M10, by the agent. **Scripts:** `scripts/spikes/s09_hpo.py` (items 1–5) and `scripts/spikes/s09_importance.py` (item 5 again, both evaluators). **Raw results:** `S-09-results.json`.
**Machine:** the `worker-cpu` image (2,500 MB limit, 16 logical CPUs visible) with Optuna 5.0.0 and scikit-learn 1.9.1 (the owner's lock choice: releases at least two weeks old), PostgreSQL 17 in Compose. Items 1 and 2 ran in a scratch database, `stratal_s09`, which the script created and dropped. Item 4 read the main database's past jobs and wrote nothing.

ADR-0018 assumed five things this spike checks. Optuna's tables can live in their own schema beside our Alembic. Ask and tell are cheap. Grouping classical trials in one job is worth it (decision 7). A deep-learning trial's start cost is small next to its training. And parameter importance is cheap enough to compute when a study closes.

## 1. Optuna's tables in their own schema

With `search_path=optuna` on Optuna's connection, and our migrations at head in the same database:

| Check | Result |
|---|---|
| Optuna's 13 tables, its own `alembic_version` (`v3.2.0.a`) included | all in schema `optuna` |
| Optuna tables in `public` | none |
| Our `alembic_version` | unchanged, `1da4cd4162e3` |
| `alembic check` (autogenerate against our models) | "No new upgrade operations detected" |
| `alembic upgrade head` again | succeeds, no change |
| `optuna storage upgrade` | "This storage is up-to-date" |

`alembic check` also prints SQLAlchemy's existing warning about the `dataset_versions` ⇄ `jobs` foreign-key cycle, which predates M10 and doesn't affect this.

**Result:** ADR-0018 rule 3 works as written. Our migration only needs `CREATE SCHEMA optuna`.

## 2. Ask and tell on PostgreSQL

40 trials, each with 10 log-float parameters and 50 intermediate values, under TPE and the median pruner:

| Call | Median | p95 |
|---|---|---|
| `ask` plus 10 suggestions | 64 ms | 76 ms |
| `report` (one intermediate value) | 3.6 ms | 4.8 ms |
| `should_prune` | 6.7 ms | 9.2 ms |
| `tell` | 15 ms | 21 ms |

`ask` took 61 ms over the first 10 trials and 64 ms over the last 10, so TPE's cost barely grew over 40 trials. The schema held 827 KB after 40 trials, about 400 KB of it the empty tables' own pages: roughly 10 KB per trial at this size, so a 200-trial study is a few MB.

**Per trial:**
- **Deep learning, 10 epochs:** one ask, ten reports and ten pruning checks, one tell, about 0.2 s against a minute of training. Negligible.
- **Classical, 5 folds:** one ask, five reports and checks, one tell, about 0.13 s against the 0.78 s trial of item 3: 17 %. It is the price of storing every trial and of pruning by fold, both of which HPO-1.3 and SCR-10 need. It is still well under the cost of a job per trial (item 3).

## 3. A classical trial: in one process, or its own job

The trial: a random forest (100 trees, depth 6) with 5-fold CV on breast cancer (569 rows), 5 repeats.

| | Median |
|---|---|
| The trial inside a running process | 0.78 s |
| The same trial in a fresh interpreter | 2.68 s |
| of which imports (NumPy, scikit-learn) | 1.63 s |
| of which loading the data | 0.01 s |
| A job's own start before its stage runs (past retention jobs: created → stage started) | about 1.8 s |

A job per trial would cost about 0.78 + 1.9 + 1.8 ≈ 4.5 s per trial, against about 0.9 s in one job (with item 2's Optuna calls), which is five times as long. 71 % of a fresh interpreter's time is overhead.

**Result:** decision 7's grouping of classical trials in one job is confirmed.

## 4. A deep-learning trial as a job

There is no trial job yet, so its start cost is taken from the 14 completed `train` jobs in the main database (job_0030 to job_0057):

| Interval | Typical, GPU free | Note |
|---|---|---|
| created → claimed | 0.1–1.2 s | longer only when another GPU job held the queue: 22–502 s for job_0036, job_0037, job_0042 and job_0053 |
| created → stage started | 1.6–2.8 s | |
| created → first training progress | 12.6–16.7 s (median of all 14: 16.4 s) | the child's start, CUDA's initialisation, the model, the loader; for a small model the first time, RUN-1.9's device benchmark. Not broken down further |

The step that advances a study (ADR-0018 rule 5) is a short CPU job: about 2 s of job start (past retention jobs, created → finished, median 2.0 s), Optuna's import (0.67 s, measured three times) and one tell and one ask (0.08 s). That comes to **about 3 s between one trial's end and the next trial's creation.**

So one trial costs about **16 s of start plus 3 s of advance, about 19 s**, on top of its training:

| Trial | Training | Overhead share |
|---|---|---|
| Small CNN, 10 epochs (M9 demo: 65 s) | 65 s | 23 % |
| Small CNN, 30 epochs (job_0051: 181 s of training) | 181 s | 9 % |

The figure is noticeable on short trials and small on long ones. A trial pruned at epoch 2 pays all of it on about 13 s of training, but that trial is cheap either way. Running all deep-learning trials in one job would save most of the 16 s. It would also give up what decision 7 bought: a fresh process per trial (VRAM released by the operating system), and pause, resume and out-of-memory handling per trial.

**Result:** decision 7 stands for deep learning. SCR-09's estimate of a study's duration adds about 19 s per trial: 20 trials of 65 s come to about 28 min, not 22 (the low-fi wireframe is corrected).

## 5. Parameter importance

A test function with a known answer: the learning rate dominates, depth matters a little, and dropout, width and weight decay barely matter. Both evaluators were run on the same TPE studies:

| Trials | fANOVA (seed 0) | PED-ANOVA (Optuna 5.0's default) |
|---|---|---|
| 20 | 0.14 s · lr 0.92, weight_decay 0.05, width 0.02, dropout 0.02, depth 0.00 | 0.005 s · **dropout 0.54**, lr 0.23, depth 0.15, … |
| 200 | 1.71 s · lr 0.95, dropout 0.05, depth 0.00, … | 0.007 s · lr 0.66, depth 0.22, dropout 0.08, … |

**Finding:** ADR-0018 rule 9 says "Optuna's default fANOVA evaluator". In Optuna 5.0 the default is **PED-ANOVA**, which ranked the wrong parameter first at 20 trials, the size of a typical study here. PED-ANOVA asks a different question: which parameters separate the top trials from the rest. That needs more trials than M10's studies will have. fANOVA ranked the learning rate first at both sizes, in under 2 s.

**Result:** the decision is unchanged. Importance uses fANOVA, now **set explicitly** (`FanovaImportanceEvaluator(seed=…)`, which needs scikit-learn as the ADR says), not taken from Optuna's default. ADR-0018 gets an "As measured" note correcting the word "default".

## Consequences

- ADR-0018 holds as approved. Two notes are added under "As measured (S-09)": importance uses fANOVA set explicitly, because Optuna 5.0's default is PED-ANOVA; and a deep-learning trial costs about 19 s beyond its training.
- The migration for `studies` creates the empty `optuna` schema; nothing else is needed for rule 3.
- SCR-09's study duration estimate adds about 19 s per deep-learning trial (corrected in `SCR-09hpo-lofi_v1.html`).
- No figure here calls for a change to decision 7, to the memory limits or to the queues.
