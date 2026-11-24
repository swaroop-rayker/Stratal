# pyright: basic
"""Phase-2 acceptance on the running stack (M11, K12): a CNN built on SCR-08, trained on a
dataset version, its test metrics on SCR-11; NFR-1, NFR-2, NFR-4 and NFR-5 measured.

    docker compose --profile gpu up -d
    docker compose run --rm dev pytest -q -s -m acceptance
        tests/acceptance/test_M11_phase2_acceptance.py

Through the API, as SCR-08 and SCR-09 do: the built-in *Small CNN* template's spec, checked
by ``POST /model-specs/validate`` against the CIFAR-10 split
(``STRATAL_ACCEPTANCE_SPLIT``, default ``ds_f8e67211dd94``), then trained for 10 epochs.

- **NFR-1**: each run's peak VRAM (PyTorch reserved) under 5.5 GB.
- **NFR-2**: the VM's RAM under 9 GB while the runs train.
- **NFR-4**: two runs with one seed reach test accuracies within 1 percentage point (OC-11).
- **The registry (SCR-11)**: each run registers a trained version whose test metrics
  ``GET /models/{lineage}`` shows, and ``/predict`` answers for a test image.
- **NFR-5**: a third run whose GPU worker is killed mid-run (``docker kill`` from the host,
  when this test prints ``KILL_NOW``) is requeued by the watchdog, resumes from its last
  checkpoint and completes; the work lost, from that checkpoint to the worker's last word, is
  at most 10 minutes. The host restarts the worker (``docker compose --profile gpu up -d
  worker-gpu``); this test waits up to 15 minutes for the kill.

Each run prints an ``ACCEPTANCE`` line of figures for the README.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine
from stratal.db.models import Event, ProcessSample, ResourceSample

pytestmark = pytest.mark.acceptance

API = os.environ.get("STRATAL_ACCEPTANCE_API", "http://api:8000/api/v1")
SPLIT = os.environ.get("STRATAL_ACCEPTANCE_SPLIT", "ds_f8e67211dd94")
EPOCHS = 10
SEED = 11
RAM_BUDGET_MB = 9 * 1024
VRAM_BUDGET_MB = 5.5 * 1024
ACCURACY_TOLERANCE = 0.01  # OC-11
LOST_BUDGET_S = 10 * 60  # NFR-5, OC-3
KILL_AT_EPOCH = 4


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{method} {path}: {exc.code} {exc.read().decode()[:600]}") from None


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _small_cnn() -> dict[str, Any]:
    """The built-in template's spec, checked as SCR-08 checks it (BUILD-1.7, BUILD-1.3)."""
    templates = _call("GET", "/model-templates?dataset_type=labelled_images")
    items = templates["items"] if isinstance(templates, dict) else templates
    spec = next(t["spec"] for t in items if t["name"] == "Small CNN")
    report = _call("POST", "/model-specs/validate", {"graph": spec, "dataset_version": SPLIT})
    problems = report["report"].get("problems") or []
    assert not [p for p in problems if p.get("level", "error") == "error"], problems
    return spec


def _train(spec: dict[str, Any], label: str) -> str:
    body = {
        "kind": "train",
        "dataset_version": SPLIT,
        "config": {
            "model": {"name": "graph", "graph": {**spec, "label": label}},
            "epochs": EPOCHS,
            "batch_size": 128,
            "seed": SEED,
            "device": "gpu",
        },
    }
    job = _call("POST", "/jobs", body)
    print(f"submitted {job['id']} ({label})", flush=True)
    return str(job["id"])


