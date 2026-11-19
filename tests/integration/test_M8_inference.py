# pyright: basic, reportMissingImports=false
"""REG-1.4: ``/predict`` for registered versions, through the API and the inference app.

The inference app runs in this process, behind the API's ``forward`` dependency.
Its GPU decisions are tested with a fake card (``FakeGpuSide``), which counts
memory as the real VRAM manager sees it, and the real GPU lease in PostgreSQL;
the real card is checked in ``tests/gpu/test_REG_1_4_inference_gpu.py``. Needs
PyTorch, so it runs in the GPU worker's image.
"""

import base64
import io
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

torch = pytest.importorskip("torch")

from stratal.api import models as models_api  # noqa: E402
from stratal.core.bus import Bus  # noqa: E402
from stratal.core.settings import Settings, get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.inference.app import create_inference_app  # noqa: E402
from stratal.inference.holder import ModelHolder, decode_image  # noqa: E402
from stratal.runtime.gpu_lease import gpu_lease  # noqa: E402
from stratal.testing.fake_models import FakeGpu  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402
from stratal.testing.labelled import labelled_split  # noqa: E402
from stratal.testing.no_pickle import forbid_unpickling, plant_pickle  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402
from stratal.train.memory import TrainingState  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

CONFIG: dict[str, Any] = {
    "model": {"name": "mlp"},
    "epochs": 1,
    "batch_size": 8,
    "device": "cpu",
    "loader_workers": 0,
    "seed": 3,
}


class FakeGpuSide:
    """A card that is "available", with memory counted by :class:`FakeGpu`; tensors stay
    on the CPU."""

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
    """What the owner's ``close()`` must do, for the parts of a model inference holds."""

    def close(self) -> None:
        self.model = None
        self.optimizer = None


LEAKED: list[Any] = []


class LeakingState(TrainingState):
    """A ``close()`` that keeps the model alive: the failure behind BUG5."""

    def close(self) -> None:
        LEAKED.append(self.model)
        self.model = None


class Service:
    """The API, the inference app behind it, and a trained version to ask."""

    def __init__(self, runner: Runner, tmp_path: Any, bus: Bus, **holder: Any) -> None:
        self.api = PipelineApi(runner)
        self.runner = runner
        self.version = labelled_split(runner, tmp_path / "src", "m8infer")
        settings = holder.pop("settings", get_settings())
        self.gpu_side = holder.get("gpu")
        self.holder = ModelHolder(engine=get_engine(), settings=settings, bus=bus, **holder)
        app = create_inference_app(
            self.holder, lambda **kw: decode_image(data_dir=settings.data_dir, **kw)
        )
        inference = TestClient(app, raise_server_exceptions=False)

        def forward(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            response = inference.post("/predict", json=body)
            return response.status_code, response.json()

        self.api.client.app.dependency_overrides[models_api.get_forward] = lambda: forward  # type: ignore[attr-defined]

    def train(self, **config: Any) -> str:
        body = {"kind": "train", "dataset_version": self.version, "config": {**CONFIG, **config}}
        response = self.api.client.post("/api/v1/jobs", json=body)
        assert response.status_code == 202, response.text
        self.runner.queue.extend(self.api.queued)
        self.api.queued.clear()
        self.runner.run_queued()
        self.runner.collect()
        job = self.api.client.get(f"/api/v1/jobs/{response.json()['id']}").json()
        assert job["state"] == "completed", job
        return str(job["model_version_id"])

    def train_pinned(self) -> str:
        """A run whose weights later runs' retention keeps (REG-2.5)."""
        version = self.train()
        pinned = self.api.client.post(f"/api/v1/model-versions/{version}/pin")
        assert pinned.status_code == 200, pinned.text
        return version

    def blob(self) -> str:
        rows = self.api.client.get(f"/api/v1/versions/{self.version}/rows?status=accepted").json()
        row = rows["items"][0]
        return str(row.get("derived_sha256") or row["sha256"])

    def predict(self, version_id: str, **body: Any) -> Any:
        return self.api.client.post(
            f"/api/v1/models/{version_id}/predict", json=body or {"blob": self.blob()}
        )


@pytest.fixture
def service(runner: Runner, tmp_path: Any, bus: Bus) -> Iterator[Service]:
    made = Service(runner, tmp_path, bus)
    yield made
    made.holder.shutdown()


@pytest.fixture
def fake_card(runner: Runner, tmp_path: Any, bus: Bus) -> Iterator[Service]:
    made = Service(runner, tmp_path, bus, gpu=FakeGpuSide(), state_cls=ClosingState)
    yield made
    made.holder.shutdown()


@pytest.fixture
def job_holds_the_gpu() -> Iterator[None]:
    ready, done = threading.Event(), threading.Event()

    def hold() -> None:
        with gpu_lease(get_engine(), holder="job_9999/train"):
            ready.set()
            done.wait(30)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert ready.wait(10)
    yield
    done.set()
    thread.join(10)


def test_REG_1_4_predict_returns_a_result_for_a_sample_input(service: Service, bus: Bus) -> None:
    version = service.train()

    response = service.predict(version)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["version_id"] == version and result["device"] in ("cpu", "gpu")
    assert [t["class"] for t in result["top"]] in (["cat", "dog"], ["dog", "cat"])
    assert sum(t["probability"] for t in result["top"]) == pytest.approx(1.0, abs=1e-4)
    assert result["load_ms"] is not None, "the first request loaded the model"
    assert (bus.get_inference_loaded() or {})["version_id"] == version, "retention keeps it"

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buffer, format="PNG")
    uploaded = service.predict(version, image_b64=base64.b64encode(buffer.getvalue()).decode())
    assert uploaded.status_code == 200, "any size: resized to the model's input"
    assert uploaded.json()["load_ms"] is None, "the model stayed loaded"


