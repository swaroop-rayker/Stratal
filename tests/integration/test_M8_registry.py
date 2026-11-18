# pyright: basic, reportMissingImports=false
"""REG-1.1, REG-1.2: every training run registers a version that traces to its exact
data and config, in a lineage whose base is saved before the first step.

Needs PyTorch, so it runs in the GPU worker's image (``-m torch``); in the dev
image it is skipped. Runs are tiny MLPs on the CPU, as in ``test_M8_training``.
"""

import json
import random
from pathlib import Path
from typing import Any

import pytest

torch = pytest.importorskip("torch")

from safetensors.torch import load_file  # noqa: E402

from stratal.core.settings import get_settings  # noqa: E402
from stratal.runtime.context import CRASH_AT_ENV  # noqa: E402
from stratal.runtime.jobs import config_hash  # noqa: E402
from stratal.testing.harness import Runner, wait_until  # noqa: E402
from stratal.testing.labelled import labelled_split  # noqa: E402
from stratal.testing.pipeline import PipelineApi  # noqa: E402
from stratal.train.config import ModelSpec  # noqa: E402
from stratal.train.reference import build_model  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.torch, pytest.mark.usefixtures("fake_units")]

CONFIG: dict[str, Any] = {
    "model": {"name": "mlp"},
    "epochs": 2,
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


def _get(api: PipelineApi, path: str) -> Any:
    response = api.client.get(f"/api/v1{path}")
    assert response.status_code == 200, response.text
    return response.json()


def _files(version: dict[str, Any]) -> Path:
    return get_settings().data_dir / version["weights_path"]


def _initial(seed: int, lineage: dict[str, Any]) -> dict[str, Any]:
    """The weights a fresh model of the lineage gets from ``seed``, as the stage builds it."""
    random.seed(seed)
    torch.manual_seed(seed)
    model = build_model(
        ModelSpec.model_validate(lineage["model"]),
        tuple(lineage["input_shape"]),
        len(lineage["classes"]),
    )
    return model.state_dict()


def _versions(api: PipelineApi, lineage_id: str, **filters: str) -> list[str]:
    query = "&".join(f"{k}={v}" for k, v in filters.items())
    return [v["id"] for v in _get(api, f"/models/{lineage_id}?{query}")["versions"]]


def test_REG_1_1_a_trained_version_traces_to_its_exact_dataset_version_and_config(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8reg")
    api = PipelineApi(runner)

    job = _run(api, _start(api, version))

    assert job["state"] == "completed", job
    detail = _get(api, f"/model-versions/{job['model_version_id']}")
    stage_config = job["stages"][0]["config"]
    assert (detail["state"], detail["weights_status"]) == ("trained", "present")
    assert detail["job_id"] == job["id"]
    assert detail["dataset_version_id"] == version, "REG-1.1.AC1: the exact data"
    assert detail["config"] == stage_config, "REG-1.1.AC1: the exact config"
    assert detail["config_hash"] == config_hash(stage_config)
    assert detail["metrics"]["val_accuracy"] == job["stages"][0]["metrics"]["val_accuracy"]
    assert [h["epoch"] for h in detail["history"]] == [1, 2]
    folder = _files(detail)
    assert sorted(p.name for p in folder.iterdir()) == ["model.json", "model.safetensors"]
    assert detail["weights_bytes"] == sum(p.stat().st_size for p in folder.iterdir())
    meta = json.loads((folder / "model.json").read_text(encoding="utf-8"))
    assert (meta["dataset_version"], meta["config_hash"]) == (version, detail["config_hash"])
    assert meta["version_id"] == detail["id"]
    assert not (get_settings().data_dir / "jobs" / job["id"] / "weights").exists()

    lineage = _get(api, f"/models/{detail['lineage_id']}")
    assert lineage["name"] == "mlp · m8reg"
    assert (lineage["input_shape"], lineage["classes"]) == ([3, 32, 32], ["cat", "dog"])
    assert lineage["counts"] == {"base": 1, "partial": 0, "trained": 1}
    assert lineage["latest_version_id"] == detail["id"]
    [base] = [v for v in lineage["versions"] if v["state"] == "base"]
    assert detail["parent_version_id"] == base["id"]
    saved = load_file(str(_files(_get(api, f"/model-versions/{base['id']}")) / "model.safetensors"))
    for name, value in _initial(CONFIG["seed"], lineage).items():
        assert torch.equal(saved[name], value), f"the base is the initial weights: {name}"
    listed = _get(api, "/models?q=m8reg")["items"]
    assert [item["id"] for item in listed] == [lineage["id"]]


def test_REG_1_1_later_runs_of_a_lineage_start_from_its_base(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8regbase")
    api = PipelineApi(runner)
    first = _run(api, _start(api, version))
    lineage_id = _get(api, f"/model-versions/{first['model_version_id']}")["lineage_id"]

    # Another seed, and a learning rate too small to move anything: the weights it ends
    # with are the weights it started from.
    second = _run(api, _start(api, version, seed=4, epochs=1, learning_rate=1e-9))

    assert second["state"] == "completed", second
    detail = _get(api, f"/model-versions/{second['model_version_id']}")
    lineage = _get(api, f"/models/{lineage_id}")
    assert detail["lineage_id"] == lineage_id, "same model, same data: same lineage"
    assert lineage["counts"] == {"base": 1, "partial": 0, "trained": 2}
    [base] = [v for v in lineage["versions"] if v["state"] == "base"]
    assert detail["parent_version_id"] == base["id"]
    trained = load_file(str(_files(detail) / "model.safetensors"))
    start = load_file(str(_files(_get(api, f"/model-versions/{base['id']}")) / "model.safetensors"))
    own_seed = _initial(4, lineage)
    for name in start:
        assert torch.allclose(trained[name], start[name], atol=1e-6), name
    assert any(not torch.allclose(trained[n], own_seed[n], atol=1e-3) for n in start)
    setup = [e["message"] for e in runner.events(second["id"]) if e["substage"] == "setup"]
    assert f"Lineage mlp · m8regbase: starting from base {base['id']}" in setup


def test_REG_1_1_a_retry_after_the_weights_were_registered_trains_nothing(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8regretry")
    api = PipelineApi(runner)
    monkeypatch.setenv(CRASH_AT_ENV, "train:substage:finish")

    job_id = _start(api, version)
    assert _run(api, job_id)["state"] == "failed", "killed right after registering"
    registered = _get(api, f"/jobs/{job_id}")["model_version_id"]
    assert registered is not None
    assert api.client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 202
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    job = _run(api, job_id)

    assert job["state"] == "completed", job
    assert job["model_version_id"] == registered, "one version per run"
    retried = [e for e in runner.events(job_id) if e["attempt"] == 2]
    assert not [e for e in retried if e["substage"] in ("train", "resume")], "nothing retrained"
    assert any(e["message"].startswith(f"Run already registered as {registered}") for e in retried)
    assert job["stages"][0]["metrics"]["val_accuracy"] is not None


def _spooled(job_id: str, substage: str) -> int:
    spool = get_settings().data_dir / "jobs" / job_id / "events.jsonl"
    if not spool.is_file():
        return 0
    return sum(
        1
        for line in spool.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("substage") == substage
    )


def test_REG_1_2_a_paused_run_is_partial_then_trained_and_states_filter(
    runner: Runner, tmp_path: Path
) -> None:
    version = labelled_split(runner, tmp_path / "src", "m8regstates")
    api = PipelineApi(runner)
    job_id = _start(api, version, epochs=25)
    thread = runner.run_in_thread()
    wait_until(lambda: _spooled(job_id, "validate") >= 2, timeout_s=60, what="two epochs")
    assert api.client.post(f"/api/v1/jobs/{job_id}/pause").status_code == 202
    thread.join(60)
    assert runner.job(job_id)["state"] == "paused"

    version_id = _get(api, f"/jobs/{job_id}")["model_version_id"]
    partial = _get(api, f"/model-versions/{version_id}")
    assert (partial["state"], partial["job_state"]) == ("partial", "paused")
    assert partial["weights_bytes"] is None, "its weights are the run's checkpoint"
    assert len(partial["history"]) >= 2
    lineage_id = partial["lineage_id"]
    [base] = _versions(api, lineage_id, state="base")
    assert _versions(api, lineage_id, state="partial") == [version_id]
    assert _versions(api, lineage_id, state="trained") == []
    assert _get(api, f"/models/{lineage_id}")["checkpoint_bytes"] > 0
    assert lineage_id in [m["id"] for m in _get(api, "/models?state=partial")["items"]]
    assert lineage_id not in [m["id"] for m in _get(api, "/models?state=trained")["items"]]

    assert api.client.post(f"/api/v1/jobs/{job_id}/resume").status_code == 202
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    assert _run(api, job_id)["state"] == "completed"

    assert _versions(api, lineage_id, state="trained") == [version_id], "the same version"
    assert _versions(api, lineage_id, state="partial") == []
    assert _versions(api, lineage_id) == [version_id, base], "newest first"
    lineage = _get(api, f"/models/{lineage_id}")
    assert lineage["counts"] == {"base": 1, "partial": 0, "trained": 1}
    assert lineage["checkpoint_bytes"] == 0, "REG-2.6"
    assert lineage["latest_state"] == "trained"
    assert lineage_id in [m["id"] for m in _get(api, "/models?state=trained")["items"]]
    assert _versions(api, lineage_id, weights="deleted") == []


def test_REG_1_1_unknown_lineages_and_versions_are_not_found(runner: Runner) -> None:
    api = PipelineApi(runner)
    for path in ("/models/ml_000000000000", "/model-versions/mv_000000000000"):
        response = api.client.get(f"/api/v1{path}")
        assert response.status_code == 404, path
        assert response.json()["code"] == "not_found"


def _unregister(lineage_id: str) -> None:
    """Put a lineage's runs back as runs were before the registry: weights in
    ``jobs/<id>/weights/``, no lineage, no versions, no base."""
    import shutil

    from sqlalchemy import text

    from stratal.db.engine import get_engine

    data = get_settings().data_dir
    with get_engine().begin() as connection:
        runs = connection.execute(
            text(
                "SELECT job_id, weights_path FROM model_versions "
                "WHERE lineage_id = :l AND state = 'trained'"
            ),
            {"l": lineage_id},
        ).all()
        connection.execute(
            text("DELETE FROM model_versions WHERE lineage_id = :l"), {"l": lineage_id}
        )
        connection.execute(text("DELETE FROM model_lineages WHERE id = :l"), {"l": lineage_id})
    for job_id, folder in runs:
        shutil.move(str(data / folder), str(data / "jobs" / job_id / "weights"))
    shutil.rmtree(data / "registry" / lineage_id)


def test_REG_1_1_backfill_registers_runs_from_before_the_registry(
    runner: Runner, tmp_path: Path
) -> None:
    from stratal.db.engine import get_engine
    from stratal.train.backfill import backfill

    version = labelled_split(runner, tmp_path / "src", "m8regbackfill")
    api = PipelineApi(runner)
    first = _run(api, _start(api, version))
    second = _run(api, _start(api, version, seed=5, epochs=1))
    lineage_id = _get(api, f"/model-versions/{first['model_version_id']}")["lineage_id"]
    _unregister(lineage_id)
    data = get_settings().data_dir

    planned = backfill(get_engine(), data, dry_run=True)
    mine = [r for r in planned if r["job_id"] in (first["id"], second["id"])]
    assert [r["parent_version_id"] is not None for r in mine] == [True, False]
    assert (data / "jobs" / first["id"] / "weights").is_dir(), "a dry run moves nothing"
    assert _get(api, "/models?q=m8regbackfill")["items"] == []

    done = [r for r in backfill(get_engine(), data) if r["lineage_id"] == lineage_id]

    assert [r["job_id"] for r in done] == [first["id"], second["id"]]
    lineage = _get(api, f"/models/{lineage_id}")
    assert lineage["counts"] == {"base": 1, "partial": 0, "trained": 2}
    [base] = [v for v in lineage["versions"] if v["state"] == "base"]
    a = _get(api, f"/model-versions/{done[0]['version_id']}")
    b = _get(api, f"/model-versions/{done[1]['version_id']}")
    assert a["parent_version_id"] == base["id"], "seed 3 started from the rebuilt base"
    assert b["parent_version_id"] is None and "seed 5" in b["note"]
    assert (a["dataset_version_id"], a["config"]) == (version, first["stages"][0]["config"])
    assert a["metrics"]["val_accuracy"] == first["stages"][0]["metrics"]["val_accuracy"]
    assert not (data / "jobs" / first["id"] / "weights").exists(), "moved, not copied"
    saved = load_file(str(_files(_get(api, f"/model-versions/{base['id']}")) / "model.safetensors"))
    for name, value in _initial(CONFIG["seed"], lineage).items():
        assert torch.equal(saved[name], value), name
    assert [r["lineage_id"] for r in backfill(get_engine(), data)].count(lineage_id) == 0
