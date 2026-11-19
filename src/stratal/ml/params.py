"""Classical models: estimators, parameters and the rules between them (ML-1.1, 1.2, 1.4).

Plain Python and Pydantic only, so ``api`` validates a request and serves the
forms' schemas (UI-2.3) without scikit-learn (ADR-0019 rule 16). The workers
turn a validated spec into a scikit-learn pipeline (:mod:`stratal.ml.pipeline`).

Each estimator has a parameter model with ranges and defaults. Rules that
involve more than one field are model validators, each raising a message that
names the fields (ML-1.4.AC1); rules that depend on the data (a task, a width,
a row count) are in :func:`check_task` and :func:`check_data`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Task = Literal["classification", "regression", "clustering", "projection"]
SupervisedTask = Literal["classification", "regression"]

EstimatorName = Literal[
    "linear",
    "ridge",
    "lasso",
    "logistic_regression",
    "svm",
    "decision_tree",
    "random_forest",
    "gradient_boosting",
    "knn",
    "naive_bayes",
    "kmeans",
    "dbscan",
    "hdbscan",
    "pca",
]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ClassWeight = Literal["none", "balanced"]


class LinearParams(_Params):
    fit_intercept: bool = Field(default=True, description="Fit an intercept term.")


class RidgeParams(_Params):
    alpha: float = Field(default=1.0, gt=0, le=1e6, description="L2 penalty strength.")
    fit_intercept: bool = True


class LassoParams(_Params):
    alpha: float = Field(default=1.0, gt=0, le=1e6, description="L1 penalty strength.")
    max_iter: int = Field(default=1000, ge=10, le=100_000)


class LogisticRegressionParams(_Params):
    penalty: Literal["l2", "l1", "elasticnet", "none"] = Field(
        default="l2", description="Regularisation; l1 and elasticnet need particular solvers."
    )
    C: float = Field(default=1.0, gt=0, le=1e6, description="Inverse regularisation strength.")
    solver: Literal["lbfgs", "liblinear", "saga", "newton-cg"] = "lbfgs"
    l1_ratio: float | None = Field(
        default=None, ge=0, le=1, description="elasticnet only: the share of L1 in the penalty."
    )
    dual: bool = Field(default=False, description="Dual formulation: l2 with liblinear only.")
    max_iter: int = Field(default=200, ge=10, le=100_000)
    class_weight: ClassWeight = "none"

    @model_validator(mode="after")
    def _rules(self) -> "LogisticRegressionParams":
        if self.penalty == "l1" and self.solver not in ("liblinear", "saga"):
            raise ValueError(
                f"penalty l1 needs solver liblinear or saga, not {self.solver} (penalty, solver)"
            )
        if self.penalty == "elasticnet" and self.solver != "saga":
            raise ValueError(
                f"penalty elasticnet needs solver saga, not {self.solver} (penalty, solver)"
            )
        if self.penalty == "elasticnet" and self.l1_ratio is None:
            raise ValueError("penalty elasticnet needs an l1_ratio (penalty, l1_ratio)")
        if self.penalty != "elasticnet" and self.l1_ratio is not None:
            raise ValueError("l1_ratio is only for penalty elasticnet (penalty, l1_ratio)")
        if self.dual and not (self.penalty == "l2" and self.solver == "liblinear"):
            raise ValueError("dual is only for penalty l2 with solver liblinear (dual)")
        if self.penalty == "none" and self.solver == "liblinear":
            raise ValueError("solver liblinear cannot fit without a penalty (penalty, solver)")
        return self


class SvmParams(_Params):
    C: float = Field(default=1.0, gt=0, le=1e6)
    kernel: Literal["rbf", "linear", "poly", "sigmoid"] = "rbf"
    gamma: Literal["scale", "auto"] = "scale"
    degree: int = Field(default=3, ge=1, le=10, description="poly only.")
    class_weight: ClassWeight = "none"

    @model_validator(mode="after")
    def _rules(self) -> "SvmParams":
        if self.degree != 3 and self.kernel != "poly":
            raise ValueError(f"degree is only for kernel poly, not {self.kernel} (kernel, degree)")
        return self


class DecisionTreeParams(_Params):
    max_depth: int | None = Field(default=None, ge=1, le=100, description="Empty: unlimited.")
    min_samples_split: int = Field(default=2, ge=2, le=10_000)
    min_samples_leaf: int = Field(default=1, ge=1, le=10_000)
    criterion: Literal["default", "gini", "entropy", "squared_error", "absolute_error"] = Field(
        default="default",
        description="Split quality: gini or entropy for classification, squared_error or "
        "absolute_error for regression; default picks gini or squared_error.",
    )
    class_weight: ClassWeight = "none"


class RandomForestParams(_Params):
    n_estimators: int = Field(default=100, ge=1, le=2000, description="Trees.")
    max_depth: int | None = Field(default=None, ge=1, le=100, description="Empty: unlimited.")
    min_samples_leaf: int = Field(default=1, ge=1, le=10_000)
    max_features: Literal["sqrt", "log2", "1.0"] = Field(
        default="sqrt", description="Features considered at each split; 1.0: all of them."
    )
    class_weight: ClassWeight = "none"


class GradientBoostingParams(_Params):
    """scikit-learn's histogram gradient boosting (SPEC §7.2; LightGBM is not planned)."""

    learning_rate: float = Field(default=0.1, gt=0, le=1)
    max_iter: int = Field(default=100, ge=1, le=2000, description="Boosting rounds.")
    max_depth: int | None = Field(default=None, ge=1, le=100)
    max_leaf_nodes: int = Field(default=31, ge=2, le=1024)
    min_samples_leaf: int = Field(default=20, ge=1, le=10_000)
    l2_regularization: float = Field(default=0.0, ge=0, le=1e6)


