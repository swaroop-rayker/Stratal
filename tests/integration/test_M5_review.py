"""LABEL-1.5, DATA-4.7: review sessions against the real database, through the API.

A split version is made by a pipeline job with the owner's units faked; the
review endpoints then sample it, store decisions and close the session.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.db.models import ReviewItem, ReviewSession
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _split_version(api: PipelineApi, tmp_path: Path, slug: str, valid: int = 30) -> str:
    source = make_image_folder(tmp_path / "src", valid=valid, corrupt=1).root
    job = api.run(
        {"dataset": slug, "source": {"path": str(source)}, "stages": {"ingest": {"min_size": 32}}}
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def _manifest_digest(version_id: str) -> str:
    with Session(get_engine()) as session:
        _, path = registry.manifest_for(session, get_settings().data_dir, version_id)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _splits(version_id: str) -> dict[str, Any]:
    with Session(get_engine()) as session:
        return {
            r["row_key"]: r["split"]
            for r in registry.load_version(session, get_settings().data_dir, version_id)
            if r["status"] == "accepted"
        }


def test_LABEL_1_5_n_rows_are_sampled_and_decisions_are_stored_with_the_version(
    runner: Runner, tmp_path: Path
) -> None:
    """AC1: review results stored and linked to the version."""
    api = PipelineApi(runner)
    version = _split_version(api, tmp_path, "m5rev")
    client = api.client

    created = client.post(
        f"/api/v1/versions/{version}/reviews", json={"sample_size": 10, "seed": 5}
    )

    assert created.status_code == 201, created.text
    review = created.json()
    assert (review["version_id"], review["sample_size"], review["seed"]) == (version, 10, 5)
    assert review["status"] == "open" and review["id"].startswith("rev_")
    assert [item["position"] for item in review["items"]] == list(range(1, 11))
    assert all(item["row"]["caption"] and item["row"]["derived_sha256"] for item in review["items"])

    url = f"/api/v1/reviews/{review['id']}"
    for item in review["items"][:7]:
        decision = "reject" if item["position"] in (2, 5) else "accept"
        put = client.put(
            f"{url}/items/{item['position']}", json={"decision": decision, "note": "ok"}
        )
        assert put.status_code == 200, put.text
    again = client.put(f"{url}/items/5", json={"decision": "accept"})
    assert again.json()["decision"] == "accept", "a decision can change while open"

    progress = client.get(url).json()["progress"]
    assert (progress["decided"], progress["accept"], progress["reject"]) == (7, 6, 1)

    closed = client.post(f"{url}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed" and closed.json()["closed_at"]
    with Session(get_engine()) as session:
        stored = session.get(ReviewSession, review["id"])
        assert stored is not None and stored.version_id == version
        decided = session.scalars(
            select(ReviewItem).where(
                ReviewItem.session_id == review["id"], ReviewItem.decision.is_not(None)
            )
        ).all()
        assert len(decided) == 7
    late = client.put(f"{url}/items/9", json={"decision": "accept"})
    assert (late.status_code, late.json()["code"]) == (409, "conflict")


def test_DATA_4_7_items_carry_their_split_and_decisions_change_no_manifest(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split_version(api, tmp_path, "m5rev47")
    before = _manifest_digest(version)
    splits = _splits(version)

    review = api.client.post(f"/api/v1/versions/{version}/reviews", json={"sample_size": 12}).json()
    for item in review["items"]:
        api.client.put(
            f"/api/v1/reviews/{review['id']}/items/{item['position']}", json={"decision": "reject"}
        )
    summary = api.client.post(f"/api/v1/reviews/{review['id']}/close").json()

    assert all(item["split"] == splits[item["row_key"]] for item in review["items"])
    assert sum(s["sampled"] for s in summary["progress"]["by_split"].values()) == 12
    assert _manifest_digest(version) == before, "a review never edits the data"
    assert _splits(version) == splits
    assert "acceptance" in summary and "acceptance_unavailable" in summary


def test_LABEL_1_5_the_seed_is_recorded_and_the_same_seed_gives_the_same_sample(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split_version(api, tmp_path, "m5seed")
    post = api.client.post

    drawn = post(f"/api/v1/versions/{version}/reviews", json={"sample_size": 8}).json()
    repeat = post(
        f"/api/v1/versions/{version}/reviews", json={"sample_size": 8, "seed": drawn["seed"]}
    ).json()

    assert isinstance(drawn["seed"], int)
    assert [i["row_key"] for i in repeat["items"]] == [i["row_key"] for i in drawn["items"]]
    assert repeat["id"] != drawn["id"]


def test_LABEL_1_5_bad_review_requests_are_refused_with_problem_details(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = _split_version(api, tmp_path, "m5bad", valid=5)
    client = api.client

    too_many = client.post(f"/api/v1/versions/{version}/reviews", json={"sample_size": 6})
    zero = client.post(f"/api/v1/versions/{version}/reviews", json={"sample_size": 0})
    unknown_version = client.post(
        "/api/v1/versions/ds_000000000000/reviews", json={"sample_size": 1}
    )
    unknown_review = client.get("/api/v1/reviews/rev_9999")
    review = client.post(f"/api/v1/versions/{version}/reviews", json={"sample_size": 2}).json()
    bad_decision = client.put(f"/api/v1/reviews/{review['id']}/items/1", json={"decision": "maybe"})
    no_item = client.put(f"/api/v1/reviews/{review['id']}/items/3", json={"decision": "accept"})
    client.post(f"/api/v1/reviews/{review['id']}/close")
    closed_twice = client.post(f"/api/v1/reviews/{review['id']}/close")

    assert (too_many.status_code, too_many.json()["code"]) == (422, "validation_error")
    assert "5 accepted rows" in too_many.json()["suggested_action"]
    assert (zero.status_code, zero.json()["code"]) == (422, "validation_error")
    assert (unknown_version.status_code, unknown_version.json()["code"]) == (404, "not_found")
    assert (unknown_review.status_code, unknown_review.json()["code"]) == (404, "not_found")
    assert (bad_decision.status_code, bad_decision.json()["code"]) == (422, "validation_error")
    assert (no_item.status_code, no_item.json()["code"]) == (404, "not_found")
    assert (closed_twice.status_code, closed_twice.json()["code"]) == (409, "conflict")
