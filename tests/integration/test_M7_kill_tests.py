"""NFR-5: a kill test for every phase-1 stage (NFR-5.AC1).

Each test SIGKILLs the stage's process partway through (the test-only crash
point in :mod:`stratal.runtime.context`), retries the job, and checks that:

- the job then completes with the same version IDs as an uninterrupted run, so
  the kill corrupted nothing;
- the stages with a per-item cache reuse what was finished before the kill, so
  the work lost is at most the batch in progress.

Stages without a per-item cache (ingest, filter, split, evaluate) restart from
the start; their bound comes from their duration on the 10k set (README, NFR-3
benchmark). The owner's units run as fakes; the stage shells, caches, commits,
supervisor and retry are real.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from stratal.runtime.context import CRASH_AT_ENV
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]

PHASE1 = ["ingest", "clean", "preprocess", "label", "filter", "split", "evaluate"]
IMAGES = 600


def _body(source: Path, until: str) -> dict[str, Any]:
    return {
        "dataset": "kill",
        "source": {"path": str(source)},
        "stages": {"ingest": {"min_size": 32}, "label": {"clip_batch_size": 64}},
        "until_stage": until,
    }


def _versions(job: dict[str, Any]) -> dict[str, str | None]:
    return {stage["name"]: stage["output_version_id"] for stage in job["stages"]}


def _metrics(job: dict[str, Any], stage: str) -> dict[str, Any]:
    return next(s for s in job["stages"] if s["name"] == stage)["metrics"] or {}


def _killed_then_retried(
    api: PipelineApi,
    runner: Runner,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    crash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    monkeypatch.setenv(CRASH_AT_ENV, crash)
    response = api.post(body)
    job_id = response.json()["id"]
    runner.queue.extend(api.queued)
    api.queued.clear()
    runner.run_queued()
    runner.collect()
    killed = runner.job(job_id)

    retried = api.client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retried.status_code == 202, retried.text
    runner.queue.extend(api.queued)
    api.queued.clear()
    runner.run_queued()
    runner.collect()
    return killed, runner.job(job_id)


CRASHES = {
    "ingest": "ingest:step:500",
    "clean": "clean:step:256",
    "preprocess": "preprocess:step:128",
    "label": "label:step:3",
    "filter": "filter:event:2",
    "split": "split:event:2",
    "evaluate": "evaluate:event:2",
}


@pytest.mark.parametrize("stage", PHASE1)
def test_NFR_5_a_stage_killed_midway_resumes_and_makes_the_same_versions(
    stage: str,
    runner: Runner,
    reset_state: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api = PipelineApi(runner)
    source = make_image_folder(tmp_path / "src", valid=IMAGES, size=64).root
    body = _body(source, stage)
    reference = _versions(api.run(body))
    reset_state()  # a fresh database and an empty derivation cache: nothing to reuse

    killed, done = _killed_then_retried(api, runner, monkeypatch, body, CRASHES[stage])

    assert killed["state"] == "failed", f"the crash point in {stage} did not fire"
    failed_stage = next(s for s in killed["stages"] if s["status"] == "failed")
    assert failed_stage["name"] == stage
    assert "signal" in str(killed["error"]).lower() or "kill" in str(killed["error"]).lower()
    assert done["state"] == "completed" and done["attempt"] == 2
    assert _versions(done) == reference, "the kill changed what the pipeline made"

    metrics = _metrics(done, stage)
    if stage == "clean":
        assert metrics["cache_hits"] >= 256 and metrics["hashed"] <= IMAGES - 256
    elif stage == "preprocess":
        assert metrics["cache_hits"] >= 128 and metrics["computed"] <= IMAGES - 128
    elif stage == "label":
        assert metrics["captions_computed"] == 0, "captions were committed before the kill"
        assert metrics["clip_cache_hits"] >= 3 * 64
        assert metrics["clip_computed"] <= IMAGES - 3 * 64
