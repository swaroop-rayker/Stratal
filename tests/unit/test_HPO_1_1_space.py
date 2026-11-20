"""HPO-1.1: the search space builds from tunable markers, checked against each field's own
range and the fixed fields (ADR-0018 rule 2). Plain Python: runs in dev and in the API."""

from typing import Any

import pytest
from pydantic import ValidationError

from stratal.hpo.space import Marker, apply, check_space, get_at, set_at
from stratal.ml.params import ClassicalConfig, SklearnSpec

FOREST: dict[str, Any] = {
    "classical": ClassicalConfig(
        model=SklearnSpec(estimator="random_forest").resolved()
    ).model_dump(mode="json")
}
LOGISTIC: dict[str, Any] = {
    "classical": ClassicalConfig(
        model=SklearnSpec(estimator="logistic_regression").resolved()
    ).model_dump(mode="json")
}
NETWORK: dict[str, Any] = {
    "config": {
        "model": {
            "name": "graph",
            "graph": {
                "label": "m",
                "nodes": [{"id": "d1", "layer": {"type": "linear", "out_features": 16}}],
                "edges": [["input", "d1"], ["d1", "output"]],
            },
        },
        "learning_rate": 0.001,
        "batch_size": 32,
        "device": "cpu",
    }
}


def _valid_classical(document: dict[str, Any]) -> str | None:
    try:
        ClassicalConfig.model_validate(document["classical"])
    except ValidationError as exc:
        return str(exc.errors()[0]["msg"])
    return None


def _marker(path: str, kind: str, **fields: Any) -> Marker:
    return Marker(path=path, type=kind, **fields)  # type: ignore[arg-type]


def test_HPO_1_1_markers_take_ranges_with_a_scale_or_choices() -> None:
    assert _marker("/a", "log_float", low=1e-4, high=1e-1).ends() == [1e-4, 1e-1]
    assert _marker("/a", "int", low=2, high=20).ends() == [2, 20]
    assert _marker("/a", "categorical", choices=["sqrt", "log2"]).ends() == ["sqrt", "log2"]
    for bad in (
        {"type": "log_float", "low": 0, "high": 1},
        {"type": "float", "low": 2, "high": 1},
        {"type": "int", "low": 1.5, "high": 4},
        {"type": "categorical", "choices": ["a", "a"]},
        {"type": "categorical", "low": 1, "high": 2, "choices": ["a", "b"]},
        {"type": "float", "low": 1, "high": 2, "choices": [1, 2]},
    ):
        with pytest.raises(ValidationError):
            Marker.model_validate({"path": "/a", **bad})


def test_HPO_1_1_a_space_on_a_classical_model_builds_and_applies() -> None:
    markers = [
        _marker("/classical/model/params/n_estimators", "int", low=50, high=500, step=10),
        _marker("/classical/model/params/max_depth", "int", low=2, high=20),
        _marker("/classical/model/params/max_features", "categorical", choices=["sqrt", "log2"]),
    ]

    assert check_space(markers, FOREST, _valid_classical) == []
    tuned = apply(FOREST, {m.path: v for m, v in zip(markers, (120, 7, "log2"), strict=True)})
    assert tuned["classical"]["model"]["params"]["max_depth"] == 7
    assert FOREST["classical"]["model"]["params"]["max_depth"] is None, "the base is untouched"


@pytest.mark.parametrize(
    ("marker", "message"),
    [
        (
            ("/classical/model/params/n_estimators", "int", {"low": 0, "high": 10}),
            "outside its field",
        ),
        (("/classical/model/params/bogus", "int", {"low": 1, "high": 10}), "no parameter"),
        (("/classical/seed", "int", {"low": 1, "high": 10}), "not a path a study can tune"),
        (("/config/device", "categorical", {"choices": ["cpu", "gpu"]}), "not a tunable"),
    ],
)
def test_HPO_1_1_a_marker_outside_its_field_or_on_no_field_is_refused(
    marker: tuple[str, str, dict[str, Any]], message: str
) -> None:
    path, kind, fields = marker
    base = NETWORK if path.startswith("/config") else FOREST

    [problem] = check_space([_marker(path, kind, **fields)], base, lambda _doc: None)

    assert message in problem["msg"] and problem["loc"] == ["space", 0, "path"]


def test_HPO_1_1_a_marker_no_value_of_which_can_be_valid_is_refused() -> None:
    # The solver is fixed to lbfgs: neither l1 nor elasticnet can ever be valid with it.
    hopeless = _marker(
        "/classical/model/params/penalty", "categorical", choices=["l1", "elasticnet"]
    )
    partly = _marker("/classical/model/params/penalty", "categorical", choices=["l1", "l2"])

    [problem] = check_space([hopeless], LOGISTIC, _valid_classical)

    assert "no value is valid with the fixed fields" in problem["msg"]
    assert check_space([partly], LOGISTIC, _valid_classical) == [], "l1 fails as a trial instead"


def test_HPO_1_1_a_network_space_reaches_training_fields_and_layer_fields() -> None:
    markers = [
        _marker("/config/learning_rate", "log_float", low=1e-4, high=1e-1),
        _marker("/config/batch_size", "categorical", choices=[16, 32, 64]),
        _marker("/config/model/graph/nodes/d1/layer/out_features", "int", low=8, high=128),
    ]

    assert check_space(markers, NETWORK, lambda _doc: None) == []
    assert get_at(NETWORK, "/config/model/graph/nodes/d1/layer/out_features") == 16
    tuned = set_at(NETWORK, "/config/model/graph/nodes/d1/layer/out_features", 64)
    assert tuned["config"]["model"]["graph"]["nodes"][0]["layer"]["out_features"] == 64


def test_HPO_1_1_a_field_tuned_twice_is_refused() -> None:
    marker = _marker("/classical/model/params/max_depth", "int", low=2, high=5)

    problems = check_space([marker, marker], FOREST, _valid_classical)

    assert [p["loc"] for p in problems] == [["space", 1, "path"]]