class UnwrittenState(TrainingState):
    """``close()`` as the RUN-1.4 stub was: the service cannot prove a GPU unload."""

    def close(self) -> None:
        raise NotImplementedError("RUN-1.4 v2: TrainingState.close")


@pytest.fixture
def no_close(runner: Runner, tmp_path: Any, bus: Bus) -> Iterator[Service]:
    made = Service(runner, tmp_path, bus, gpu=FakeGpuSide(), state_cls=UnwrittenState)
    yield made
    made.holder.shutdown()


def test_REG_1_4_without_a_working_close_inference_stays_on_the_cpu(no_close: Service) -> None:
    version = no_close.train()

    result = no_close.predict(version).json()

    reason = no_close.holder.status()["gpu_unavailable"]
    assert reason is not None and "RUN-1.4" in reason
    assert (result["device"], result["fallback_reason"]) == ("cpu", reason)
    card = no_close.gpu_side
    assert isinstance(card, FakeGpuSide) and card.gpu.allocated_mb() == 0, "nothing on the card"


def test_REG_1_4_while_a_job_holds_the_gpu_a_small_model_runs_on_the_cpu(
    fake_card: Service, job_holds_the_gpu: None
) -> None:
    version = fake_card.train()

    response = fake_card.predict(version)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["device"] == "cpu", "REG-1.4.AC1 while training holds the GPU"
    assert "job_9999/train holds the GPU" in result["fallback_reason"]


def test_REG_1_4_a_large_model_is_refused_with_gpu_busy_naming_the_job(
    runner: Runner, tmp_path: Any, bus: Bus, job_holds_the_gpu: None
) -> None:
    small_limit = Settings(**{**get_settings().model_dump(), "inference_cpu_max_parameters": 10})
    made = Service(
        runner, tmp_path, bus, gpu=FakeGpuSide(), state_cls=ClosingState, settings=small_limit
    )
    version = made.train()

    response = made.predict(version)

    assert response.status_code == 409, response.text
    problem = response.json()
    assert problem["code"] == "gpu_busy" and "job_9999/train" in problem["detail"]
    made.holder.shutdown()


def test_REG_1_4_on_a_free_gpu_one_model_at_a_time_and_every_unload_verified(
    fake_card: Service, bus: Bus
) -> None:
    first = fake_card.train_pinned()
    second = fake_card.train(seed=4)
    card = fake_card.holder._gpu.gpu  # type: ignore[attr-defined]

    a = fake_card.predict(first).json()
    held_by_first = card.allocated
    b = fake_card.predict(second).json()

    assert (a["device"], b["device"]) == ("gpu", "gpu")
    assert held_by_first == 300.0 and card.allocated == 300.0, "the first was unloaded"
    assert fake_card.holder.status()["loaded"]["version_id"] == second
    assert (bus.get_inference_loaded() or {})["version_id"] == second
    assert fake_card.predict(second).json()["load_ms"] is None


