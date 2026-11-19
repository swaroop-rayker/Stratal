"""Spike S-09: Optuna on PostgreSQL and the cost per trial (ADR-0018).

Run in the worker-cpu image, on the Compose network, with the repository mounted:

    docker compose run --rm -v ./src:/app/src:ro -v ./migrations:/app/migrations:ro \
        -v ./scripts:/app/scripts:ro -v ./docs/spikes:/out worker-cpu \
        python scripts/spikes/s09_hpo.py --out /out/S-09-results.json

It creates a scratch database, ``stratal_s09``, and drops it at the end. The main
database is only read (past jobs' start latencies, item 4).

1. Optuna's tables in their own schema beside our Alembic.
2. Ask and tell latency; rows and bytes per trial.
3. A classical trial in one process against the same trial in a fresh interpreter.
4. A deep-learning trial as a job: start latency of past train jobs.
5. fANOVA's time on 20 and 200 trials.
"""

# pyright: basic, reportMissingImports=false

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url

SCRATCH_DB = "stratal_s09"
MAIN_URL = make_url(os.environ["STRATAL_DATABASE_URL"])
SCRATCH_URL = MAIN_URL.set(database=SCRATCH_DB)


def _url(url: sa.URL) -> str:
    return url.render_as_string(hide_password=False)


def _admin() -> sa.Engine:
    return sa.create_engine(_url(MAIN_URL), isolation_level="AUTOCOMMIT")


def create_scratch() -> None:
    with _admin().connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        c.execute(sa.text(f"CREATE DATABASE {SCRATCH_DB}"))


def drop_scratch() -> None:
    with _admin().connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))


def optuna_url() -> str:
    # search_path puts Optuna's tables, and its own alembic_version, in schema optuna.
    return _url(SCRATCH_URL.update_query_dict({"options": "-csearch_path=optuna"}))


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "STRATAL_DATABASE_URL": _url(SCRATCH_URL)}
    return subprocess.run(
        ["alembic", *args], env=env, capture_output=True, text=True, cwd="/app", check=False
    )


# ------------------------------------------------------------------ 1. schema


def item_1_schema() -> dict[str, Any]:
    import optuna

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = sa.create_engine(_url(SCRATCH_URL))
    with engine.begin() as c:
        c.execute(sa.text("CREATE SCHEMA optuna"))
    optuna.create_study(storage=optuna_url(), study_name="probe")
    with engine.connect() as c:
        tables = c.execute(
            sa.text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('public', 'optuna') ORDER BY 1, 2"
            )
        ).all()
        ours = c.execute(sa.text("SELECT version_num FROM public.alembic_version")).scalar()
        theirs = c.execute(sa.text("SELECT version_num FROM optuna.alembic_version")).scalar()
    in_optuna = sorted(t for s, t in tables if s == "optuna")
    public = {t for s, t in tables if s == "public"}
    leaked = sorted(set(in_optuna) & public - {"alembic_version"})
    check = alembic("check")  # autogenerate: fails if models and database differ
    again = alembic("upgrade", "head")
    upgrade = subprocess.run(
        ["optuna", "storage", "upgrade", "--storage", optuna_url()],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "optuna_tables": in_optuna,
        "optuna_tables_in_public": leaked,
        "our_alembic_version": ours,
        "optuna_alembic_version": theirs,
        "alembic_check_rc": check.returncode,
        "alembic_check_out": (check.stdout + check.stderr).strip()[-400:],
        "alembic_upgrade_again_rc": again.returncode,
        "optuna_storage_upgrade_rc": upgrade.returncode,
        "optuna_storage_upgrade_out": (upgrade.stdout + upgrade.stderr).strip()[-400:],
    }


# ------------------------------------------------------------------ 2. ask and tell


