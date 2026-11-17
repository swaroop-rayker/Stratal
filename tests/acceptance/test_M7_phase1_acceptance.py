"""Phase-1 acceptance on the running stack: one full pipeline run, measured.

    docker compose --profile gpu up -d
    docker compose run --rm dev pytest -q -s -m acceptance tests/acceptance

Submits the whole phase-1 pipeline for a source folder (``STRATAL_ACCEPTANCE_SOURCE``,
default ``coco20k``) through the API, waits for it, and checks from the stack's own
telemetry:

- the phase-1 done-definition: raw images → captioned, filtered, versioned, split
  dataset with an eval report;
- NFR-2: RAM peak under 9 GB while it ran (the WSL VM's used memory, which bounds
  all Stratal containers together from above);
- NFR-1: the job's own peak VRAM (PyTorch reserved) under 5.5 GB;
- NFR-3: captioning at 2 images a second or more, counting only images captioned
  in this run, over the whole label stage (model loading and CLIP scoring
  included, so a lower bound).

It takes over an hour when most images have no cached caption, so it is marked
``acceptance`` and runs only when asked.
"""

import json
import os
import time
import urllib.request
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stratal.db.engine import get_engine
from stratal.db.models import ProcessSample, ResourceSample

pytestmark = pytest.mark.acceptance

API = os.environ.get("STRATAL_ACCEPTANCE_API", "http://api:8000/api/v1")
SOURCE = os.environ.get("STRATAL_ACCEPTANCE_SOURCE", "coco20k")
TIMEOUT_S = 5 * 3600
RAM_BUDGET_MB = 9 * 1024
VRAM_BUDGET_MB = 5.5 * 1024
CAPTIONS_PER_S = 2.0


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _wait(job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        job = _call("GET", f"/jobs/{job_id}")
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        stage = job.get("current_stage") or "-"
        print(f"{job_id} {job['state']} {stage}", flush=True)
        time.sleep(30)
    raise TimeoutError(f"{job_id} did not finish in {TIMEOUT_S} s")


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_NFR_2_a_full_phase_1_run_stays_within_ram_vram_and_caption_rate() -> None:
    job = _call(
        "POST",
        "/jobs",
        {
            "kind": "pipeline",
            "dataset": SOURCE,
            "source": {"kind": "folder", "path": f"/sources/{SOURCE}"},
            "stages": {},
            "until_stage": None,
        },
    )
    print(f"submitted {job['id']} for /sources/{SOURCE}", flush=True)
    job = _wait(job["id"])
    assert job["state"] == "completed", job.get("error")

    # The phase-1 done-definition.
    stages = {stage["name"]: stage for stage in job["stages"]}
    assert list(stages) == ["ingest", "clean", "preprocess", "label", "filter", "split", "evaluate"]
    split_version = stages["split"]["output_version_id"]
    report = _call("GET", f"/versions/{split_version}/reports/latest?kind=phase1_dataset")
    raw = report["report"]["rows"]["total"]
    kept = report["report"]["rows"]["accepted"]

    started, finished = _at(job["started_at"]), _at(job["finished_at"])
    with Session(get_engine()) as session:
        peak_ram = session.scalar(
            select(func.max(ResourceSample.ram_used_mb)).where(
                ResourceSample.ts >= started, ResourceSample.ts <= finished
            )
        )
        peak_vram_job = session.scalar(
            select(func.max(ProcessSample.torch_reserved_mb)).where(
                ProcessSample.job_id == job["id"]
            )
        )
        peak_vram_card = session.scalar(
            select(func.max(ResourceSample.vram_used_mb)).where(
                ResourceSample.ts >= started, ResourceSample.ts <= finished
            )
        )

    label = stages["label"]
    label_s = (_at(label["finished_at"]) - _at(label["started_at"])).total_seconds()
    metrics: dict[str, Any] = label["metrics"] or {}
    computed = int(metrics.get("captions_computed", 0))
    rate = computed / label_s if label_s else 0.0
    summary = {
        "job": job["id"],
        "source": SOURCE,
        "raw_rows": raw,
        "kept_rows": kept,
        "split_version": split_version,
        "report": report["id"],
        "minutes": round((finished - started).total_seconds() / 60, 1),
        "stage_minutes": {
            name: round((_at(s["finished_at"]) - _at(s["started_at"])).total_seconds() / 60, 2)
            for name, s in stages.items()
        },
        "peak_ram_mb": peak_ram,
        "peak_vram_job_mb": peak_vram_job,
        "peak_vram_card_mb": peak_vram_card,
        "captions_computed": computed,
        "caption_cache_hits": metrics.get("caption_cache_hits"),
        "label_minutes": round(label_s / 60, 1),
        "captions_per_s": round(rate, 2),
        "final_batch_size": metrics.get("final_batch_size"),
    }
    print("ACCEPTANCE " + json.dumps(summary), flush=True)

    assert raw >= 10_000 and kept > 0
    assert peak_ram is not None and peak_ram < RAM_BUDGET_MB, f"NFR-2: RAM peaked at {peak_ram} MB"
    assert peak_vram_job is not None and peak_vram_job < VRAM_BUDGET_MB, "NFR-1"
    if computed >= 1000:  # NFR-3 needs a real captioning run, not a cache replay
        assert rate >= CAPTIONS_PER_S, f"NFR-3: {rate:.2f} images/s"
