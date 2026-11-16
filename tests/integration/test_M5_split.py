"""M5 against the real PostgreSQL and Redis: the split stage as part of a pipeline job.

The owner's units run as fakes (``stratal.testing.fake_clean``, ``fake_label``
and ``fake_split``, loaded in the child processes through
``STRATAL_EXTRA_STAGES``). What is tested here is the agent's runner: the
database lookup of the split to continue (DATA-4.5), its place in the config
and so in the version's identity (DATA-4.4), memoization, and the API refusing
bad proportions (DATA-4.1.AC2).
"""

import shutil
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


def _version(version_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """A version's config, counts and rows by key."""
    with Session(get_engine()) as session:
        version = registry.get_version(session, version_id)
        rows = {
            r["row_key"]: r
            for r in registry.load_version(session, get_settings().data_dir, version_id)
        }
        return dict(version.config), dict(version.counts), rows


def _stage(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in job["stages"] if s["name"] == name)


def _pipeline(slug: str, source: Path, **split: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dataset": slug,
        "source": {"path": str(source)},
        "stages": {"ingest": {"min_size": 32}},
    }
    if split:
        body["stages"]["split"] = split
    return body


def test_DATA_4_1_the_pipeline_runs_through_the_split_as_one_job(
    runner: Runner, tmp_path: Path
) -> None:
    source = make_image_folder(tmp_path / "src", valid=12, corrupt=1).root

    job = Api(runner).run(_pipeline("m5split", source))

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]][-3:] == ["filter", "split", "evaluate"]
    config, counts, rows = _version(job["output_version_id"])
    assert config["continues_version_id"] is None
    with Session(get_engine()) as session:
        split_version = registry.get_version(session, job["output_version_id"])
        filter_version = registry.get_version(session, _stage(job, "filter")["output_version_id"])
        assert split_version.dtype == filter_version.dtype == "image_caption", "DATA-5.1"
        assert filter_version.counts["validation"]["rejected_rows"] == []
    accepted = [r for r in rows.values() if r["status"] == "accepted"]
    assert len(accepted) == 12 == counts["split"]["accepted"]
    assert all(r["split"] in {"train", "val", "test"} and r["split_group"] for r in accepted)
    assert all(r["split"] is None for r in rows.values() if r["status"] != "accepted")
    assert _stage(job, "split")["metrics"]["leakage_violations"] == 0


def test_DATA_4_1_bad_proportions_are_refused_with_validation_error(
    runner: Runner, tmp_path: Path
) -> None:
    source = make_image_folder(tmp_path / "src", valid=3).root

    response = Api(runner).post(_pipeline("m5bad", source, train=0.9, val=0.2, test=0.1))

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["loc"][:2] == ["stages", "split"]
    assert "sum to 1" in body["errors"][0]["msg"]


def test_DATA_4_4_rerunning_the_split_gives_the_same_version_and_a_new_seed_another(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    first = api.run(_pipeline("m5rerun", make_image_folder(tmp_path / "src", valid=20).root))
    filtered = _stage(first, "filter")["output_version_id"]

    again = api.run({"from_version": filtered})
    reseeded = api.run({"from_version": filtered, "stages": {"split": {"seed": 1}}})

    assert again["output_version_id"] == first["output_version_id"]
    assert again["stages"][0]["cache_hit"] is True
    assert reseeded["output_version_id"] != first["output_version_id"]
    config, _, _ = _version(reseeded["output_version_id"])
    assert (config["seed"], config["continues_version_id"]) == (1, None)


def test_DATA_4_5_a_new_version_of_the_dataset_continues_its_earlier_split(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    small = make_image_folder(tmp_path / "small", valid=15, seed=1).root
    big = tmp_path / "big"
    shutil.copytree(small, big)
    extra = make_image_folder(tmp_path / "extra", valid=5, seed=2).root
    shutil.copytree(extra, big / "added")

    first = api.run(_pipeline("m5carry", small))
    second = api.run(_pipeline("m5carry", big))
    other_seed = api.run(_pipeline("m5carry", big, seed=9))

    config, counts, rows = _version(second["output_version_id"])
    assert config["continues_version_id"] == first["output_version_id"]
    _, _, before = _version(first["output_version_id"])
    for key, row in before.items():
        if row["status"] == "accepted":
            assert rows[key]["split"] == row["split"], key
    report = counts["split"]
    assert (report["carried_over"], report["newly_assigned"], report["moved"]) == (15, 5, 0)
    seed_config, _, _ = _version(other_seed["output_version_id"])
    assert seed_config["continues_version_id"] is None, "a split with another seed is not continued"


def test_DATA_4_4_a_continues_version_id_given_by_the_user_is_refused(
    runner: Runner, tmp_path: Path
) -> None:
    source = make_image_folder(tmp_path / "src", valid=4).root

    job = Api(runner).run(_pipeline("m5cont", source, continues_version_id="ds_000000000000"))

    assert job["state"] == "failed"
    assert job["error"]["stage"] == "split"
    assert "continues_version_id" in job["error"]["reason"]