def item_2_ask_tell(n: int = 40) -> dict[str, Any]:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    storage = optuna.storages.RDBStorage(optuna_url())
    study = optuna.create_study(
        storage=storage,
        study_name="ask_tell",
        sampler=optuna.samplers.TPESampler(seed=0),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=2),
    )
    ask_ms: list[float] = []
    report_ms: list[float] = []
    prune_ms: list[float] = []
    tell_ms: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        trial = study.ask()
        params = {f"p{i}": trial.suggest_float(f"p{i}", 1e-4, 1.0, log=True) for i in range(10)}
        ask_ms.append(1000 * (time.perf_counter() - t0))
        value = sum(params.values())
        for step in range(50):
            t0 = time.perf_counter()
            trial.report(value / (step + 1), step)
            report_ms.append(1000 * (time.perf_counter() - t0))
            t0 = time.perf_counter()
            trial.should_prune()
            prune_ms.append(1000 * (time.perf_counter() - t0))
        t0 = time.perf_counter()
        study.tell(trial, value)
        tell_ms.append(1000 * (time.perf_counter() - t0))

    engine = sa.create_engine(_url(SCRATCH_URL))
    with engine.connect() as c:
        sizes = c.execute(
            sa.text(
                "SELECT relname, n_live_tup, pg_total_relation_size(relid) "
                "FROM pg_stat_user_tables WHERE schemaname = 'optuna' ORDER BY 1"
            )
        ).all()
        total = c.execute(
            sa.text(
                "SELECT sum(pg_total_relation_size(relid)) FROM pg_stat_user_tables "
                "WHERE schemaname = 'optuna'"
            )
        ).scalar()
        rows = {
            t: c.execute(sa.text(f'SELECT count(*) FROM optuna."{t}"')).scalar()
            for t in ("trials", "trial_params", "trial_intermediate_values", "trial_values")
        }

    def summary(xs: list[float]) -> dict[str, float]:
        xs = sorted(xs)
        return {
            "median_ms": round(statistics.median(xs), 2),
            "p95_ms": round(xs[int(0.95 * (len(xs) - 1))], 2),
            "max_ms": round(xs[-1], 2),
        }

    return {
        "trials": n,
        "params_per_trial": 10,
        "intermediate_values_per_trial": 50,
        "ask_with_10_suggests": summary(ask_ms),
        "ask_first_10_trials_median_ms": round(statistics.median(ask_ms[:10]), 2),
        "ask_last_10_trials_median_ms": round(statistics.median(ask_ms[-10:]), 2),
        "report": summary(report_ms),
        "should_prune": summary(prune_ms),
        "tell": summary(tell_ms),
        "rows": rows,
        "rows_per_trial": {k: v / n for k, v in rows.items()},
        "bytes_total_optuna_schema": total,
        "table_sizes": [{"table": r[0], "live_rows": r[1], "bytes": r[2]} for r in sizes],
    }


# ------------------------------------------------------------------ 3. classical trial

TRIAL_CODE = r"""
import time
t0 = time.perf_counter()
import numpy as np
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
t1 = time.perf_counter()
X, y = load_breast_cancer(return_X_y=True)
t2 = time.perf_counter()
forest = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=0, n_jobs=1)
model = make_pipeline(StandardScaler(), forest)
cv = StratifiedKFold(5, shuffle=True, random_state=0)
score = cross_val_score(model, X, y, cv=cv).mean()
t3 = time.perf_counter()
print(f"{t1 - t0:.4f} {t2 - t1:.4f} {t3 - t2:.4f} {score:.4f}")
"""


def item_3_classical(repeats: int = 5) -> dict[str, Any]:
    from sklearn.datasets import load_breast_cancer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x, y = load_breast_cancer(return_X_y=True)
    in_process: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        model = make_pipeline(
            StandardScaler(),
            RandomForestClassifier(n_estimators=100, max_depth=6, random_state=0, n_jobs=1),
        )
        cross_val_score(model, x, y, cv=StratifiedKFold(5, shuffle=True, random_state=0)).mean()
        in_process.append(time.perf_counter() - t0)

    fresh: list[dict[str, float]] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = subprocess.run(
            [sys.executable, "-c", TRIAL_CODE], capture_output=True, text=True, check=True
        )
        wall = time.perf_counter() - t0
        imports, load, fit, _score = (float(v) for v in out.stdout.split())
        fresh.append({"wall_s": wall, "imports_s": imports, "load_s": load, "trial_s": fit})

    trial = statistics.median(in_process)
    wall = statistics.median(f["wall_s"] for f in fresh)
    return {
        "trial": "random forest, 100 trees, depth 6, 5-fold CV on breast cancer (569 rows)",
        "in_process_trial_s": [round(v, 3) for v in in_process],
        "in_process_median_s": round(trial, 3),
        "fresh_interpreter": [{k: round(v, 3) for k, v in f.items()} for f in fresh],
        "fresh_wall_median_s": round(wall, 3),
        "fresh_imports_median_s": round(statistics.median(f["imports_s"] for f in fresh), 3),
        "overhead_per_trial_s": round(wall - trial, 3),
        "overhead_share": round((wall - trial) / wall, 3),
    }