def _wait(job_id: str, *, timeout_s: float, on_epoch: Any = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = _call("GET", f"/jobs/{job_id}")
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        stage = job["stages"][0]
        step, total = stage.get("step"), stage.get("total")
        epoch = round(step / total * EPOCHS, 2) if step and total else None  # global steps
        print(f"{job_id} {job['state']} attempt {job['attempt']} epoch {epoch}", flush=True)
        if on_epoch is not None:
            on_epoch(job, epoch)
        time.sleep(10)
    raise TimeoutError(f"{job_id} did not finish in {timeout_s} s")


def _figures(job: dict[str, Any]) -> dict[str, Any]:
    started, finished = _at(job["started_at"]), _at(job["finished_at"])
    with Session(get_engine()) as session:
        peak_ram = session.scalar(
            select(func.max(ResourceSample.ram_used_mb)).where(
                ResourceSample.ts >= started, ResourceSample.ts <= finished
            )
        )
        peak_card = session.scalar(
            select(func.max(ResourceSample.vram_used_mb)).where(
                ResourceSample.ts >= started, ResourceSample.ts <= finished
            )
        )
        peak_torch = session.scalar(
            select(func.max(ProcessSample.torch_reserved_mb)).where(
                ProcessSample.job_id == job["id"]
            )
        )
    metrics: dict[str, Any] = job["stages"][0]["metrics"] or {}
    return {
        "job": job["id"],
        "model_version_id": job.get("model_version_id"),
        "attempt": job["attempt"],
        "minutes": round((finished - started).total_seconds() / 60, 1),
        "val_accuracy": metrics.get("val_accuracy"),
        "test_accuracy": metrics.get("test_accuracy"),
        "peak_ram_mb": peak_ram,
        "peak_vram_torch_mb": peak_torch,
        "peak_vram_card_mb": peak_card,
        "peak_vram_reserved_mb_stage": metrics.get("peak_vram_reserved_mb"),
    }


def test_NFR_4_two_runs_of_the_small_cnn_from_the_builder_agree_and_stay_within_budget() -> None:
    spec = _small_cnn()
    results = []
    for run in ("a", "b"):
        job = _wait(_train(spec, "acceptance_small_cnn"), timeout_s=3 * 3600)
        assert job["state"] == "completed", job.get("error")
        figures = _figures(job)
        print("ACCEPTANCE " + json.dumps({"run": run, **figures}), flush=True)
        results.append((job, figures))

    for job, figures in results:
        assert figures["peak_vram_torch_mb"] is not None
        assert figures["peak_vram_torch_mb"] < VRAM_BUDGET_MB, f"NFR-1: {figures}"
        assert figures["peak_ram_mb"] is not None and figures["peak_ram_mb"] < RAM_BUDGET_MB, (
            f"NFR-2: {figures}"
        )
        version = _call("GET", f"/model-versions/{job['model_version_id']}")
        assert version["state"] == "trained"
        assert version["metrics"].get("test_accuracy") is not None, "test metrics on SCR-11"

    a, b = (f["test_accuracy"] for _job, f in results)
    assert abs(a - b) <= ACCURACY_TOLERANCE, f"NFR-4: {a:.4f} and {b:.4f}"

    # SCR-11's Try it: the service answers for a test image (REG-1.4). The latest version:
    # retention may already have deleted the first run's weights, an intermediate one.
    first = results[-1][0]["model_version_id"]
    row = _call("GET", f"/versions/{SPLIT}/rows?split=test&limit=1")["items"][0]
    answer = _call(
        "POST", f"/models/{first}/predict", {"blob": row["derived_sha256"] or row["sha256"]}
    )
    print(
        "ACCEPTANCE " + json.dumps({"predict": answer.get("classes", answer)}, default=str)[:400],
        flush=True,
    )


def test_NFR_5_a_killed_worker_loses_at_most_10_minutes() -> None:
    spec = _small_cnn()
    job_id = _train(spec, "acceptance_small_cnn")
    told = {"kill": False}

    def kill_when_ready(job: dict[str, Any], epoch: Any) -> None:
        if (
            not told["kill"]
            and job["attempt"] == 1
            and isinstance(epoch, int | float)
            and epoch >= KILL_AT_EPOCH
        ):
            print(f"KILL_NOW {job_id}", flush=True)
            told["kill"] = True

    job = _wait(job_id, timeout_s=3 * 3600, on_epoch=kill_when_ready)
    assert told["kill"], "the run ended before the kill point"
    assert job["state"] == "completed", job.get("error")
    assert job["attempt"] >= 2, "the worker was not killed: the run never resumed"

    with Session(get_engine()) as session:
        last_word = session.scalar(
            select(func.max(Event.ts)).where(
                Event.job_id == job_id, Event.attempt == 1, Event.type != "job_state"
            )
        )
        checkpoint = session.scalar(
            select(func.max(Event.ts)).where(
                Event.job_id == job_id, Event.attempt == 1, Event.substage == "checkpoint"
            )
        )
        resumed = session.scalars(
            select(Event.message).where(Event.job_id == job_id, Event.substage == "resume")
        ).all()
    assert last_word is not None and checkpoint is not None
    lost = (last_word - checkpoint).total_seconds()
    figures = _figures(job)
    print(
        "ACCEPTANCE "
        + json.dumps({"run": "killed", **figures, "lost_s": round(lost, 1), "resume": resumed[:1]}),
        flush=True,
    )
    assert resumed, "the second attempt resumed from a checkpoint"
    assert lost <= LOST_BUDGET_S, f"NFR-5: {lost:.0f} s of work lost"
