"""Spike S-09, item 5 again: fANOVA against PED-ANOVA (Optuna 5.0's default) on 20 and 200 trials.

    docker compose run --rm --no-deps -v ./scripts:/app/scripts:ro worker-cpu \
        python scripts/spikes/s09_importance.py
"""

# pyright: basic, reportMissingImports=false

import json
import time

import optuna
from optuna.importance import FanovaImportanceEvaluator, PedAnovaImportanceEvaluator

optuna.logging.set_verbosity(optuna.logging.WARNING)


def objective(t: optuna.Trial) -> float:
    # lr matters most, depth a little, the rest barely: a known answer to compare against.
    lr = t.suggest_float("lr", 1e-5, 1e-1, log=True)
    depth = t.suggest_int("depth", 1, 12)
    width = t.suggest_categorical("width", [32, 64, 128, 256])
    dropout = t.suggest_float("dropout", 0.0, 0.5)
    wd = t.suggest_float("weight_decay", 1e-6, 1e-2, log=True)
    return abs(lr - 3e-3) * 100 + depth * 0.01 + 0.001 * width / 32 + dropout * 0.1 + wd


out = {}
for n in (20, 200):
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(objective, n_trials=n)
    for name, ev in (
        ("fanova", FanovaImportanceEvaluator(seed=0)),
        ("ped_anova", PedAnovaImportanceEvaluator()),
    ):
        t0 = time.perf_counter()
        imp = optuna.importance.get_param_importances(study, evaluator=ev)
        out[f"{name}_{n}"] = {
            "seconds": round(time.perf_counter() - t0, 3),
            "importance": {k: round(v, 3) for k, v in imp.items()},
        }
print(json.dumps(out, indent=1))
