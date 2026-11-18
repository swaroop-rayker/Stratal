# pyright: basic, reportMissingImports=false
"""RUN-1.1, RUN-1.3, RUN-1.8: training jobs end to end, against the real PostgreSQL and Redis.

Needs PyTorch, so it runs in the GPU worker's image
(``docker compose run --rm worker-gpu pytest -m torch``); in the dev image it is
skipped. The runs are tiny (an MLP on 32-px synthetic images, on the CPU unless
marked ``gpu``), so they finish in seconds.
"""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

torch = pytest.importorskip("torch")

from safetensors.torch import load_file  # noqa: E402

from stratal.core.settings import get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.runtime.context import CRASH_AT_ENV  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402
from stratal.testing.labelled import labelled_split  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

CONFIG: dict[str, Any] = {
    "model": {"name": "mlp"},
    "epochs": 3,
    "batch_size": 4,
    "device": "cpu",
    "loader_workers": 0,
    "seed": 3,
}


def _start(api: PipelineApi, version: str, **config: Any) -> str:
    response = api.client.post(
        "/api/v1/jobs",
        json={"kind": "train", "dataset_version": version, "config": {**CONFIG, **config}},
    )
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    return str(response.json()["id"])


def _run(api: PipelineApi, job_id: str) -> dict[str, Any]:
    api.runner.run_queued()
    api.runner.collect()
    return api.runner.job(job_id)


def _job_dir(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id


def test_RUN_1_1_a_training_job_runs_through_its_states_to_completed(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8train")
    api = PipelineApi(runner)

    job_id = _start(api, version)
    job = _run(api, job_id)

    assert job["state"] == "completed", job
    [stage] = job["stages"]
    metrics = stage["metrics"]
    assert (metrics["epochs"], metrics["device"]) == (3, "cpu")
    assert 0.0 <= metrics["val_accuracy"] <= 1.0
    assert metrics["parameters"] == 3072 * 256 + 256 + 256 * 128 + 128 + 128 * 2 + 2  # 2 classes
    with get_engine().connect() as connection:
        states = (
            connection.execute(
                text("SELECT to_state FROM job_transitions WHERE job_id = :j ORDER BY id"),
                {"j": job_id},
            )
            .scalars()
            .all()
        )
    assert list(states) == ["queued", "running", "completed"], "every transition is logged"
    validated = [e for e in runner.events(job_id) if e["substage"] == "validate"]
    assert [e["metrics"]["epoch"] for e in validated] == [1, 2, 3]
    weights = _job_dir(job_id) / "weights"
    assert (weights / "model.safetensors").is_file() and (weights / "model.json").is_file()
    assert not list((_job_dir(job_id) / "checkpoints").glob("ckpt-*")), "REG-2.6"


def test_RUN_1_8_progress_reports_carry_the_training_metrics(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8metrics")
    api = PipelineApi(runner)

    job_id = _start(api, version, epochs=1)
    _run(api, job_id)

    progress = [e for e in runner.events(job_id) if e["type"] == "stage_progress"]
    assert progress, "at least one progress report per epoch"
    last = progress[-1]
    assert last["step"] == last["total"]
    assert {"loss", "accuracy", "learning_rate", "grad_norm", "step_ms", "samples_per_s"} <= set(
        last["metrics"]
    )


def test_RUN_1_3_a_killed_run_resumes_from_its_checkpoint_and_matches_an_uninterrupted_one(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8resume")
    api = PipelineApi(runner)
    whole = _start(api, version)
    assert _run(api, whole)["state"] == "completed"

    setup = next(e for e in runner.events(whole) if e["substage"] == "setup")
    steps_per_epoch = -(-int(setup["metrics"]["train_rows"]) // CONFIG["batch_size"])
    # The first progress report at or past this step is in epoch 2, after epoch 1's checkpoint.
    monkeypatch.setenv(CRASH_AT_ENV, f"train:step:{steps_per_epoch + 1}")
    killed = _start(api, version)
    first = _run(api, killed)
    assert first["state"] == "failed", first
    assert list((_job_dir(killed) / "checkpoints").glob("ckpt-*.json")), "a checkpoint survived"
    retry = api.client.post(f"/api/v1/jobs/{killed}/retry")
    assert retry.status_code == 202, retry.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    resumed = _run(api, killed)

    assert resumed["state"] == "completed", resumed
    resumes = [e for e in runner.events(killed) if e["substage"] == "resume"]
    assert len(resumes) == 1 and resumes[0]["attempt"] == 2
    assert resumes[0]["metrics"]["resumed_at_step"] > 0
    a = load_file(str(_job_dir(whole) / "weights" / "model.safetensors"))
    b = load_file(str(_job_dir(killed) / "weights" / "model.safetensors"))
    assert set(a) == set(b)
    for name in a:
        assert torch.allclose(a[name], b[name], atol=1e-6), name  # NFR-4: within tolerance
    assert resumed["stages"][0]["metrics"]["val_loss"] == pytest.approx(
        runner.job(whole)["stages"][0]["metrics"]["val_loss"], abs=1e-5
    )


@pytest.mark.gpu
def test_RUN_1_1_a_training_job_runs_on_the_gpu_under_the_cap(
    runner: Runner, tmp_path: Path
) -> None:
    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")
    version = labelled_split(runner, tmp_path / "src", "m8gpu")
    api = PipelineApi(runner)

    job_id = _start(api, version, model={"name": "small_cnn"}, device="gpu", epochs=2)
    job = _run(api, job_id)

    assert job["state"] == "completed", job
    metrics = job["stages"][0]["metrics"]
    assert metrics["device"] == "gpu"
    assert metrics["peak_vram_reserved_mb"] < 5.5 * 1024, "NFR-1"
