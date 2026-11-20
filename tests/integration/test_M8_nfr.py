# pyright: basic, reportMissingImports=false
"""NFR-1 and NFR-8 for training, against the real PostgreSQL and Redis.

NFR-1: on the card, a training run's peak VRAM (PyTorch's reserved memory plus the
CUDA context, ADR-0006 rule 9) stays under 5.5 GB, and the job records it
(NFR-1.AC1). NFR-8: a run writes its checkpoints and weights only as safetensors and
JSON, a whole run and its resume unpickle nothing, and a pickle planted where weights
or a checkpoint belong is refused with ``weights_unreadable`` before any of it runs.

Needs PyTorch, so it runs in the GPU worker's image (check.ps1, "integration (torch)").
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

torch = pytest.importorskip("torch")

from stratal.core.errors import WeightsUnreadable  # noqa: E402
from stratal.core.settings import get_settings  # noqa: E402
from stratal.db.engine import get_engine  # noqa: E402
from stratal.registry import store  # noqa: E402
from stratal.runtime.child import FORBID_UNPICKLING_ENV  # noqa: E402
from stratal.runtime.context import CRASH_AT_ENV  # noqa: E402
from stratal.testing.harness import Runner  # noqa: E402
from stratal.testing.labelled import labelled_split  # noqa: E402
from stratal.testing.no_pickle import forbid_unpickling, payload, plant_pickle  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402
from stratal.train import checkpoint as ckpt  # noqa: E402
from stratal.train import weights  # noqa: E402
from stratal.train.config import ModelSpec  # noqa: E402
from stratal.train.reference import build_model  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

NFR_1_LIMIT_GB = 5.5
CUDA_CONTEXT_GB = 0.09  # ADR-0006 rule 9, measured in spike S-02
SAFE_SUFFIXES = {".safetensors", ".json"}

CONFIG: dict[str, Any] = {
    "model": {"name": "mlp"},
    "epochs": 3,
    "batch_size": 4,
    "device": "cpu",
    "loader_workers": 0,
    "seed": 5,
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


def _retry(api: PipelineApi, job_id: str) -> dict[str, Any]:
    response = api.client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    return _run(api, job_id)


def _checkpoints(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id / "checkpoints"


def _version_folders(job_id: str) -> dict[str, Path]:
    """The run's lineage folders by state: its base and its trained version (REG-1.1)."""
    with get_engine().connect() as connection:
        lineage = connection.execute(
            text("SELECT lineage_id FROM model_versions WHERE job_id = :j AND state = 'trained'"),
            {"j": job_id},
        ).scalar_one()
        rows = connection.execute(
            text(
                "SELECT state, weights_path FROM model_versions WHERE lineage_id = :l "
                "AND (job_id = :j OR state = 'base') AND weights_path IS NOT NULL"
            ),
            {"l": lineage, "j": job_id},
        ).all()
    return {state: get_settings().data_dir / path for state, path in rows}


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def _crash_after_first_checkpoint(
    api: PipelineApi, version: str, monkeypatch: pytest.MonkeyPatch
) -> str:
    """A run killed right after its first checkpoint, at the end of epoch 1 (RUN-1.3)."""
    monkeypatch.setenv(CRASH_AT_ENV, "train:substage:checkpoint")
    job_id = _start(api, version)
    job = _run(api, job_id)
    monkeypatch.delenv(CRASH_AT_ENV)
    assert job["state"] == "failed", job
    assert list(_checkpoints(job_id).glob("ckpt-*.json")), job["error"]
    return job_id


# ---------------------------------------------------------------------------- NFR-1


@pytest.mark.gpu
def test_NFR_1_peak_vram_of_a_training_run_stays_under_5_5_gb_and_is_logged(
    runner: Runner, tmp_path: Path
) -> None:
    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")
    version = labelled_split(runner, tmp_path / "src", "nfr1train", per_class=400)
    api = PipelineApi(runner)

    # Both reference models at the default batch, and the CNN at four times it.
    for model, batch in (("mlp", 128), ("small_cnn", 128), ("small_cnn", 512)):
        job_id = _start(
            api, version, model={"name": model}, device="gpu", batch_size=batch, epochs=1
        )
        job = _run(api, job_id)

        assert job["state"] == "completed", job
        setup = next(e for e in runner.events(job_id) if e["substage"] == "setup")
        assert int(setup["metrics"]["train_rows"]) >= batch, "at least one full batch"
        metrics = job["stages"][0]["metrics"]
        assert metrics["device"] == "gpu"
        reserved_gb = metrics["peak_vram_reserved_mb"] / 1024  # NFR-1.AC1: logged per job
        assert metrics["peak_vram_allocated_mb"] <= metrics["peak_vram_reserved_mb"]
        total = reserved_gb + CUDA_CONTEXT_GB
        assert total < NFR_1_LIMIT_GB, f"{model} at {batch}: {total:.2f} GB"
        print(f"NFR-1: {model} batch {batch}: peak reserved {reserved_gb:.3f} GB + context")