def test_REG_1_4_the_gpu_is_given_back_to_a_waiting_job_and_when_idle(
    fake_card: Service, bus: Bus
) -> None:
    version = fake_card.train()
    fake_card.predict(version)
    assert fake_card.holder.status()["lease_held"]
    assert fake_card.holder.tick() is None, "neither idle nor wanted yet"

    bus.set_gpu_wanted("job_0042/train", ttl_s=5)
    assert fake_card.holder.tick() == "job_0042/train is waiting for the GPU"

    status = fake_card.holder.status()
    assert not status["lease_held"] and status["loaded"] is None
    assert bus.get_inference_loaded() is None
    with gpu_lease(get_engine(), holder="job_0042/train"):  # the job gets it at once
        pass
    bus.set_gpu_wanted("nobody", ttl_s=0.001)
    fake_card.holder._settings = Settings(  # type: ignore[attr-defined]
        **{**get_settings().model_dump(), "inference_idle_release_s": 0.0}
    )
    fake_card.predict(version)
    assert fake_card.holder.tick() == "idle"
    assert not fake_card.holder.status()["lease_held"]


def test_REG_1_4_an_unload_that_leaves_vram_behind_restarts_the_service(
    runner: Runner, tmp_path: Any, bus: Bus
) -> None:
    exits: list[str] = []
    made = Service(
        runner,
        tmp_path,
        bus,
        gpu=FakeGpuSide(),
        state_cls=LeakingState,
        on_leak=lambda: exits.append("exit"),
    )
    first = made.train_pinned()
    second = made.train(seed=4)
    assert made.predict(first).json()["device"] == "gpu"

    made.predict(second)

    assert exits == ["exit"], "BUG5: memory not back at baseline, so the process exits"
    LEAKED.clear()
    made.holder.shutdown()


def test_REG_1_4_deleted_partial_and_unknown_versions_are_refused(service: Service) -> None:
    first, _ = service.train(), service.train()  # default retention deletes the first

    deleted = service.predict(first)
    unknown = service.predict("mv_000000000000")
    bad = service.predict(first, image_b64="not base64!")

    assert (deleted.status_code, deleted.json()["code"]) == (409, "weights_deleted")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "not_found")
    assert bad.status_code == 422


def test_REG_1_4_the_api_says_how_to_start_a_missing_service(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = service.train()
    del service.api.client.app.dependency_overrides[models_api.get_forward]  # type: ignore[attr-defined]
    nowhere = Settings(**{**get_settings().model_dump(), "inference_url": "http://127.0.0.1:9"})
    monkeypatch.setattr(models_api, "get_settings", lambda: nowhere)

    response = service.predict(version)

    assert response.status_code == 503
    problem = response.json()
    assert problem["code"] == "dependency_unavailable"
    assert "--profile gpu up -d inference" in problem["suggested_action"]


def _weights_folder(version_id: str) -> Any:
    with get_engine().connect() as connection:
        path = connection.execute(
            text("SELECT weights_path FROM model_versions WHERE id = :v"), {"v": version_id}
        ).scalar_one()
    return get_settings().data_dir / path


def test_NFR_8_predict_loads_a_version_without_unpickling_anything(service: Service) -> None:
    version = service.train()

    with forbid_unpickling() as attempts:
        response = service.predict(version)

    assert response.status_code == 200, response.text
    assert attempts == []


def test_NFR_8_a_pickle_planted_as_a_versions_weights_is_refused_and_never_runs(
    service: Service, tmp_path: Any
) -> None:
    version = service.train()
    marker = tmp_path / "PWNED"
    plant_pickle(_weights_folder(version) / "model.safetensors", marker)

    response = service.predict(version)

    assert response.status_code == 409, response.text
    problem = response.json()
    assert problem["code"] == "weights_unreadable", problem
    assert "Re-train" in problem["suggested_action"]
    assert not marker.exists(), "the payload never ran"
    assert service.holder.status()["loaded"] is None, "nothing stayed loaded"
