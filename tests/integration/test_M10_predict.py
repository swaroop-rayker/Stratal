# pyright: basic, reportMissingImports=false
"""REG-1.4 and ML-1.5 for tables and texts: ``/predict`` with a record or a text (ADR-0019
rules 20 and 21), through the API and the inference app in this process.

Classical models are served from their skops files on the CPU without the GPU
lease; table and text networks with their own fitted encoding or vocabulary.
Needs PyTorch, scikit-learn and skops: the GPU worker's image (marker ``torch``).
"""

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

torch = pytest.importorskip("torch")
pytest.importorskip("sklearn")
pytest.importorskip("skops")

from stratal.api import models as models_api  # noqa: E402
from stratal.build import layers as L  # noqa: E402
from stratal.build.spec import GraphSpec, Node  # noqa: E402
from stratal.core.bus import Bus  # noqa: E402
from stratal.core.settings import get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.inference.app import create_inference_app  # noqa: E402
from stratal.inference.holder import ModelHolder, decode_image  # noqa: E402
from stratal.testing.fake_models import FakeGpu  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402
from stratal.testing.no_pickle import forbid_unpickling  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402
from stratal.train.memory import TrainingState  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]


class FakeGpuSide:
    """A card that is "available", with memory counted by FakeGpu; tensors stay on the CPU."""

    tensor_device = "cpu"

    def __init__(self) -> None:
        self.gpu = FakeGpu()

    def available(self) -> bool:
        return True

    def backend(self) -> FakeGpu:
        return self.gpu

    def prepare(self) -> None:
        pass

    def to_device(self, model: Any) -> Any:
        self.gpu.hold(model, 300.0)
        return model


class ClosingState(TrainingState):
    def close(self) -> None:
        self.model = None
        self.optimizer = None


def _chain(label: str, *layers: Any) -> GraphSpec:
    ids = [f"n{i}" for i in range(len(layers))]
    nodes = [Node(id=i, layer=layer) for i, layer in zip(ids, layers, strict=True)]
    path = ["input", *ids, "output"]
    return GraphSpec(label=label, nodes=nodes, edges=list(itertools.pairwise(path)))


MLP = _chain("tab_mlp", L.Linear(out_features=16), L.ReLU())
LSTM = _chain("text_lstm", L.Embedding(embedding_dim=16, padding_idx=0), L.LSTM(hidden_size=16))


