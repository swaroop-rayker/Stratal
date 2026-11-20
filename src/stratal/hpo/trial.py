# pyright: basic, reportMissingImports=false
# Optuna exists only in the worker images, so this module is checked in basic mode
# (DECISIONS.md, "untyped libraries behind one module each").
"""A deep-learning trial's link to its study, from inside its ``train`` stage
(HPO-1.2, HPO-1.3, ADR-0018 rules 5 to 7).

After every epoch the trial reports the study's objective to Optuna at step = epoch
and asks the median pruner whether to stop, then adds the epoch's running time to
the study's budget clock: when the budget is spent, the trial stops at this epoch
(pruned, reason ``budget``) unless it was the last. The trial never sees the test
split (rule 8) and never writes to the registry: its initial and trained weights go
to ``jobs/<job>/trial/`` (rule 7), and the study's next step decides what to keep.
"""

import math
import time
from pathlib import Path
from typing import Any

import optuna
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import StratalError
from stratal.hpo import store as hpo_store
from stratal.hpo.control import trial_folder

WARMUP_EPOCHS = 2
"""The median pruner does not stop a network before its second epoch (rule 6)."""
STARTUP_TRIALS = 5


class TrialDiverged(StratalError):
    """The objective is not a finite number: the run diverged. The trial fails."""

    code = "diverged"
    title = "The trial diverged"


def pruner(pruning: bool, *, warmup: int) -> optuna.pruners.BasePruner:
    if not pruning:
        return optuna.pruners.NopPruner()
    return optuna.pruners.MedianPruner(n_startup_trials=STARTUP_TRIALS, n_warmup_steps=warmup)


class TrialLink:
    def __init__(self, engine: Engine, data_dir: Path, job_id: str, spec: dict[str, Any]) -> None:
        self.engine = engine
        self.study_id = str(spec["study_id"])
        self.number = int(spec["trial"])
        self.folder: Path = trial_folder(data_dir, job_id)
        with Session(engine) as session:
            study = hpo_store.get_study(session, self.study_id)
            self.objective = study.objective
            self.direction = study.direction
            self.pruning = study.pruning
            self.timeout_s = float(study.limits["timeout_s"])
        self._trial: optuna.Trial | None = None
        self._mark = time.monotonic()

    def _optuna(self) -> optuna.Trial:
        if self._trial is None:
            from stratal.hpo.runner import rdb_storage

            optuna.logging.set_verbosity(optuna.logging.WARNING)
            study = optuna.load_study(
                study_name=self.study_id,
                storage=rdb_storage(),
                pruner=pruner(self.pruning, warmup=WARMUP_EPOCHS),
            )
            trial_id = study._storage.get_trial_id_from_study_id_trial_number(
                study._study_id, self.number
            )
            self._trial = optuna.trial.Trial(study, trial_id)
        return self._trial

    def value(self, record: dict[str, Any]) -> float:
        if self.objective not in record:
            raise StratalError(
                f"The epoch's record has no {self.objective}.",
                suggested_action="Choose an objective the network reports.",
            )
        value = float(record[self.objective])
        if not math.isfinite(value):
            raise TrialDiverged(
                f"The trial's {self.objective} is {value}: the run diverged.",
                suggested_action="None needed: the study goes on. Lower the top of the "
                "learning rate's range if many trials diverge.",
            )
        return value

    def after_epoch(self, epoch: int, record: dict[str, Any], *, last: bool) -> str | None:
        """Report the epoch; return ``median`` or ``budget`` when the trial should stop."""
        value = self.value(record)
        trial = self._optuna()
        trial.report(value, epoch)
        hpo_store.trial_step(self.engine, self.study_id, self.number, epoch, value)
        now = time.monotonic()
        elapsed = hpo_store.add_elapsed(self.engine, self.study_id, now - self._mark)
        self._mark = now
        if last:
            return None
        if self.pruning and trial.should_prune():
            return "median"
        if elapsed >= self.timeout_s:
            return "budget"
        return None

    def pruned(self, reason: str, record: dict[str, Any]) -> None:
        hpo_store.trial_finished(
            self.engine,
            self.study_id,
            self.number,
            state="pruned",
            value=self.value(record),
            prune_reason=reason,
        )
        self._add_time()

    def complete(self, record: dict[str, Any]) -> float:
        value = self.value(record)
        hpo_store.trial_finished(
            self.engine, self.study_id, self.number, state="complete", value=value
        )
        self._add_time()
        return value

    def _add_time(self) -> None:
        now = time.monotonic()
        hpo_store.add_elapsed(self.engine, self.study_id, now - self._mark)
        self._mark = now
