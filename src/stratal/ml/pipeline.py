"""Classical models as scikit-learn pipelines (ML-1.1 to ML-1.3, ADR-0019 rule 17). Workers only.

One ``Pipeline``: a ``ColumnTransformer`` (numeric columns: median imputing then
standard scaling; categorical columns: a constant for missing values then
one-hot encoding that ignores categories train never had), then the estimator.
It is fitted on the train split only; validation and test are only scored
(ML-1.3). The estimator's name maps to scikit-learn's classifier or regressor by
the task; the unsupervised four ignore the target.

scikit-learn ships without complete type information, so it is reached through
``Any`` here; nothing scikit-learn returns leaves this module untyped.
"""

import importlib
import math
from dataclasses import dataclass
from typing import Any

from stratal.core.errors import StratalError
from stratal.eval import task_metrics
from stratal.ml.data import TabularData
from stratal.ml.params import SklearnSpec, Task, task_for


def _sk(module: str) -> Any:
    return importlib.import_module(f"sklearn.{module}")


def _weight(value: str) -> str | None:
    return None if value == "none" else value


def build_estimator(spec: SklearnSpec, task: Task, seed: int) -> Any:
    """The scikit-learn estimator for ``spec`` on ``task``, with its parameters."""
    p = spec.typed_params().model_dump()
    name = spec.estimator
    classify = task == "classification"
    if name == "linear":
        return _sk("linear_model").LinearRegression(fit_intercept=p["fit_intercept"])
    if name == "ridge":
        return _sk("linear_model").Ridge(alpha=p["alpha"], fit_intercept=p["fit_intercept"])
    if name == "lasso":
        return _sk("linear_model").Lasso(
            alpha=p["alpha"], max_iter=p["max_iter"], random_state=seed
        )
    if name == "logistic_regression":
        # Our penalty, in scikit-learn 1.8's terms: it deprecated 'penalty' for l1_ratio
        # (0: l2, 1: l1, between: elasticnet) and C = inf for no penalty.
        penalty = p["penalty"]
        l1_ratio = {"l2": 0.0, "l1": 1.0, "none": 0.0}.get(penalty, p["l1_ratio"])
        return _sk("linear_model").LogisticRegression(
            C=math.inf if penalty == "none" else p["C"],
            solver=p["solver"],
            l1_ratio=l1_ratio,
            dual=p["dual"],
            max_iter=p["max_iter"],
            class_weight=_weight(p["class_weight"]),
            random_state=seed,
        )
    if name == "svm":
        common = {"C": p["C"], "kernel": p["kernel"], "gamma": p["gamma"], "degree": p["degree"]}
        if classify:
            # No probability estimates: scikit-learn 1.9 deprecated SVC(probability=True);
            # /predict returns the class with a null probability (ADR-0019 rule 21).
            return _sk("svm").SVC(
                **common, class_weight=_weight(p["class_weight"]), random_state=seed
            )
        return _sk("svm").SVR(**common)
    if name == "decision_tree":
        criterion = p["criterion"]
        if criterion == "default":
            criterion = "gini" if classify else "squared_error"
        if classify != (criterion in ("gini", "entropy")):
            raise StratalError(
                f"criterion {criterion} is for {'regression' if classify else 'classification'}.",
                suggested_action="Choose gini or entropy to classify, squared_error or "
                "absolute_error for regression, or leave it at default.",
            )
        cls = "DecisionTreeClassifier" if classify else "DecisionTreeRegressor"
        extra = {"class_weight": _weight(p["class_weight"])} if classify else {}
        return getattr(_sk("tree"), cls)(
            max_depth=p["max_depth"],
            min_samples_split=p["min_samples_split"],
            min_samples_leaf=p["min_samples_leaf"],
            criterion=criterion,
            random_state=seed,
            **extra,
        )
    if name == "random_forest":
        cls = "RandomForestClassifier" if classify else "RandomForestRegressor"
        extra = {"class_weight": _weight(p["class_weight"])} if classify else {}
        features = 1.0 if p["max_features"] == "1.0" else p["max_features"]
        return getattr(_sk("ensemble"), cls)(
            n_estimators=p["n_estimators"],
            max_depth=p["max_depth"],
            min_samples_leaf=p["min_samples_leaf"],
            max_features=features,
            random_state=seed,
            n_jobs=1,
            **extra,
        )
    if name == "gradient_boosting":
        cls = "HistGradientBoostingClassifier" if classify else "HistGradientBoostingRegressor"
        return getattr(_sk("ensemble"), cls)(
            learning_rate=p["learning_rate"],
            max_iter=p["max_iter"],
            max_depth=p["max_depth"],
            max_leaf_nodes=p["max_leaf_nodes"],
            min_samples_leaf=p["min_samples_leaf"],
            l2_regularization=p["l2_regularization"],
            random_state=seed,
        )
    if name == "knn":
        cls = "KNeighborsClassifier" if classify else "KNeighborsRegressor"
        return getattr(_sk("neighbors"), cls)(
            n_neighbors=p["n_neighbors"], weights=p["weights"], p=p["p"]
        )
    if name == "naive_bayes":
        return _sk("naive_bayes").GaussianNB(var_smoothing=p["var_smoothing"])
    if name == "kmeans":
        return _sk("cluster").KMeans(
            n_clusters=p["n_clusters"],
            n_init=p["n_init"],
            max_iter=p["max_iter"],
            random_state=seed,
        )
    if name == "dbscan":
        return _sk("cluster").DBSCAN(eps=p["eps"], min_samples=p["min_samples"])
    if name == "hdbscan":
        return _sk("cluster").HDBSCAN(
            min_cluster_size=p["min_cluster_size"], min_samples=p["min_samples"], copy=True
        )
    if name == "pca":
        return _sk("decomposition").PCA(n_components=p["n_components"], random_state=seed)
    raise AssertionError(f"no estimator {name}")