class Service:
    def __init__(self, runner: Runner, bus: Bus) -> None:
        self.api = PipelineApi(runner)
        self.runner = runner
        settings = get_settings()
        self.holder = ModelHolder(
            engine=get_engine(),
            settings=settings,
            bus=bus,
            gpu=FakeGpuSide(),
            state_cls=ClosingState,
        )
        inference = TestClient(
            create_inference_app(
                self.holder, lambda **kw: decode_image(data_dir=settings.data_dir, **kw)
            ),
            raise_server_exceptions=False,
        )

        def forward(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            response = inference.post("/predict", json=body)
            return response.status_code, response.json()

        self.api.client.app.dependency_overrides[models_api.get_forward] = lambda: forward  # type: ignore[attr-defined]

    def pipeline(self, body: dict[str, Any]) -> str:
        job = self.api.run(body)
        assert job["state"] == "completed", job
        return str(job["output_version_id"])

    def train(self, version: str, **request: Any) -> str:
        response = self.api.client.post(
            "/api/v1/jobs", json={"kind": "train", "dataset_version": version, **request}
        )
        assert response.status_code == 202, response.text
        self.runner.queue.extend(self.api.queued)
        self.api.queued.clear()
        self.runner.run_queued()
        self.runner.collect()
        job = self.api.client.get(f"/api/v1/jobs/{response.json()['id']}").json()
        assert job["state"] == "completed", job
        return str(job["stages"][0]["metrics"]["model_version_id"])

    def classical(self, version: str, estimator: str, **params: Any) -> str:
        model = {"kind": "sklearn", "estimator": estimator, "params": params}
        return self.train(version, classical={"model": model})

    def network(self, version: str, graph: GraphSpec, **config: Any) -> str:
        model = {"name": "graph", "graph": graph.model_dump(mode="json")}
        body = {
            "model": model,
            "epochs": 30,
            "batch_size": 16,
            "learning_rate": 0.01,
            "device": "cpu",
        }
        return self.train(version, config={**body, **config})

    def predict(self, version_id: str, **body: Any) -> Any:
        return self.api.client.post(f"/api/v1/models/{version_id}/predict", json=body)


def _table(root: Path, slug: str, *, regression: bool = False) -> dict[str, Any]:
    root.mkdir(parents=True)
    lines = ["size,colour,y"]
    for i in range(120):
        size = float(i % 20)
        colour = ("red", "green", "blue")[i % 3]
        y = (
            f"{2 * size + (5 if colour == 'red' else 0):.1f}"
            if regression
            else ("big" if size > 9 else "small")
        )
        lines.append(f"{size},{colour},{y}")
    (root / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "dataset": slug,
        "source": {"path": str(root)},
        "dataset_type": "tabular",
        "stages": {"ingest": {"table": {"target": "y"}}},
    }


@pytest.fixture
def service(runner: Runner) -> Iterator[Service]:
    bus = Bus(get_settings().redis_url, timeout_s=2.0)
    made = Service(runner, bus)
    yield made
    made.holder.shutdown()  # unloads and gives back the GPU lease, which later tests need


def test_REG_1_4_a_classical_model_predicts_a_record_on_the_cpu_without_the_lease(
    service: Service, tmp_path: Path
) -> None:
    version = service.pipeline(_table(tmp_path / "t", "m10pred"))
    model = service.classical(version, "random_forest", n_estimators=30)

    with forbid_unpickling() as attempts:
        response = service.predict(model, record={"size": 17.0, "colour": "red"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert attempts == []
    assert (body["task"], body["device"], body["top"][0]["class"]) == (
        "classification",
        "cpu",
        "big",
    )
    assert 0.5 < body["top"][0]["probability"] <= 1.0
    assert not service.holder.status()["lease_held"], "a classical model never takes the GPU"
    imputed = service.predict(model, record={"size": None, "colour": None})
    assert imputed.status_code == 200, "nulls are imputed (ML-1.3)"


@pytest.mark.parametrize(
    ("record", "field"),
    [
        ({"size": 3.0}, "colour"),
        ({"size": 3.0, "colour": "red", "y": "big"}, "y"),
        ({"size": 3.0, "colour": "red", "weight": 1}, "weight"),
        ({"size": "large", "colour": "red"}, "size"),
    ],
)
def test_REG_1_4_a_record_that_does_not_fit_the_table_is_refused_naming_the_field(
    service: Service, tmp_path: Path, record: dict[str, Any], field: str
) -> None:
    version = service.pipeline(_table(tmp_path / "t", "m10bad"))
    model = service.classical(version, "logistic_regression")

    response = service.predict(model, record=record)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "wrong_input"
    assert field in [e["loc"][-1] for e in body["errors"]]


def test_REG_1_4_the_input_must_be_the_one_the_version_takes(
    service: Service, tmp_path: Path
) -> None:
    version = service.pipeline(_table(tmp_path / "t", "m10kind"))
    model = service.classical(version, "knn")

    as_text = service.predict(model, text="a big red thing")
    two = service.predict(model, text="x", record={"size": 1.0, "colour": "red"})

    assert (as_text.status_code, as_text.json()["code"]) == (422, "wrong_input")
    assert two.status_code == 422, "exactly one input"


def test_REG_1_4_regression_clusters_and_projections_answer_in_their_own_terms(
    service: Service, tmp_path: Path
) -> None:
    numbers = service.pipeline(_table(tmp_path / "r", "m10reg", regression=True))
    classes = service.pipeline(_table(tmp_path / "c", "m10clu"))
    record = {"size": 10.0, "colour": "red"}

    value = service.predict(service.classical(numbers, "ridge"), record=record).json()
    cluster = service.predict(service.classical(classes, "kmeans", n_clusters=3), record=record)
    projection = service.predict(service.classical(classes, "pca", n_components=2), record=record)
    dbscan = service.predict(service.classical(classes, "dbscan", eps=1.0), record=record)

    assert value["task"] == "regression" and 20.0 < value["value"] < 30.0, value
    assert cluster.json()["task"] == "clustering" and cluster.json()["cluster"] in (0, 1, 2)
    assert len(projection.json()["components"]) == 2
    assert (dbscan.status_code, dbscan.json()["code"]) == (409, "not_servable")


def test_REG_1_4_table_networks_use_their_own_fitted_encoding(
    service: Service, tmp_path: Path
) -> None:
    classes = service.network(service.pipeline(_table(tmp_path / "c", "m10netc")), MLP)
    numbers = service.network(
        service.pipeline(_table(tmp_path / "r", "m10netr", regression=True)), MLP, epochs=60
    )

    big = service.predict(classes, record={"size": 18.0, "colour": "blue"}).json()
    value = service.predict(numbers, record={"size": 10.0, "colour": "red"}).json()

    assert big["top"][0]["class"] == "big", big
    assert value["task"] == "regression" and 20.0 < value["value"] < 30.0, value


def test_REG_1_4_a_text_network_tokenizes_with_its_vocabulary(
    service: Service, tmp_path: Path
) -> None:
    root = tmp_path / "x"
    root.mkdir()
    rows = [
        json.dumps(
            {
                "text": f"a {('cat', 'dog', 'bird')[i % 3]} number {i} sits here",
                "label": ("cat", "dog", "bird")[i % 3],
            }
        )
        for i in range(90)
    ]
    (root / "t.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    version = service.pipeline(
        {
            "dataset": "m10txtp",
            "source": {"path": str(root)},
            "dataset_type": "labelled_text",
            "stages": {"tokenize": {"min_freq": 1}},
        }
    )
    model = service.network(version, LSTM)

    response = service.predict(model, text="  A DOG   number 5000 sits here ")
    image = service.predict(model, blob="0" * 64)

    assert response.status_code == 200, response.text
    assert response.json()["top"][0]["class"] == "dog"
    assert (image.status_code, image.json()["code"]) == (422, "wrong_input")