class KnnParams(_Params):
    n_neighbors: int = Field(default=5, ge=1, le=1000)
    weights: Literal["uniform", "distance"] = "uniform"
    p: Literal[1, 2] = Field(default=2, description="1: Manhattan distance; 2: Euclidean.")


class NaiveBayesParams(_Params):
    var_smoothing: float = Field(default=1e-9, gt=0, le=1, description="Gaussian naive Bayes.")


class KMeansParams(_Params):
    n_clusters: int = Field(default=8, ge=2, le=1000)
    n_init: int = Field(default=10, ge=1, le=100)
    max_iter: int = Field(default=300, ge=10, le=10_000)


class DbscanParams(_Params):
    eps: float = Field(default=0.5, gt=0, le=1e6, description="Neighbourhood radius.")
    min_samples: int = Field(default=5, ge=1, le=10_000)


class HdbscanParams(_Params):
    min_cluster_size: int = Field(default=5, ge=2, le=10_000)
    min_samples: int | None = Field(default=None, ge=1, le=10_000)


class PcaParams(_Params):
    n_components: int = Field(default=2, ge=1, le=10_000, description="Components kept.")


@dataclass(frozen=True)
class Estimator:
    name: EstimatorName
    title: str
    params: type[_Params]
    tasks: tuple[Task, ...]


ESTIMATORS: dict[str, Estimator] = {
    e.name: e
    for e in (
        Estimator("linear", "Linear regression", LinearParams, ("regression",)),
        Estimator("ridge", "Ridge regression", RidgeParams, ("regression",)),
        Estimator("lasso", "Lasso regression", LassoParams, ("regression",)),
        Estimator(
            "logistic_regression",
            "Logistic regression",
            LogisticRegressionParams,
            ("classification",),
        ),
        Estimator("svm", "Support vector machine", SvmParams, ("classification", "regression")),
        Estimator(
            "decision_tree", "Decision tree", DecisionTreeParams, ("classification", "regression")
        ),
        Estimator(
            "random_forest", "Random forest", RandomForestParams, ("classification", "regression")
        ),
        Estimator(
            "gradient_boosting",
            "Gradient boosting",
            GradientBoostingParams,
            ("classification", "regression"),
        ),
        Estimator("knn", "k nearest neighbours", KnnParams, ("classification", "regression")),
        Estimator("naive_bayes", "Naive Bayes", NaiveBayesParams, ("classification",)),
        Estimator("kmeans", "k-means", KMeansParams, ("clustering",)),
        Estimator("dbscan", "DBSCAN", DbscanParams, ("clustering",)),
        Estimator("hdbscan", "HDBSCAN", HdbscanParams, ("clustering",)),
        Estimator("pca", "PCA", PcaParams, ("projection",)),
    )
}
"""ML-1.1's and ML-1.2's estimators. Each name maps to scikit-learn's classifier or
regressor by the dataset's task (ADR-0019 rule 15); the unsupervised four ignore the target."""


