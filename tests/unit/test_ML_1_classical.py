"""ML-1.1 to ML-1.3 and ML-1.5 with scikit-learn and skops (marker ``ml``: the worker-cpu image).

A small synthetic table stands in for a dataset: two numeric columns (one with
missing values) and one categorical, a class and a numeric target.
"""
# pyright: reportMissingImports=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import math
import random
from typing import Any

import pytest

from stratal.data.dataset_types import Column, TableSchema
from stratal.ml.data import SplitTable, TabularData
from stratal.ml.params import ESTIMATORS, SklearnSpec

pytestmark = pytest.mark.ml
pytest.importorskip("sklearn")
pytest.importorskip("skops")

from stratal.ml import persist  # noqa: E402
from stratal.ml.pipeline import build_pipeline, fit, score  # noqa: E402
from stratal.testing.no_pickle import forbid_unpickling  # noqa: E402


def _table(task: str, rows: dict[str, int], seed: int = 0) -> TabularData:
    rng = random.Random(seed)
    target = Column("y", "categorical" if task == "classification" else "numeric", "target")
    schema = TableSchema(
        (Column("a", "numeric"), Column("b", "numeric"), Column("c", "categorical"), target)
    )
    splits: dict[str, SplitTable] = {}
    for split, count in rows.items():
        part = SplitTable({"a": [], "b": [], "c": []})
        for i in range(count):
            a = rng.gauss(0, 1)
            b = rng.gauss(5, 2) if i % 7 else None
            c = rng.choice(["red", "green", "blue"])
            part.columns["a"].append(a)
            part.columns["b"].append(b)
            part.columns["c"].append(c)
            signal = a + (0.5 if c == "red" else -0.5)
            part.target.append(
                ("yes" if signal > 0 else "no") if task == "classification" else 3 * signal
            )
        splits[split] = part
    return TabularData(
        schema, "classification" if task == "classification" else "regression", splits
    )


ROWS = {"train": 120, "val": 40, "test": 40}


def _supervised() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name, estimator in ESTIMATORS.items():
        for task in estimator.tasks:
            if task in ("classification", "regression"):
                found.append((name, task))
    return found


@pytest.mark.parametrize(("estimator", "task"), _supervised())
def test_ML_1_1_each_listed_model_trains_on_a_tabular_dataset(estimator: str, task: str) -> None:
    data = _table(task, ROWS)
    spec = SklearnSpec(estimator=estimator)  # type: ignore[arg-type]

    pipeline = fit(build_pipeline(spec, data, seed=0), data, spec)
    scores = score(pipeline, data, spec, ("val", "test"))

    if task == "classification":
        assert 0.0 <= float(scores.scalars["val_accuracy"]) <= 1.0
        assert scores.detail["test"]["labels"] == ["no", "yes"]
        assert sum(map(sum, scores.detail["test"]["confusion_matrix"])) == ROWS["test"]
    else:
        assert math.isfinite(float(scores.scalars["val_r2"]))
        assert float(scores.scalars["test_mse"]) >= 0


@pytest.mark.parametrize("estimator", ["kmeans", "dbscan", "hdbscan", "pca"])
def test_ML_1_2_clustering_and_pca_run_and_report_their_metrics(estimator: str) -> None:
    data = _table("classification", ROWS)
    params: dict[str, Any] = {"kmeans": {"n_clusters": 3}, "dbscan": {"eps": 1.5}}.get(
        estimator, {}
    )
    spec = SklearnSpec(estimator=estimator, params=params)  # type: ignore[arg-type]

    pipeline = fit(build_pipeline(spec, data, seed=0), data, spec)
    scores = score(pipeline, data, spec, ("train",))

    if estimator == "pca":
        assert scores.scalars["task"] == "projection"
        assert 0 < float(scores.scalars["explained_variance"]) <= 1
    else:
        assert scores.scalars["task"] == "clustering"
        assert int(scores.scalars["clusters"]) >= 1
        if estimator == "kmeans":
            assert int(scores.scalars["clusters"]) == 3
            assert -1 <= float(scores.scalars["silhouette"]) <= 1
            assert float(scores.scalars["davies_bouldin"]) >= 0


def test_ML_1_3_the_scaler_statistics_come_from_the_train_split() -> None:
    data = _table("classification", ROWS)
    # Shift val and test far away: if they leaked into the fit, the mean would move.
    for split in ("val", "test"):
        data.splits[split].columns["a"] = [v + 100 for v in data.splits[split].columns["a"]]
    spec = SklearnSpec(estimator="logistic_regression")

    pipeline = fit(build_pipeline(spec, data, seed=0), data, spec)

    numeric = pipeline.named_steps["preprocess"].named_transformers_["numeric"]
    train_a = data.splits["train"].columns["a"]
    assert numeric.named_steps["scale"].mean_[0] == pytest.approx(sum(train_a) / len(train_a))
    train_b = sorted(v for v in data.splits["train"].columns["b"] if v is not None)
    median = (train_b[len(train_b) // 2 - 1] + train_b[len(train_b) // 2]) / 2
    assert numeric.named_steps["impute"].statistics_[1] == pytest.approx(
        median if len(train_b) % 2 == 0 else train_b[len(train_b) // 2]
    )
    encoder = pipeline.named_steps["preprocess"].named_transformers_["categorical"]
    assert list(encoder.named_steps["encode"].categories_[0]) == ["blue", "green", "red"]


@pytest.mark.parametrize(
    "estimator", ["random_forest", "gradient_boosting", "knn", "svm", "pca", "kmeans"]
)
def test_ML_1_5_a_skops_save_and_load_round_trips_and_nothing_is_unpickled(
    estimator: str, tmp_path: Any
) -> None:
    data = _table("classification", ROWS)
    spec = SklearnSpec(estimator=estimator)  # type: ignore[arg-type]
    pipeline = fit(build_pipeline(spec, data, seed=0), data, spec)

    persist.save(pipeline, tmp_path)
    with forbid_unpickling() as attempts:
        loaded = persist.load(tmp_path / persist.MODEL_FILE)
    assert attempts == []

    test = data.arrow("test")
    if estimator == "pca":
        assert (loaded.transform(test) == pipeline.transform(test)).all()
    else:
        assert list(loaded.predict(test)) == list(pipeline.predict(test))


def test_ML_1_5_a_file_with_an_untrusted_type_is_refused(tmp_path: Any) -> None:
    from sklearn.dummy import DummyClassifier
    from sklearn.pipeline import Pipeline
    from skops.io import dump

    from stratal.core.errors import WeightsUnreadable

    # A trusted pipeline holding one estimator Stratal never builds.
    model = Pipeline([("model", DummyClassifier())])
    dump(model, tmp_path / persist.MODEL_FILE)

    with pytest.raises(WeightsUnreadable, match="DummyClassifier"):
        persist.load(tmp_path / persist.MODEL_FILE)
    (tmp_path / "junk.skops").write_bytes(b"not a zip")
    with pytest.raises(WeightsUnreadable):
        persist.load(tmp_path / "junk.skops")
