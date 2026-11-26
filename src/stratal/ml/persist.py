"""Classical models saved with skops, never unpickled (ML-1.5, NFR-8, ADR-0019 rule 18).

Workers only.

``skops.io`` writes a model as a zip of JSON and arrays, and loads it only if
every type in it is trusted. We trust an explicit list: the pipeline, the
preprocessing steps and the estimators of :mod:`stratal.ml.pipeline`, plus the
NumPy and scikit-learn internals they hold. A file holding any other type is
refused as ``weights_unreadable``, as a planted pickle is for networks.
"""

import importlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

from stratal.core.errors import WeightsUnreadable

MODEL_FILE = "model.skops"

TRUSTED: tuple[str, ...] = (
    # Python and NumPy containers and scalars.
    "builtins.dict",
    "builtins.list",
    "builtins.slice",
    "builtins.str",
    "builtins.tuple",
    "numpy.dtype",
    "numpy.float64",
    "numpy.int64",
    "numpy.ndarray",
    "numpy.uint64",
    "numpy.random._generator.Generator",
    # The pipeline and its preprocessing (ADR-0019 rule 17).
    "sklearn.pipeline.Pipeline",
    "sklearn.compose._column_transformer.ColumnTransformer",
    "sklearn.impute._base.SimpleImputer",
    "sklearn.preprocessing._data.StandardScaler",
    "sklearn.preprocessing._encoders.OneHotEncoder",
    "sklearn.preprocessing._encoders.OrdinalEncoder",
    "sklearn.preprocessing._label.LabelEncoder",
    # The estimators (ML-1.1, ML-1.2) and what they hold.
    "sklearn.linear_model._base.LinearRegression",
    "sklearn.linear_model._ridge.Ridge",
    "sklearn.linear_model._coordinate_descent.Lasso",
    "sklearn.linear_model._logistic.LogisticRegression",
    "sklearn.svm._classes.SVC",
    "sklearn.svm._classes.SVR",
    "sklearn.tree._classes.DecisionTreeClassifier",
    "sklearn.tree._classes.DecisionTreeRegressor",
    "sklearn.tree._tree.Tree",
    "sklearn.ensemble._forest.RandomForestClassifier",
    "sklearn.ensemble._forest.RandomForestRegressor",
    "sklearn.ensemble._hist_gradient_boosting.gradient_boosting.HistGradientBoostingClassifier",
    "sklearn.ensemble._hist_gradient_boosting.gradient_boosting.HistGradientBoostingRegressor",
    "sklearn.ensemble._hist_gradient_boosting.binning._BinMapper",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
    "sklearn._loss.loss.HalfSquaredError",
    "sklearn._loss.loss.HalfBinomialLoss",
    "sklearn._loss.loss.HalfMultinomialLoss",
    "sklearn._loss._loss.CyHalfSquaredError",
    "sklearn._loss._loss.CyHalfBinomialLoss",
    "sklearn._loss._loss.CyHalfMultinomialLoss",
    "sklearn._loss.link.IdentityLink",
    "sklearn._loss.link.LogitLink",
    "sklearn._loss.link.MultinomialLogit",
    "sklearn._loss.link.Interval",
    "sklearn.neighbors._classification.KNeighborsClassifier",
    "sklearn.neighbors._regression.KNeighborsRegressor",
    "sklearn.neighbors._kd_tree.KDTree",
    "sklearn.neighbors._ball_tree.BallTree",
    "sklearn.metrics._dist_metrics.EuclideanDistance64",
    "sklearn.metrics._dist_metrics.ManhattanDistance64",
    "sklearn.naive_bayes.GaussianNB",
    "sklearn.cluster._kmeans.KMeans",
    "sklearn.cluster._dbscan.DBSCAN",
    "sklearn.cluster._hdbscan.hdbscan.HDBSCAN",
    "sklearn.decomposition._pca.PCA",
)
"""Every type a Stratal classical pipeline may contain. Extending it is a reviewed change."""


def _io() -> Any:
    return importlib.import_module("skops.io")


def save(model: Any, folder: Path) -> int:
    """Write ``model`` as ``folder/model.skops`` (temporary name, then rename); its size."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / MODEL_FILE
    staging = folder / f".{MODEL_FILE}.part"
    _io().dump(model, staging)
    staging.replace(final)
    return final.stat().st_size


def types_in(path: Path) -> set[str]:
    """Every ``module.Class`` a skops archive names, read from its ``schema.json``.

    skops trusts scikit-learn's own estimators by default, so its own list of
    untrusted types would not show, say, an estimator Stratal never builds. Reading
    the schema lets :data:`TRUSTED` decide.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            fields = cast(dict[str, object], node)
            module, name = fields.get("__module__"), fields.get("__class__")
            if isinstance(module, str) and isinstance(name, str):
                found.add(f"{module}.{name}")
            for value in fields.values():
                walk(value)
        elif isinstance(node, list):
            for value in cast(list[object], node):
                walk(value)

    with zipfile.ZipFile(path) as archive:
        walk(json.loads(archive.read("schema.json")))
    return found


def untrusted_types(path: Path) -> list[str]:
    """The types in ``path`` that are not on :data:`TRUSTED`."""
    return sorted(types_in(path) - set(TRUSTED))


def load(path: Path) -> Any:
    """Load a classical model, refusing any type not on :data:`TRUSTED` (NFR-8)."""
    try:
        extra = untrusted_types(path)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:  # not a skops archive
        raise _unreadable(path, f"{type(exc).__name__}: {exc}") from exc
    if extra:
        raise _unreadable(path, f"it holds types Stratal does not trust: {', '.join(extra[:5])}")
    return _io().load(path, trusted=list(TRUSTED))


def _unreadable(path: Path, why: str) -> WeightsUnreadable:
    return WeightsUnreadable(
        f"The model file {path.name} cannot be loaded safely: {why}"[:500],
        suggested_action="Re-train the version; never replace registry files by hand (NFR-8).",
    )
