# pyright: basic, reportMissingImports=false
# Optuna and scikit-learn exist only in the worker images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""The ``study`` stage: an HPO study run by Optuna (HPO-1.2 to HPO-1.5, ADR-0018).

A classical study runs all its trials in this one stage (decision 7, rule 4):

* **TPE**, seeded with the study's seed, suggests each trial's values; Optuna keeps
  the trials in its own tables, in schema ``optuna`` (rule 3, spike S-09).
* **Cross-validation on the train split** scores a trial: ``cv_folds`` folds that
  keep split groups whole (``StratifiedGroupKFold`` for classes, ``GroupKFold`` for
  a number), the whole pipeline fitted on each fold's training part (ML-1.3).
* **Pruning** (rule 6): after each fold the running mean is reported; the median
  pruner stops a trial behind the median, from fold 1 and after 5 trials. The time
  budget stops a running trial at its next fold, recorded as pruned with reason
  ``budget``.
* **Pause and resume**: a pause stops between folds; the trial in progress is
  recorded as failed ``interrupted`` and does not count against ``n_trials``; a
  resume reopens the study with what is left of both limits.
* **At the end** (rules 7 to 9): parameter importance by fANOVA (set explicitly:
  Optuna 5.0's default is PED-ANOVA, S-09), then the best configuration is refit on
  the train split, scored on validation and test once, and registered.

A retry of a study that had finished picks up where it stopped.
"""

import math
import time
from dataclasses import replace
from typing import Any

import optuna
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from stratal.core.errors import StratalError
from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.models import ModelVersion, Study
from stratal.hpo import store as hpo_store
from stratal.hpo.space import Marker, apply
from stratal.ml.data import SplitTable, TabularData, load_tabular
from stratal.ml.params import ClassicalConfig, check_data, check_task
from stratal.ml.pipeline import build_pipeline, fit, score
from stratal.mon.events import MetricValue, utc_now
from stratal.runtime.context import JobCancelled, JobPaused
from stratal.runtime.stages import StageResult, StageRun

STAGE = "study"
STARTUP_TRIALS = 5
OBJECTIVE_SCORE = {
    "cv_accuracy": "val_accuracy",
    "cv_macro_f1": "val_macro_f1",
    "cv_r2": "val_r2",
    "cv_rmse": "val_rmse",
}
"""A classical objective, and the score of a fold's held-out part it averages."""


class InvalidCombination(Exception):
    """A sampled configuration the rules refuse (ML-1.4): the trial fails and the study goes on."""


def storage_url() -> str:
    """Optuna's connection: the same database, its own schema (ADR-0018 rule 3)."""
    url = make_url(get_settings().database_url)
    return url.update_query_dict({"options": "-csearch_path=optuna"}).render_as_string(
        hide_password=False
    )


def suggest(trial: optuna.Trial, marker: Marker) -> Any:
    name = marker.path
    if marker.type == "categorical":
        assert marker.choices is not None
        return trial.suggest_categorical(name, marker.choices)
    assert marker.low is not None and marker.high is not None
    if marker.type == "int":
        step = int(marker.step) if marker.step else 1
        return trial.suggest_int(name, int(marker.low), int(marker.high), step=step)
    return trial.suggest_float(
        name, marker.low, marker.high, log=marker.type == "log_float", step=marker.step
    )


def _subset(part: SplitTable, rows: Any) -> SplitTable:
    index = [int(i) for i in rows]
    return SplitTable(
        {name: [values[i] for i in index] for name, values in part.columns.items()},
        [part.target[i] for i in index],
        [part.groups[i] for i in index],
    )


class ClassicalStudy:
    def __init__(self, run: StageRun, study: Study) -> None:
        self.run = run
        self.ctx = run.ctx
        self.engine = run.engine
        self.study_id = study.id
        self.base: dict[str, Any] = dict(study.base)
        self.markers = [Marker.model_validate(m) for m in study.space]
        self.objective = study.objective
        self.direction = study.direction
        self.limits = dict(study.limits)
        self.pruning = study.pruning
        self.folds = int(study.cv_folds or 5)
        self.seed = study.seed
        self.state = study.state
        self.elapsed_before = float(study.elapsed_s)
        self.started = time.monotonic()

    # ------------------------------------------------------------------ helpers
    def elapsed(self) -> float:
        return self.elapsed_before + (time.monotonic() - self.started)

    def emit(self, message: str, **fields: Any) -> None:
        self.ctx.emit(stage=STAGE, message=message, **fields)

    def load(self) -> TabularData:
        with Session(self.engine) as session:
            _, manifest = registry.manifest_for(
                session, self.run.data_dir, str(self.run.input_version_id)
            )
        data = load_tabular(manifest)
        groups = len(set(data.splits["train"].groups))
        if groups < self.folds:
            raise StratalError(
                f"The train split has {groups} groups, fewer than its {self.folds} folds.",
                suggested_action="Lower cv_folds.",
            )
        return data

    def splitter(self, data: TabularData) -> Any:
        from sklearn.model_selection import GroupKFold, StratifiedGroupKFold

        if data.task == "classification":
            return StratifiedGroupKFold(n_splits=self.folds, shuffle=True, random_state=self.seed)
        return GroupKFold(n_splits=self.folds)

    def optuna_study(self) -> optuna.Study:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        pruner: optuna.pruners.BasePruner = (
            optuna.pruners.MedianPruner(n_startup_trials=STARTUP_TRIALS, n_warmup_steps=1)
            if self.pruning
            else optuna.pruners.NopPruner()
        )
        return optuna.create_study(
            study_name=self.study_id,
            storage=optuna.storages.RDBStorage(storage_url()),
            direction=self.direction,
            sampler=optuna.samplers.TPESampler(seed=self.seed),
            pruner=pruner,
            load_if_exists=True,
        )

    def reopen(self, study: optuna.Study) -> None:
        """Trials left running by a pause or a crash: failed, ``interrupted``, uncounted."""
        storage = study._storage
        for trial in study.get_trials(deepcopy=False, states=(optuna.trial.TrialState.RUNNING,)):
            storage.set_trial_state_values(trial._trial_id, optuna.trial.TrialState.FAIL)
            hpo_store.trial_finished(
                self.engine, self.study_id, trial.number, state="failed", code="interrupted"
            )

    # ------------------------------------------------------------------ one trial
    def evaluate(self, trial: optuna.Trial, data: TabularData, cv: Any) -> float:
        values = {m.path: suggest(trial, m) for m in self.markers}
        hpo_store.trial_started(self.engine, self.study_id, trial.number, values)
        try:
            config = ClassicalConfig.model_validate(apply(self.base, values)["classical"])
            spec = config.model.resolved()
            problem = check_task(spec.estimator, data.task) or next(
                (f"{field}: {why}" for field, why in check_data(spec, data.facts())), None
            )
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            problem = str(exc).splitlines()[-1] if str(exc) else type(exc).__name__
            spec = None
        if problem is not None or spec is None:
            hpo_store.trial_finished(
                self.engine, self.study_id, trial.number, state="failed", code="invalid_combination"
            )
            self.emit(
                f"Trial {trial.number} failed: invalid_combination · {problem}",
                substage="trial",
                metrics={"trial": trial.number, "code": "invalid_combination"},
            )
            raise InvalidCombination(problem)

        train = data.splits["train"]
        key = OBJECTIVE_SCORE[self.objective]
        scores: list[float] = []
        for step, (fit_rows, held_rows) in enumerate(
            cv.split(train.groups, [str(t) for t in train.target], groups=train.groups), start=1
        ):
            self.stop_if_asked(trial.number)
            if self.elapsed() >= self.limits["timeout_s"]:
                value = sum(scores) / len(scores) if scores else None
                hpo_store.trial_finished(
                    self.engine,
                    self.study_id,
                    trial.number,
                    state="pruned",
                    value=value,
                    prune_reason="budget",
                )
                raise optuna.TrialPruned("time budget")
            fold = replace(
                data,
                splits={
                    "train": _subset(train, fit_rows),
                    "val": _subset(train, held_rows),
                    "test": SplitTable({n: [] for n in train.columns}),
                },
            )
            pipeline = fit(build_pipeline(spec, fold, self.seed), fold, spec)
            scores.append(float(score(pipeline, fold, spec, ("val",)).scalars[key]))
            mean = sum(scores) / len(scores)
            trial.report(mean, step)
            hpo_store.trial_step(self.engine, self.study_id, trial.number, step, mean)
            if self.pruning and trial.should_prune():
                hpo_store.trial_finished(
                    self.engine,
                    self.study_id,
                    trial.number,
                    state="pruned",
                    value=mean,
                    prune_reason="median",
                )
                self.emit(
                    f"Trial {trial.number} pruned at fold {step} · median",
                    substage="trial",
                    metrics={"trial": trial.number, "pruned_at_step": step},
                )
                raise optuna.TrialPruned(f"median at fold {step}")
        value = sum(scores) / len(scores)
        if not math.isfinite(value):
            raise InvalidCombination("the score is not finite")
        hpo_store.trial_finished(
            self.engine, self.study_id, trial.number, state="complete", value=value
        )
        return value

    def stop_if_asked(self, number: int) -> None:
        request = self.ctx.stop_request()
        if request is None:
            return
        hpo_store.trial_finished(
            self.engine, self.study_id, number, state="failed", code="interrupted"
        )
        hpo_store.update_study(self.engine, self.study_id, elapsed_s=round(self.elapsed(), 3))
        if request == "cancel":
            hpo_store.update_study(
                self.engine, self.study_id, state="cancelled", finished_at=utc_now()
            )
            raise JobCancelled(self.ctx.job_id)
        raise JobPaused(self.ctx.job_id)

    def after_trial(self, study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        values: dict[str, Any] = {"elapsed_s": round(self.elapsed(), 3)}
        complete = [
            t
            for t in study.get_trials(deepcopy=False)
            if t.state == optuna.trial.TrialState.COMPLETE
        ]
        if complete:
            best = study.best_trial
            values.update(best_trial=best.number, best_value=best.value, best_params=best.params)
        hpo_store.update_study(self.engine, self.study_id, **values)
        with Session(self.engine) as session:
            counts = hpo_store.counts(session, self.study_id)
        self.emit(
            f"Trial {trial.number} {trial.state.name.lower()} · {counts['started']}/"
            f"{self.limits['n_trials']} trials · best {values.get('best_value', '–')}",
            substage="trials",
            step=counts["started"],
            total=int(self.limits["n_trials"]),
            metrics={"trials_started": counts["started"], "trials_complete": counts["complete"]},
        )

    # ------------------------------------------------------------------ the study
    def search(self, data: TabularData) -> None:
        study = self.optuna_study()
        self.reopen(study)
        with Session(self.engine) as session:
            started = hpo_store.counts(session, self.study_id)["started"]
        remaining = int(self.limits["n_trials"]) - started
        budget = float(self.limits["timeout_s"]) - self.elapsed_before
        self.emit(
            f"Tuning {len(self.markers)} fields by {self.folds}-fold cross-validation · "
            f"{self.objective} ({self.direction}) · {remaining} trials or {budget:.0f} s left",
            substage="setup",
            metrics={"trials_left": remaining, "budget_left_s": round(budget, 1)},
        )
        if remaining > 0 and budget > 0:
            cv = self.splitter(data)
            study.optimize(
                lambda trial: self.evaluate(trial, data, cv),
                n_trials=remaining,
                timeout=budget,
                catch=(InvalidCombination,),
                callbacks=[self.after_trial],
                gc_after_trial=False,
            )
        with Session(self.engine) as session:
            counts = hpo_store.counts(session, self.study_id)
        ended_by = "n_trials" if counts["started"] >= int(self.limits["n_trials"]) else "timeout"
        importance = self.importance(study)
        hpo_store.update_study(
            self.engine,
            self.study_id,
            state="closed",
            ended_by=ended_by,
            elapsed_s=round(self.elapsed(), 3),
            importance=importance,
        )
        self.state = "closed"
        self.emit(
            f"Study closed by {ended_by}: {counts['complete']} complete, {counts['pruned']} "
            f"pruned, {counts['failed']} failed",
            substage="close",
            metrics={k: v for k, v in counts.items()},
        )

    def importance(self, study: optuna.Study) -> dict[str, Any]:
        """fANOVA, set explicitly (S-09), over the complete trials (HPO-1.5, rule 9)."""
        complete = [
            t
            for t in study.get_trials(deepcopy=False)
            if t.state == optuna.trial.TrialState.COMPLETE
        ]
        if len(self.markers) < 2:
            return {"reason": "one field is tuned, so there is nothing to rank"}
        if len(complete) < 2:
            return {"reason": f"{len(complete)} complete trial; importance needs two"}
        if len({t.value for t in complete}) == 1:
            return {"reason": "every complete trial scored the same, so nothing can be ranked"}
        evaluator = optuna.importance.FanovaImportanceEvaluator(seed=self.seed)
        try:
            found = optuna.importance.get_param_importances(study, evaluator=evaluator)
        except (RuntimeError, ValueError) as exc:  # importance is a report: never fail the study
            return {"reason": f"fANOVA could not rank the fields: {exc}"}
        return {"method": "fanova", "values": {k: round(float(v), 6) for k, v in found.items()}}

    def finish(self) -> dict[str, MetricValue]:
        """Refit the best configuration, register it, test it once (rules 7 and 8)."""
        from stratal.ml.stage import ClassicalRun

        with Session(self.engine) as session:
            study = hpo_store.get_study(session, self.study_id)
            best_params = dict(study.best_params or {})
            best_trial = study.best_trial
        if best_trial is None:
            hpo_store.update_study(
                self.engine, self.study_id, state="completed", finished_at=utc_now()
            )
            self.emit(
                "No trial completed: nothing to register or evaluate",
                level="WARN",
                reason="Every trial failed or was pruned before it finished.",
                suggested_action="Widen the space or the budget, and start a new study.",
            )
            return {"trials_complete": 0}
        config = ClassicalConfig.model_validate(apply(self.base, best_params)["classical"])
        training = ClassicalRun(self.run, config)
        result = training.already_registered() or training.execute()
        version_id = str(result.metrics["model_version_id"])
        with Session(self.engine) as session, session.begin():
            version = session.get(ModelVersion, version_id)
            assert version is not None
            version.study_id = self.study_id
            detail = dict((version.metrics or {}).get("detail") or {})
        test = {k: v for k, v in result.metrics.items() if str(k).startswith("test_")}
        wrote = hpo_store.record_test_once(
            self.engine, self.study_id, {**test, "detail": detail.get("test")}
        )
        hpo_store.update_study(
            self.engine,
            self.study_id,
            best_version_id=version_id,
            state="completed",
            finished_at=utc_now(),
        )
        self.emit(
            f"Best trial {best_trial} registered as {version_id}; test evaluated once"
            + ("" if wrote else " (kept from the first evaluation)"),
            substage="test_eval",
            metrics={"model_version_id": version_id, **test},
        )
        return {"best_trial": best_trial, "model_version_id": version_id, **test}

    def execute(self) -> StageResult:
        if self.state == "running":
            self.search(self.load())
        metrics = self.finish()
        with Session(self.engine) as session:
            counts = hpo_store.counts(session, self.study_id)
            study = hpo_store.get_study(session, self.study_id)
            best_value = study.best_value
        metrics.update(
            {
                "study_id": self.study_id,
                "trials_started": counts["started"],
                "trials_complete": counts["complete"],
                "trials_pruned": counts["pruned"],
                "trials_failed": counts["failed"],
            }
        )
        if best_value is not None:
            metrics["best_value"] = round(float(best_value), 6)
        return StageResult(metrics=metrics)


def run_study(run: StageRun) -> StageResult:
    study_id = str(run.config["study_id"])
    with Session(run.engine) as session:
        study = hpo_store.get_study(session, study_id)
        session.expunge(study)
    if study.state in ("completed", "cancelled"):
        return StageResult(metrics={"study_id": study_id})
    if study.kind != "classical":
        raise StratalError("Studies of networks arrive with the next part of M10.")
    return ClassicalStudy(run, study).execute()