# ---------------------------------------------------------------------------- NFR-8


def test_NFR_8_a_run_and_its_resume_unpickle_nothing_and_write_only_safetensors_and_json(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = labelled_split(runner, tmp_path / "src", "nfr8files")
    api = PipelineApi(runner)
    report = tmp_path / "unpickled.txt"
    monkeypatch.setenv(FORBID_UNPICKLING_ENV, str(report))

    job_id = _crash_after_first_checkpoint(api, version, monkeypatch)
    left = _files(_checkpoints(job_id))
    with forbid_unpickling() as attempts:
        loaded = ckpt.load(ckpt.latest(_checkpoints(job_id)))  # type: ignore[arg-type]
    resumed = _retry(api, job_id)

    assert left and {p.suffix for p in left} == SAFE_SUFFIXES, left
    assert loaded.tensors and attempts == []
    assert resumed["state"] == "completed", resumed
    assert [e for e in runner.events(job_id) if e["substage"] == "resume"], "it resumed"
    assert not report.exists(), report.read_text(encoding="utf-8")  # both attempts, whole
    folders = _version_folders(job_id)
    assert set(folders) == {"base", "trained"}
    # This run's lineage only: other tests' classical models keep .skops files, NFR-8's
    # safe format for scikit-learn (ADR-0019), in the same session's registry.
    lineage = next(iter(folders.values())).parent
    assert {p.suffix for p in _files(lineage)} <= SAFE_SUFFIXES, _files(lineage)
    for folder in folders.values():
        spec = json.loads((folder / store.META_FILE).read_text(encoding="utf-8"))
        model = build_model(
            ModelSpec.model_validate(spec["model"]),
            tuple(spec["input_shape"]),
            len(spec["classes"]),
        )
        with forbid_unpickling() as attempts:
            weights.load_into(model, folder)
        assert attempts == [], folder


@pytest.mark.parametrize("planter", ["pickle", "torch.save"])
def test_NFR_8_a_pickle_planted_as_weights_or_a_checkpoint_is_refused_and_never_runs(
    tmp_path: Path, planter: str
) -> None:
    marker = tmp_path / "PWNED"

    def plant(path: Path) -> None:
        if planter == "pickle":
            plant_pickle(path, marker)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(payload(marker), path)

    folder = tmp_path / "registry" / "ml_x" / "mv_x"
    plant(folder / store.WEIGHTS_FILE)
    model = torch.nn.Linear(2, 2)
    with pytest.raises(WeightsUnreadable, match="not a valid safetensors file") as weights_err:
        weights.load_into(model, folder)
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "ckpt-000000004.json").write_text(
        f'{{"format": {ckpt.FORMAT}, "config_hash": "x", "position": {{}}, "history": []}}',
        encoding="utf-8",
    )
    plant(checkpoints / "ckpt-000000004.safetensors")
    with pytest.raises(WeightsUnreadable) as checkpoint_err:
        ckpt.load(checkpoints / "ckpt-000000004.json")

    assert weights_err.value.code == "weights_unreadable"
    assert "Re-train" in (weights_err.value.suggested_action or "")
    assert "Cancel the run" in (checkpoint_err.value.suggested_action or "")
    assert not marker.exists(), "the payload never ran"


def test_NFR_8_a_run_resuming_from_a_planted_checkpoint_fails_typed_and_runs_nothing(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = labelled_split(runner, tmp_path / "src", "nfr8planted")
    api = PipelineApi(runner)
    job_id = _crash_after_first_checkpoint(api, version, monkeypatch)
    marker = tmp_path / "PWNED"
    [tensors] = _checkpoints(job_id).glob("ckpt-*.safetensors")
    plant_pickle(tensors, marker)

    job = _retry(api, job_id)

    assert job["state"] == "failed", job
    assert job["error"]["code"] == "weights_unreadable", job["error"]
    assert "Cancel the run" in job["error"]["suggested_action"]
    assert not marker.exists(), "the payload never ran"