def preprocessor(
    numeric: list[str],
    categorical: list[str],
    ordinal: list[tuple[str, tuple[str, ...]]] | None = None,
) -> Any:
    """Imputing, scaling and encoding, fitted with the pipeline on train only (ML-1.3).

    An ``ordinal`` column (DATA-7.3) is encoded in its given order; a value outside the
    order, or missing, becomes -1 and is then imputed with the median position and
    scaled like a number (ADR-0019 rule 29)."""
    pipeline, compose = _sk("pipeline"), _sk("compose")
    impute, prep = _sk("impute"), _sk("preprocessing")
    parts: list[tuple[str, Any, list[str]]] = []
    if numeric:
        parts.append(
            (
                "numeric",
                pipeline.Pipeline(
                    [
                        ("impute", impute.SimpleImputer(strategy="median")),
                        ("scale", prep.StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        parts.append(
            (
                "categorical",
                pipeline.Pipeline(
                    [
                        ("impute", impute.SimpleImputer(strategy="constant", fill_value="missing")),
                        (
                            "encode",
                            prep.OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    ]
                ),
                categorical,
            )
        )
    for name, order in ordinal or []:
        parts.append(
            (
                f"ordinal:{name}",
                pipeline.Pipeline(
                    [
                        ("missing", impute.SimpleImputer(strategy="constant", fill_value="")),
                        (
                            "encode",
                            prep.OrdinalEncoder(
                                categories=[list(order)],
                                handle_unknown="use_encoded_value",
                                unknown_value=-1,
                            ),
                        ),
                        ("impute", impute.SimpleImputer(missing_values=-1, strategy="median")),
                        ("scale", prep.StandardScaler()),
                    ]
                ),
                [name],
            )
        )
    return compose.ColumnTransformer(parts)


def build_pipeline(spec: SklearnSpec, data: TabularData, seed: int) -> Any:
    task = task_for(spec.estimator, data.task)
    return _sk("pipeline").Pipeline(
        [
            ("preprocess", preprocessor(data.numeric, data.categorical, data.ordinal)),
            ("model", build_estimator(spec, task, seed)),
        ]
    )


@dataclass(frozen=True)
class Scores:
    """Scalars for the run's metrics and events; ``detail`` for the version's record."""

    scalars: dict[str, float | int | str]
    detail: dict[str, Any]


def score(pipeline: Any, data: TabularData, spec: SklearnSpec, splits: tuple[str, ...]) -> Scores:
    """EVAL-1.2's metric set for the run's task, on each split in ``splits``."""
    task = task_for(spec.estimator, data.task)
    scalars: dict[str, float | int | str] = {"task": task}
    detail: dict[str, Any] = {"task": task}
    if task in ("classification", "regression"):
        for split in splits:
            part = data.splits[split]
            if not len(part):
                continue
            predicted = [
                p.item() if hasattr(p, "item") else p for p in pipeline.predict(data.arrow(split))
            ]
            if task == "classification":
                truth = [str(v) for v in part.target]
                guess = [str(v) for v in predicted]
                labels = sorted({*truth, *guess, *data.classes})
                m = task_metrics.classification(truth, guess, labels=labels)
                scalars[f"{split}_accuracy"] = round(m.accuracy, 6)
                scalars[f"{split}_macro_f1"] = round(m.macro_f1, 6)
                detail[split] = {
                    "accuracy": m.accuracy,
                    "macro_f1": m.macro_f1,
                    "per_class_f1": {str(k): v for k, v in m.per_class_f1.items()},
                    "confusion_matrix": m.confusion_matrix,
                    "labels": [str(label) for label in m.labels],
                    "rows": len(truth),
                }
            else:
                r = task_metrics.regression(
                    [float(v) for v in part.target], [float(v) for v in predicted]
                )
                scalars.update(
                    {
                        f"{split}_mse": round(r.mse, 6),
                        f"{split}_rmse": round(math.sqrt(r.mse), 6),
                        f"{split}_mae": round(r.mae, 6),
                        f"{split}_r2": round(r.r2, 6),
                    }
                )
                detail[split] = {"mse": r.mse, "mae": r.mae, "r2": r.r2, "rows": len(part)}
        return Scores(scalars, detail)

    encoded = pipeline.named_steps["preprocess"].transform(data.arrow("train"))
    model = pipeline.named_steps["model"]
    if task == "projection":
        ratio = [float(v) for v in model.explained_variance_ratio_]
        scalars["explained_variance"] = round(sum(ratio), 6)
        detail["train"] = {"explained_variance_ratio": ratio, "components": len(ratio)}
        return Scores(scalars, detail)
    labels = [int(v) for v in model.labels_]
    clusters = sorted({v for v in labels if v != -1})
    noise = sum(1 for v in labels if v == -1)
    scalars.update({"clusters": len(clusters), "noise_points": noise})
    train: dict[str, Any] = {"clusters": len(clusters), "noise_points": noise, "rows": len(labels)}
    detail["train"] = train
    kept = [i for i, v in enumerate(labels) if v != -1]
    if 2 <= len(clusters) < len(kept):
        m = task_metrics.clustering(
            [[float(x) for x in encoded[i]] for i in kept], [labels[i] for i in kept]
        )
        scalars["silhouette"] = round(m.silhouette, 6)
        scalars["davies_bouldin"] = round(m.davies_bouldin, 6)
        train.update(silhouette=m.silhouette, davies_bouldin=m.davies_bouldin)
    else:
        train["note"] = (
            f"{len(clusters)} clusters among {len(kept)} non-noise points: silhouette and "
            "Davies–Bouldin need 2 to points − 1 clusters"
        )
    return Scores(scalars, detail)


def fit(pipeline: Any, data: TabularData, spec: SklearnSpec) -> Any:
    """Fit on the train split only (ML-1.3). Supervised models see the target."""
    task = task_for(spec.estimator, data.task)
    train = data.splits["train"]
    if task in ("classification", "regression"):
        target = (
            [str(v) for v in train.target]
            if task == "classification"
            else [float(v) for v in train.target]
        )
        return pipeline.fit(data.arrow("train"), target)
    return pipeline.fit(data.arrow("train"))
