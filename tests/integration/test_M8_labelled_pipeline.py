"""DATA-5.2 (labelled images, ADR-0014 rule 3) against the real PostgreSQL and Redis.

A labelled-images pipeline runs ingest, clean, preprocess and split, with labels
from class folders and the split stratified by that folder. The owner's units
run as fakes, as in the M5 tests; what is tested is the request handling, the
stage order per type and the type carried from version to version.
"""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi as Api
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _classes(root: Path) -> Path:
    """Two class folders of six distinct images each, and one stray file at the root."""
    for seed, label in enumerate(("cat", "dog"), start=1):
        make_image_folder(root / label, valid=6, size=48, seed=seed)
    make_image_folder(root / "tmp-stray", valid=1, size=48, seed=9)
    (root / "tmp-stray" / "valid" / "img_00000.png").rename(root / "stray.png")
    return root


def _labelled(slug: str, source: Path, **stages: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": slug,
        "source": {"path": str(source)},
        "dataset_type": "labelled_images",
        "stages": {"ingest": {"min_size": 32}, **stages},
    }


def _version_rows(version_id: str) -> tuple[str | None, dict[str, Any], list[dict[str, Any]]]:
    with Session(get_engine()) as session:
        version = registry.get_version(session, version_id)
        rows = list(registry.load_version(session, get_settings().data_dir, version_id))
        return version.dtype, dict(version.config), rows


def test_DATA_5_2_a_labelled_pipeline_runs_ingest_clean_preprocess_split(
    runner: Runner, tmp_path: Path
) -> None:
    source = _classes(tmp_path / "src")

    job = Api(runner).run(_labelled("m8labelled", source))

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]] == ["ingest", "clean", "preprocess", "split"]
    for stage in job["stages"]:
        dtype, _, _ = _version_rows(stage["output_version_id"])
        assert dtype == "labelled_images", stage["name"]
    _, config, rows = _version_rows(job["output_version_id"])
    assert config["stratum_keys"] == ["source_folder"]
    assert config["leakage_distance"] == 6, "small images: the owner's 6 bits (OC-5)"
    clean = next(s for s in job["stages"] if s["name"] == "clean")
    assert _version_rows(clean["output_version_id"])[1]["phash_max_distance"] == 4
    accepted = [r for r in rows if r["status"] == "accepted"]
    assert len(accepted) == 12
    assert {r["label"] for r in accepted} == {"cat", "dog"}
    assert all(r["label"] == r["row_key"].split("/")[0] for r in accepted)
    assert all(r["split"] in {"train", "val", "test"} for r in accepted)
    stray = next(r for r in rows if r["row_key"] == "stray.png")
    assert (stray["status"], stray["reason_code"]) == ("rejected", "no_label")


def test_DATA_5_2_a_version_keeps_its_type_when_the_pipeline_continues_from_it(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    first = api.run({**_labelled("m8cont", _classes(tmp_path / "src")), "until_stage": "clean"})
    clean_version = first["output_version_id"]

    job = api.run({"from_version": clean_version})

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]] == ["preprocess", "split"]
    assert _version_rows(job["output_version_id"])[0] == "labelled_images"


@pytest.mark.parametrize(
    ("body_change", "loc"),
    [
        ({"stages": {"ingest": {"min_size": 32, "label_from": None}}}, "label_from"),
        (
            {"dataset_type": "image_caption", "stages": {"ingest": {"label_from": "folder"}}},
            "label_from",
        ),
        ({"stages": {"label": {}}}, None),
    ],
)
def test_DATA_5_2_inconsistent_requests_are_refused(
    runner: Runner, tmp_path: Path, body_change: dict[str, Any], loc: str | None
) -> None:
    body = {**_labelled("m8bad", _classes(tmp_path / "src")), **body_change}

    response = Api(runner).post(body)

    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "validation_error"
    if loc is not None:
        assert [e["loc"][-1] for e in problem["errors"]] == [loc]


def test_DATA_5_2_continuing_with_another_type_is_refused(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    first = api.run({**_labelled("m8other", _classes(tmp_path / "src")), "until_stage": "clean"})

    response = api.post(
        {"from_version": first["output_version_id"], "dataset_type": "image_caption"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["loc"] == ["dataset_type"]