class SklearnSpec(BaseModel):
    """A classical model: ``{"kind": "sklearn", "estimator": ..., "params": {...}}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["sklearn"] = "sklearn"
    estimator: EstimatorName
    params: dict[str, Any] = Field(
        default_factory=lambda: dict[str, Any](),
        description="The estimator's parameters; see GET /ml/estimators/schema.",
    )

    @model_validator(mode="after")
    def _valid_params(self) -> "SklearnSpec":
        # Raised as a ValueError so the request's 422 points at config.model.
        try:
            ESTIMATORS[self.estimator].params.model_validate(self.params)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "params"
            raise ValueError(f"{self.estimator}: {where}: {first['msg']}") from None
        return self

    def resolved(self) -> "SklearnSpec":
        """The spec with every parameter filled in, as recorded with the run (NFR-4)."""
        params = ESTIMATORS[self.estimator].params.model_validate(self.params)
        return SklearnSpec(estimator=self.estimator, params=params.model_dump(mode="json"))

    def typed_params(self) -> _Params:
        return ESTIMATORS[self.estimator].params.model_validate(self.params)

    @property
    def display(self) -> str:
        return self.estimator

    def identity(self) -> dict[str, Any]:
        """The spec as it enters a lineage key: the estimator, not its parameters, so the
        runs of a study and their re-runs share one lineage (ADR-0019 rule 19)."""
        return {"kind": "sklearn", "estimator": self.estimator}


class ClassicalConfig(BaseModel):
    """A classical model's run (ML-1.1, ML-1.2). It runs on the CPU queue (ADR-0019 rule 15)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: SklearnSpec
    seed: int = Field(default=0, ge=0, le=2**31 - 1, description="random_state where it applies.")


def task_for(estimator: str, data_task: SupervisedTask) -> Task:
    """The task a run performs: the dataset's, or the estimator's own if unsupervised."""
    tasks = ESTIMATORS[estimator].tasks
    return data_task if data_task in tasks else tasks[0]


def check_task(estimator: str, data_task: SupervisedTask) -> str | None:
    """Why ``estimator`` cannot run on a dataset of ``data_task``, or ``None``."""
    tasks = ESTIMATORS[estimator].tasks
    if data_task in tasks or tasks[0] in ("clustering", "projection"):
        return None
    return f"{estimator} is for {' or '.join(tasks)}; this dataset's target makes it {data_task}"


@dataclass(frozen=True)
class DataFacts:
    """What the rules that depend on the data need to know of the train split."""

    train_rows: int
    encoded_width: int
    """Features after one-hot encoding: numeric columns plus, per categorical column, its
    train split's categories plus one for unknown (as the pipeline encodes them)."""


def check_data(spec: SklearnSpec, facts: DataFacts) -> list[tuple[str, str]]:
    """``(field, reason)`` for each rule the data breaks (ML-1.4)."""
    params = spec.typed_params()
    problems: list[tuple[str, str]] = []
    if isinstance(params, KnnParams) and params.n_neighbors > facts.train_rows:
        problems.append(
            (
                "n_neighbors",
                f"{params.n_neighbors} neighbours, but train has {facts.train_rows} rows",
            )
        )
    if isinstance(params, PcaParams) and params.n_components > min(
        facts.encoded_width, facts.train_rows
    ):
        problems.append(
            (
                "n_components",
                f"{params.n_components} components, but the encoded input has "
                f"{facts.encoded_width} features and train {facts.train_rows} rows",
            )
        )
    if isinstance(params, KMeansParams) and params.n_clusters > facts.train_rows:
        problems.append(
            ("n_clusters", f"{params.n_clusters} clusters, but train has {facts.train_rows} rows")
        )
    return problems


def schema() -> dict[str, Any]:
    """Every estimator's title, tasks and parameter schema, for SCR-09's forms (UI-2.3)."""
    return {
        name: {
            "title": e.title,
            "tasks": list(e.tasks),
            "schema": e.params.model_json_schema(),
        }
        for name, e in ESTIMATORS.items()
    }


def defaults(estimator: str) -> Mapping[str, Any]:
    return ESTIMATORS[estimator].params().model_dump(mode="json")
