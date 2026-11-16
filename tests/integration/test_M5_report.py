"""EVAL-1.1, DATA-4.7: the evaluate stage and report endpoints against the real database.

The owner's units run as fakes (the shared ``fake_units`` fixture), so the
numbers in the reports are the fakes'; what is tested is that a report is
built and stored per version, with the review it names or the newest closed one.
"""

from pathlib import Path
from typing import Any

import pytest

from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _pipeline(api: PipelineApi, tmp_path: Path, slug: str, seed: int = 0) -> dict[str, Any]:
    source = make_image_folder(tmp_path / slug, valid=16, corrupt=1, seed=seed).root
    job = api.run(
        {"dataset": slug, "source": {"path": str(source)}, "stages": {"ingest": {"min_size": 32}}}
    )
    assert job["state"] == "completed", job
    return job


def _report_job(api: PipelineApi, version: str, **body: Any) -> dict[str, Any]:
    response = api.client.post(f"/api/v1/versions/{version}/reports", json=body)
    assert response.status_code == 202, response.text
    api.runner.queue.extend(api.queued)
    api.queued.clear()
    api.runner.run_queued()
    api.runner.collect()
    return api.runner.job(response.json()["id"])


def _closed_review(api: PipelineApi, version: str, decisions: list[str]) -> str:
    review = api.client.post(
        f"/api/v1/versions/{version}/reviews", json={"sample_size": len(decisions), "seed": 3}
    ).json()
    for item, decision in zip(review["items"], decisions, strict=True):
        api.client.put(
            f"/api/v1/reviews/{review['id']}/items/{item['position']}", json={"decision": decision}
        )
    api.client.post(f"/api/v1/reviews/{review['id']}/close")
    return str(review["id"])


def test_EVAL_1_1_the_pipeline_ends_with_a_report_on_its_split_version(
    runner: Runner, tmp_path: Path
) -> None:
    """AC1: a report generated per dataset version."""
    api = PipelineApi(runner)

    job = _pipeline(api, tmp_path, "m5eval")

    assert [s["name"] for s in job["stages"]][-3:] == ["filter", "split", "evaluate"]
    split_version = job["stages"][-2]["output_version_id"]
    assert job["output_version_id"] == split_version, "evaluate makes no new version"
    latest = api.client.get(f"/api/v1/versions/{split_version}/reports/latest")
    assert latest.status_code == 200, latest.text
    stored = latest.json()
    assert stored["id"] == job["stages"][-1]["metrics"]["report_id"]
    assert stored["job_id"] == job["id"] and stored["review_session_id"] is None
    report = stored["report"]
    assert report["version_id"] == split_version
    assert report["rows"]["accepted"] == 16 and sum(report["rows"]["by_split"].values()) == 16
    assert {"rejections", "clip_scores", "filter_rate", "review"} <= set(report)
    assert api.client.get(f"/api/v1/reports/{stored['id']}").json() == stored


def test_DATA_4_7_a_report_uses_the_newest_closed_review_or_the_one_named(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _pipeline(api, tmp_path, "m5evrev")["output_version_id"]
    first = _closed_review(api, version, ["accept", "reject", "accept"])
    second = _closed_review(api, version, ["accept", "accept"])

    newest = _report_job(api, version)
    named = _report_job(api, version, review_id=first)

    assert newest["state"] == named["state"] == "completed"
    latest = api.client.get(f"/api/v1/versions/{version}/reports/latest").json()
    assert latest["review_session_id"] == first, "the latest report is the one naming the first"
    by_id = api.client.get(f"/api/v1/reports/{newest['stages'][0]['metrics']['report_id']}").json()
    assert by_id["review_session_id"] == second
    review_part = latest["report"]["review"]
    assert (review_part["review_id"], review_part["decided"]) == (first, 3)
    assert review_part["acceptance"]["overall"]["decided"] == 3


def test_EVAL_1_1_bad_report_requests_are_refused(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    version = _pipeline(api, tmp_path, "m5evbad")["output_version_id"]
    other = _pipeline(api, tmp_path, "m5evother", seed=1)["output_version_id"]
    foreign = _closed_review(api, other, ["accept"])
    post = api.client.post

    wrong_review = post(f"/api/v1/versions/{version}/reports", json={"review_id": foreign})
    missing_version = post("/api/v1/versions/ds_000000000000/reports", json={})
    no_report = api.client.get(f"/api/v1/versions/{other}/reports/latest")
    no_such = api.client.get("/api/v1/reports/999999")

    assert (wrong_review.status_code, wrong_review.json()["code"]) == (422, "validation_error")
    assert (missing_version.status_code, missing_version.json()["code"]) == (404, "not_found")
    assert no_report.status_code == 200, "the pipeline already reported on it"
    assert (no_such.status_code, no_such.json()["code"]) == (404, "not_found")
