"""ML-1.4: invalid parameter combinations are rejected, each rule with its own test.

Plain Python (``stratal.ml.params``), so these run in the dev image and in the API.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from stratal.ml.params import (
    ESTIMATORS,
    ClassicalConfig,
    DataFacts,
    SklearnSpec,
    check_data,
    check_task,
    schema,
    task_for,
)


def _spec(estimator: str, **params: Any) -> SklearnSpec:
    return SklearnSpec(estimator=estimator, params=params)  # type: ignore[arg-type]


def _refused(estimator: str, match: str, **params: Any) -> None:
    with pytest.raises(ValidationError, match=match):
        _spec(estimator, **params)


def test_ML_1_4_l1_needs_liblinear_or_saga() -> None:
    _refused("logistic_regression", "penalty l1 needs solver liblinear or saga", penalty="l1")
    assert _spec("logistic_regression", penalty="l1", solver="saga")


def test_ML_1_4_elasticnet_needs_saga() -> None:
    _refused(
        "logistic_regression",
        "needs solver saga",
        penalty="elasticnet",
        solver="lbfgs",
        l1_ratio=0.5,
    )


def test_ML_1_4_elasticnet_needs_an_l1_ratio() -> None:
    _refused("logistic_regression", "needs an l1_ratio", penalty="elasticnet", solver="saga")
    assert _spec("logistic_regression", penalty="elasticnet", solver="saga", l1_ratio=0.5)


def test_ML_1_4_l1_ratio_is_only_for_elasticnet() -> None:
    _refused("logistic_regression", "l1_ratio is only for penalty elasticnet", l1_ratio=0.3)


def test_ML_1_4_dual_is_only_for_l2_with_liblinear() -> None:
    _refused("logistic_regression", "dual is only for", dual=True)
    assert _spec("logistic_regression", dual=True, solver="liblinear")


def test_ML_1_4_liblinear_cannot_fit_without_a_penalty() -> None:
    _refused(
        "logistic_regression", "cannot fit without a penalty", penalty="none", solver="liblinear"
    )


def test_ML_1_4_degree_is_only_for_the_poly_kernel() -> None:
    _refused("svm", "degree is only for kernel poly", degree=5)
    assert _spec("svm", kernel="poly", degree=5)


def test_ML_1_4_ranges_and_unknown_fields_are_refused_naming_the_field() -> None:
    _refused("random_forest", "n_estimators", n_estimators=0)
    _refused("dbscan", "eps", eps=0)
    _refused("kmeans", "n_clusters", n_clusters=1)
    _refused("knn", "bogus", bogus=1)


def test_ML_1_4_an_estimator_must_suit_the_dataset_task() -> None:
    assert check_task("linear", "classification") is not None
    assert check_task("logistic_regression", "regression") is not None
    assert check_task("naive_bayes", "regression") is not None
    assert check_task("random_forest", "regression") is None
    assert check_task("kmeans", "classification") is None, "unsupervised: any table"
    assert task_for("pca", "classification") == "projection"
    assert task_for("svm", "regression") == "regression"


def test_ML_1_4_rules_that_depend_on_the_data() -> None:
    facts = DataFacts(train_rows=10, encoded_width=4)
    assert check_data(_spec("knn", n_neighbors=11), facts)[0][0] == "n_neighbors"
    assert check_data(_spec("pca", n_components=5), facts)[0][0] == "n_components"
    assert check_data(_spec("kmeans", n_clusters=12), facts)[0][0] == "n_clusters"
    assert check_data(_spec("pca", n_components=4), facts) == []


def test_ML_1_1_every_listed_estimator_has_a_schema_and_defaults() -> None:
    names = set(schema())
    assert names == set(ESTIMATORS)
    assert {"linear", "ridge", "lasso", "logistic_regression", "svm", "decision_tree"} <= names
    assert {"random_forest", "gradient_boosting", "knn", "naive_bayes"} <= names  # ML-1.1
    assert {"kmeans", "dbscan", "hdbscan", "pca"} <= names  # ML-1.2
    resolved = ClassicalConfig(model=_spec("random_forest")).model.resolved()
    assert resolved.params["n_estimators"] == 100 and resolved.params["max_features"] == "sqrt"
    assert _spec("random_forest").identity() == {"kind": "sklearn", "estimator": "random_forest"}