# ------------------------------------------------------------------ 4. job start latency


def item_4_job_latency() -> dict[str, Any]:
    engine = sa.create_engine(_url(MAIN_URL))
    query = sa.text(
        """
        SELECT j.id, j.kind, j.created_at, j.started_at, j.finished_at,
               (SELECT min(e.ts) FROM events e WHERE e.job_id = j.id AND e.type = 'stage_started')
                   AS stage_started,
               (SELECT min(e.ts) FROM events e WHERE e.job_id = j.id AND e.type = 'stage_progress'
                   AND e.stage = 'train') AS first_progress
        FROM jobs j
        WHERE j.state = 'completed' AND j.kind IN ('train', 'retention')
        ORDER BY j.created_at
        """
    )
    with engine.connect() as c:
        rows = c.execute(query).mappings().all()

    def secs(a: Any, b: Any) -> float | None:
        return None if a is None or b is None else round((b - a).total_seconds(), 2)

    jobs = [
        {
            "job": r["id"],
            "kind": r["kind"],
            "created_to_started_s": secs(r["created_at"], r["started_at"]),
            "created_to_stage_started_s": secs(r["created_at"], r["stage_started"]),
            "created_to_first_train_progress_s": secs(r["created_at"], r["first_progress"]),
            "total_s": secs(r["created_at"], r["finished_at"]),
        }
        for r in rows
    ]

    def med(kind: str, key: str) -> float | None:
        xs = [j[key] for j in jobs if j["kind"] == kind and j[key] is not None]
        return round(statistics.median(xs), 2) if xs else None

    return {
        "jobs": jobs,
        "retention_created_to_finished_median_s": med("retention", "total_s"),
        "train_created_to_first_progress_median_s": med(
            "train", "created_to_first_train_progress_s"
        ),
        "train_created_to_started_median_s": med("train", "created_to_started_s"),
    }


# ------------------------------------------------------------------ 5. fANOVA


def item_5_fanova() -> dict[str, Any]:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    out: dict[str, Any] = {}
    for n in (20, 200):
        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=0))

        def objective(t: optuna.Trial) -> float:
            lr = t.suggest_float("lr", 1e-5, 1e-1, log=True)
            depth = t.suggest_int("depth", 1, 12)
            width = t.suggest_categorical("width", [32, 64, 128, 256])
            dropout = t.suggest_float("dropout", 0.0, 0.5)
            wd = t.suggest_float("weight_decay", 1e-6, 1e-2, log=True)
            return (abs(lr - 3e-3) * 100) + depth * 0.01 + 0.001 * width / 32 + dropout * 0.1 + wd

        study.optimize(objective, n_trials=n)
        t0 = time.perf_counter()
        importance = optuna.importance.get_param_importances(study)
        out[str(n)] = {
            "seconds": round(time.perf_counter() - t0, 3),
            "importance": {k: round(v, 3) for k, v in importance.items()},
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    import optuna
    import sklearn

    results: dict[str, Any] = {
        "versions": {"optuna": optuna.__version__, "scikit-learn": sklearn.__version__},
        "cpus_visible": os.cpu_count(),
    }
    create_scratch()
    try:
        results["1_schema"] = item_1_schema()
        results["2_ask_tell"] = item_2_ask_tell()
    finally:
        drop_scratch()
    results["3_classical"] = item_3_classical()
    results["4_job_latency"] = item_4_job_latency()
    results["5_fanova"] = item_5_fanova()
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=2, default=str)
        f.write("\n")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
