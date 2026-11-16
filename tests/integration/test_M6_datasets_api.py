"""DATA-3.1 to 3.4, UI-2.1: the datasets and versions endpoints (API.md §3.2) against the
real database, on versions made by a pipeline job with only the label stage faked.
"""

import io
import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from stratal.core.settings import get_settings
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_labels")]


@pytest.fixture
def fake_labels(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Only the GPU stage is faked; dedupe, split and report run the owner's real units,
    so exact copies are marked as duplicates."""
    from stratal.runtime import stages as stage_registry
    from stratal.testing.pipeline import restore_real_units

    monkeypatch.setenv("STRATAL_EXTRA_STAGES", "stratal.testing.stages,stratal.testing.fake_label")
    stage_registry.load_extra_stages()
    yield
    restore_real_units()


V1 = "/api/v1"


def _pipeline(api: PipelineApi, tmp_path: Path, slug: str, **folder: int) -> dict[str, Any]:
    source = make_image_folder(tmp_path / slug, **{"valid": 12, "corrupt": 1, **folder}).root
    job = api.run(
        {"dataset": slug, "source": {"path": str(source)}, "stages": {"ingest": {"min_size": 128}}}
    )
    assert job["state"] == "completed", job
    return job


def test_UI_2_1_datasets_list_their_versions_newest_first(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    job = _pipeline(api, tmp_path, "m6list")
    client = api.client

    created = client.post(f"{V1}/datasets", json={"slug": "m6empty", "modality": "image"})
    assert created.status_code == 201, created.text
    assert created.json()["version_count"] == 0 and created.json()["latest_version"] is None
    again = client.post(f"{V1}/datasets", json={"slug": "m6empty", "modality": "image"})
    assert again.status_code == 409 and again.json()["code"] == "conflict"
    bad = client.post(f"{V1}/datasets", json={"slug": "Bad Slug", "modality": "image"})
    assert bad.status_code == 422 and bad.json()["errors"][0]["loc"][-1] == "slug"

    listed = client.get(f"{V1}/datasets").json()
    slugs = [d["slug"] for d in listed["items"]]
    assert set(slugs) == {"m6list", "m6empty"}

    dataset = client.get(f"{V1}/datasets/m6list").json()
    versions = client.get(f"{V1}/datasets/m6list/versions").json()["items"]
    stages = [v["stage"] for v in versions]
    assert stages[-1] == "ingest" and "split" in stages
    created_at = [v["created_at"] for v in versions]
    assert created_at == sorted(created_at, reverse=True), "newest first"
    assert dataset["version_count"] == len(versions)
    assert dataset["latest_version"]["version_id"] == versions[0]["version_id"]
    assert dataset["stored_bytes"] == sum(
        v["manifest_bytes"] + v["new_blob_bytes"] for v in versions
    )
    assert all(v["created_by_job_id"] == job["id"] for v in versions)
    assert all(v["created_by_job_state"] == "completed" for v in versions)

    page = client.get(f"{V1}/datasets/m6list/versions", params={"limit": 2}).json()
    rest = client.get(
        f"{V1}/datasets/m6list/versions", params={"limit": 500, "cursor": page["next_cursor"]}
    ).json()
    assert [v["version_id"] for v in page["items"] + rest["items"]] == [
        v["version_id"] for v in versions
    ]
    assert rest["next_cursor"] is None

    missing = client.get(f"{V1}/datasets/nope")
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"


def test_DATA_3_3_lineage_endpoint_walks_to_raw_and_down_to_descendants(
    runner: Runner, tmp_path: Path
) -> None:
    """DATA-3.3.AC1 through the API: from any version back to raw, and down again."""
    api = PipelineApi(runner)
    job = _pipeline(api, tmp_path, "m6lin")
    client = api.client
    final = job["output_version_id"]

    lineage = client.get(f"{V1}/versions/{final}/lineage").json()
    ancestors = lineage["ancestors"]
    assert ancestors[-1]["stage"] == "ingest" and ancestors[-1]["parent_version_id"] is None
    chain = [final] + [a["version_id"] for a in ancestors]
    for child, parent in itertools.pairwise(chain):
        assert client.get(f"{V1}/versions/{child}").json()["parent_version_id"] == parent
    assert lineage["descendants"] == [] and lineage["in_progress"] == []

    raw = ancestors[-1]["version_id"]
    down = client.get(f"{V1}/versions/{raw}/lineage").json()
    assert down["ancestors"] == []
    assert [d["version_id"] for d in down["descendants"]] == list(reversed(chain[:-1]))

    version = client.get(f"{V1}/versions/{final}").json()
    assert version["dataset"] == "m6lin" and len(version["content_hash"]) == 64
    assert version["stage"] == "split" and version["config"]["seed"] == 0
    assert client.get(f"{V1}/versions/ds_000000000000").json()["code"] == "not_found"


def test_DATA_3_1_rows_page_and_filter_by_status_reason_split_group_and_key(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    job = _pipeline(api, tmp_path, "m6rows", exact_copies=2, undersized=1)
    client = api.client
    final = job["output_version_id"]
    url = f"{V1}/versions/{final}/rows"

    everything = client.get(url, params={"limit": 500}).json()
    rows = everything["items"]
    assert everything["matched"] == len(rows) and everything["next_cursor"] is None
    assert [r["row_key"] for r in rows] == sorted(r["row_key"] for r in rows)

    pages: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 4, **({"cursor": cursor} if cursor else {})}
        page = client.get(url, params=params).json()
        pages.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == rows, "pages join up to the whole manifest"

    duplicates = client.get(url, params={"reason_code": "exact_duplicate", "limit": 500}).json()
    assert duplicates["matched"] == 2
    assert all(r["status"] == "duplicate" for r in duplicates["items"])
    group = duplicates["items"][0]["dup_group_id"]
    members = client.get(url, params={"dup_group_id": group}).json()["items"]
    assert len(members) >= 2 and any(m["status"] == "accepted" for m in members)

    rejected = client.get(url, params={"reason_code": "below_min_size"}).json()
    assert rejected["matched"] == 1 and rejected["items"][0]["status"] == "rejected"

    train = client.get(url, params={"split": "train", "limit": 500}).json()
    assert train["matched"] > 0 and all(r["split"] == "train" for r in train["items"])

    key = rows[0]["row_key"]
    found = client.get(url, params={"q": key[-8:]}).json()
    assert key in [r["row_key"] for r in found["items"]]

    bad = client.get(url, params={"status": "lost"})
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error"


def test_DATA_3_1_stats_count_statuses_reasons_and_scores(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    job = _pipeline(api, tmp_path, "m6stats", exact_copies=2, undersized=1)
    client = api.client
    final = job["output_version_id"]

    stats = client.get(f"{V1}/versions/{final}/stats").json()
    version = client.get(f"{V1}/versions/{final}").json()
    assert sum(stats["by_status"].values()) == stats["row_count"] == version["row_count"]
    assert stats["by_status"]["accepted"] == version["counts"]["accepted"]
    reasons = {(r["reason_code"], r["decided_at_stage"]): r["rows"] for r in stats["reasons"]}
    assert reasons[("exact_duplicate", "clean")] == 2
    assert reasons[("below_min_size", "ingest")] == 1
    assert reasons[("corrupt_image", "ingest")] == 1
    assert stats["by_stage"]["ingest"] == {"below_min_size": 1, "corrupt_image": 1}
    accepted = stats["by_status"]["accepted"]
    assert sum(stats["by_split"].values()) == accepted
    assert stats["size_bytes"]["count"] == accepted
    assert stats["clip_score"]["count"] == accepted


def test_DATA_3_4_blobs_serve_files_and_thumbnails_by_hash(runner: Runner, tmp_path: Path) -> None:
    api = PipelineApi(runner)
    job = _pipeline(api, tmp_path, "m6blob")
    client = api.client
    row = client.get(
        f"{V1}/versions/{job['output_version_id']}/rows", params={"status": "accepted"}
    ).json()["items"][0]

    original = client.get(f"{V1}/blobs/{row['sha256']}")
    assert original.status_code == 200 and original.headers["content-type"].startswith("image/")
    assert "immutable" in original.headers["cache-control"]
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.size == (row["width"], row["height"])

    thumb = client.get(f"{V1}/blobs/{row['derived_sha256']}", params={"w": 64})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.width == 64

    assert client.get(f"{V1}/blobs/{'0' * 64}").json()["code"] == "not_found"
    assert client.get(f"{V1}/blobs/..%2Fetc").status_code in (404, 422)
    assert client.get(f"{V1}/blobs/{row['sha256']}", params={"w": 4}).status_code == 422


def test_UI_2_1_sources_lists_folders_with_file_counts(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sources"
    make_image_folder(root / "birds", valid=3)
    (root / "empty").mkdir()
    (root / ".hidden").mkdir()
    (root / "loose.txt").write_text("not a folder")
    monkeypatch.setattr(get_settings(), "sources_dir", root)

    items = PipelineApi(runner).client.get(f"{V1}/sources").json()["items"]

    assert [(i["name"], i["files"]) for i in items] == [("birds", 3), ("empty", 0)]
    assert items[0]["path"] == f"{root.as_posix()}/birds"
