"""BUILD-1.2, 1.3, 1.4, 1.7, DATA-5.5, UI-2.3: the builder's API, against the real database.

Without PyTorch: the API validates specs in plain Python (ADR-0017 rule 4).
Training a graph spec is tested in ``test_M9_graph_training.py`` (torch image).
"""

from pathlib import Path
from typing import Any

import pytest

from stratal.build import layers as L
from stratal.build.spec import GraphSpec, chain
from stratal.build.templates import BUILTINS
from stratal.testing.harness import Runner
from stratal.testing.labelled import labelled_split
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]

CNN = chain("tiny_cnn", L.Conv2d(out_channels=8), L.ReLU(), L.MaxPool2d(), L.GlobalAvgPool())


def _validate(api: PipelineApi, version: str, graph: Any, **extra: Any) -> Any:
    spec: Any = graph.model_dump(mode="json") if isinstance(graph, GraphSpec) else graph
    body: dict[str, Any] = {"graph": spec}
    return api.client.post(
        "/api/v1/model-specs/validate", json={**body, "dataset_version": version, **extra}
    )


@pytest.fixture
def split(runner: Runner, tmp_path: Path) -> tuple[PipelineApi, str]:
    return PipelineApi(runner), labelled_split(runner, tmp_path / "src", "m9build")


def test_BUILD_1_3_the_dataset_fixes_the_input_and_output(split: tuple[PipelineApi, str]) -> None:
    api, version = split

    response = _validate(api, version, CNN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"]["input"] == {"kind": "image", "dims": [3, 32, 32], "vocab": None}
    assert (body["dataset"]["output_size"], body["dataset"]["classes"]) == (2, ["cat", "dog"])
    report = body["report"]
    assert report["valid"] and report["head"] == {
        "in_features": 8,
        "out_features": 2,
        "parameters": 18,
    }
    assert report["total_parameters"] == 8 * 3 * 9 + 8 + 18


def test_BUILD_1_3_changing_the_dataset_updates_both_ends(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    small = labelled_split(runner, tmp_path / "a", "m9small")
    large = labelled_split(runner, tmp_path / "b", "m9large", size=64)

    one = _validate(api, small, CNN).json()
    two = _validate(api, large, CNN).json()

    assert one["dataset"]["input"]["dims"] == [3, 32, 32]
    assert two["dataset"]["input"]["dims"] == [3, 64, 64]
    assert one["report"]["nodes"][2]["shape_text"] == "image 8×16×16"
    assert two["report"]["nodes"][2]["shape_text"] == "image 8×32×32"


def test_BUILD_1_4_a_mismatched_linear_input_comes_back_as_a_problem(
    split: tuple[PipelineApi, str],
) -> None:
    api, version = split
    spec = chain("bad", L.Flatten(), L.Linear(in_features=100))

    report = _validate(api, version, spec).json()["report"]

    assert not report["valid"]
    assert [(p["node_id"], p["code"]) for p in report["problems"]] == [("n2", "shape_mismatch")]


def test_BUILD_1_2_an_out_of_range_field_is_a_422_naming_it(split: tuple[PipelineApi, str]) -> None:
    api, version = split
    spec = CNN.model_dump(mode="json")
    spec["nodes"][0]["layer"]["kernel_size"] = 99

    response = _validate(api, version, spec)

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_error"
    assert any("kernel_size" in [str(x) for x in e["loc"]] for e in problem["errors"])


def test_DATA_5_5_a_family_the_type_does_not_take_is_a_problem(
    split: tuple[PipelineApi, str],
) -> None:
    api, version = split
    rnn = {"label": "r", "nodes": [{"id": "a", "layer": {"type": "attention"}}], "edges": []}

    report = _validate(api, version, rnn).json()["report"]

    assert "incompatible_family" in [p["code"] for p in report["problems"]]
    types = {t["name"]: t for t in api.client.get("/api/v1/dataset-types").json()}
    assert types["labelled_images"]["families"] == ["mlp", "cnn"]
    assert types["labelled_text"]["default_metrics"] == ["accuracy", "macro_f1"]


def test_BUILD_1_3_a_version_that_cannot_feed_the_builder_says_why(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    split_version = labelled_split(runner, tmp_path / "src", "m9why")
    parent = api.client.get(f"/api/v1/versions/{split_version}").json()["parent_version_id"]

    body = _validate(api, parent, CNN).json()

    assert body["report"] is None and "split" in body["dataset"]["problem"]


def test_UI_2_3_the_schema_carries_every_field_for_the_forms(
    split: tuple[PipelineApi, str],
) -> None:
    api, _ = split
    schema = api.client.get("/api/v1/model-specs/schema").json()
    conv = schema["layer"]["$defs"]["Conv2d"]["properties"]
    assert {"out_channels", "kernel_size", "stride", "padding", "dilation", "bias"} <= set(conv)
    assert schema["max_nodes"] == 200


def test_BUILD_1_7_a_saved_template_reloads_identically(split: tuple[PipelineApi, str]) -> None:
    api, _ = split
    shuffled = CNN.model_dump(mode="json")
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()

    created = api.client.post(
        "/api/v1/model-templates",
        json={"name": "tiny cnn", "dataset_type": "labelled_images", "spec": shuffled},
    )
    assert created.status_code == 201, created.text
    loaded = api.client.get(f"/api/v1/model-templates/{created.json()['id']}").json()

    assert loaded["spec"] == CNN.model_dump(mode="json"), "canonical: as built, not as listed"
    assert loaded["structure_hash"] == created.json()["structure_hash"]
    again = api.client.put(
        f"/api/v1/model-templates/{loaded['id']}", json={"spec": loaded["spec"]}
    ).json()
    assert again["spec"] == loaded["spec"] and again["structure_hash"] == loaded["structure_hash"]
    clash = api.client.post(
        "/api/v1/model-templates",
        json={"name": "tiny cnn", "dataset_type": "labelled_images", "spec": shuffled},
    )
    assert clash.status_code == 409
    assert api.client.delete(f"/api/v1/model-templates/{loaded['id']}").status_code == 204


def test_BUILD_1_7_built_in_templates_are_listed_valid_and_locked(
    split: tuple[PipelineApi, str],
) -> None:
    api, version = split
    images = api.client.get("/api/v1/model-templates?dataset_type=labelled_images").json()

    assert {t["name"] for t in images} == {
        b.name for b in BUILTINS if b.dataset_type == "labelled_images"
    }
    for template in images:
        report = _validate(api, version, template["spec"]).json()["report"]
        assert report["valid"], (template["name"], report["problems"])
    small = next(t for t in images if t["name"] == "Small CNN")
    assert api.client.delete(f"/api/v1/model-templates/{small['id']}").status_code == 409
    assert (
        api.client.put(f"/api/v1/model-templates/{small['id']}", json={"name": "x"}).status_code
        == 409
    )


def test_BUILD_1_4_a_train_request_with_a_bad_graph_is_refused(
    split: tuple[PipelineApi, str],
) -> None:
    api, version = split
    bad = chain("bad", L.Conv2d())  # ends in an image, not a vector

    response = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {"model": {"name": "graph", "graph": bad.model_dump(mode="json")}},
        },
    )

    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert errors[0]["code"] == "output_not_vector"
    assert errors[0]["loc"][:3] == ["config", "model", "graph"]
    good = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {"model": {"name": "graph", "graph": CNN.model_dump(mode="json")}},
        },
    )
    assert good.status_code == 202, good.text
