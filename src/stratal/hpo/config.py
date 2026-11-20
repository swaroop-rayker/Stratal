"""A study request and its objectives (HPO-1.1 to HPO-1.4, ADR-0018 rule 1). Plain Python."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratal.hpo.space import Marker
from stratal.ml.params import ClassicalConfig
from stratal.train.config import TrainConfig

Direction = Literal["maximize", "minimize"]

OBJECTIVES: dict[str, dict[str, Direction]] = {
    # Classical models are tuned by cross-validation on the train split (ADR-0018 rule 4).
    "classical:classification": {"cv_accuracy": "maximize", "cv_macro_f1": "maximize"},
    "classical:regression": {"cv_r2": "maximize", "cv_rmse": "minimize"},
    # Networks on the validation split, after each epoch (ADR-0018 rule 5).
    "deep:classification": {"val_accuracy": "maximize", "val_loss": "minimize"},
    "deep:regression": {"val_rmse": "minimize", "val_loss": "minimize"},
}
"""Each kind and task's objectives, the first its default, with their directions."""


def objectives(kind: str, task: str) -> dict[str, Direction]:
    return OBJECTIVES[f"{kind}:{task}"]


class StudyRequest(BaseModel):
    """``POST /jobs`` with ``kind: hpo_study``: a base run, what to tune, the limits."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["hpo_study"]
    dataset_version: str = Field(description="The version the base run would train on.")
    classical: ClassicalConfig | None = Field(
        default=None, description="A classical base run (ML-1.1). Give this or 'config'."
    )
    config: TrainConfig | None = Field(
        default=None, description="A network base run (RUN-1.1). Give this or 'classical'."
    )
    space: list[Marker] = Field(
        min_length=1, max_length=20, description="The tuned fields (HPO-1.1)."
    )
    objective: str | None = Field(
        default=None,
        description="What to optimise: cv_accuracy, cv_macro_f1, cv_r2 or cv_rmse for a "
        "classical model; val_accuracy, val_loss or val_rmse for a network. Empty: the "
        "task's first.",
    )
    n_trials: int = Field(default=20, ge=1, le=200, description="Trials started at most.")
    timeout_s: int = Field(
        default=3600, ge=60, le=86_400, description="Running time at most, pauses excluded."
    )
    pruning: bool = Field(default=True, description="Median pruning (HPO-1.3).")
    cv_folds: int = Field(default=5, ge=2, le=10, description="Classical models only.")
    seed: int = Field(default=0, ge=0, le=2**31 - 1, description="TPE's suggestions.")

    @model_validator(mode="after")
    def _one_base(self) -> "StudyRequest":
        if (self.classical is None) == (self.config is None):
            raise ValueError("give exactly one of 'classical' and 'config' as the base run")
        return self

    @property
    def study_kind(self) -> Literal["classical", "deep"]:
        return "classical" if self.classical is not None else "deep"

    def base(self) -> dict[str, Any]:
        """The base run as a JSON document, the root of every marker's path."""
        if self.classical is not None:
            return {"classical": self.classical.model_dump(mode="json")}
        assert self.config is not None
        return {"config": self.config.model_dump(mode="json")}
