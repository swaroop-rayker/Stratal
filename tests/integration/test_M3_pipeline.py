"""M3 against the real PostgreSQL and Redis: the pipeline through clean and preprocess.

The clean stage runs with fakes for the owner's units (``stratal.testing.fake_clean``),
loaded into the child processes through ``STRATAL_EXTRA_STAGES``. Everything
else is the production code.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stratal.api.app import create_app
from stratal.api.jobs import get_enqueue
from stratal.core.settings import get_settings
from stratal.data import dedupe, registry
from stratal.data.clean import OWNER_UNITS, CleanConfig
from stratal.db.engine import get_engine
from stratal.runtime import jobs as job_service
from stratal.runtime import stages as stage_registry
from stratal.storage.derivations import PgDerivationCache
from stratal.testing.harness import Runner
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration

WITH_FAKE_CLEAN = "stratal.testing.stages,stratal.testing.fake_clean"


@pytest.fixture
def fake_clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the clean stage with fakes, here and in the child processes."""
    monkeypatch.setenv("STRATAL_EXTRA_STAGES", WITH_FAKE_CLEAN)
    stage_registry.load_extra_stages()
    yield
    stage_registry.register(
        stage_registry.StageDef(
            "clean", "cpu", CleanConfig, stage_registry.make_clean_runner(OWNER_UNITS)
        )
    )


class Api:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self.queued: list[job_service.Enqueue] = []
        app = create_app()
        app.dependency_overrides[get_enqueue] = lambda: self.queued.append
        self.client = TestClient(app)

    def post(self, body: dict[str, Any]) -> Any:
        return self.client.post("/api/v1/jobs", json={"kind": "pipeline", **body})

    def run(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self.post(body)
        assert response.status_code == 202, response.text
        self.runner.queue.extend(self.queued)
        self.queued.clear()
        self.runner.run_queued()
        self.runner.collect()
        return self.runner.job(response.json()["id"])


def _source(tmp_path: Path, **planted: int) -> str:
    return str(make_image_folder(tmp_path / "src", **planted).root)


def test_ADR_0004_postgres_derivation_cache_round_trips_and_never_overwrites(
    runner: Runner,
) -> None:
    cache = PgDerivationCache(get_engine(), job_id=None)
    cache.put_many("phash", "p1", {"a" * 64: "00ff", "b" * 64: "0f0f"})
    cache.put_many("phash", "p1", {"a" * 64: "ffff"})  # same key: ignored (INV-11)

    assert cache.get_many("phash", "p1", ["a" * 64, "b" * 64, "c" * 64]) == {
        "a" * 64: "00ff",
        "b" * 64: "0f0f",
    }
    assert cache.get_many("phash", "p2", ["a" * 64]) == {}, "other params, other value"
    assert cache.get_many("preprocess", "p1", ["a" * 64]) == {}


def test_DATA_6_1_pipeline_runs_ingest_clean_and_preprocess_as_one_job(
    runner: Runner, fake_clean: None, tmp_path: Path
) -> None:
    api = Api(runner)
    job = api.run(
        {
            "dataset": "m3demo",
            "source": {"path": _source(tmp_path, valid=10, corrupt=1, exif_rotated=2)},
            "stages": {"ingest": {"min_size": 32}, "preprocess": {"max_side": 128}},
        }
    )

    assert job["state"] == "completed", job
    assert [(s["name"], s["status"]) for s in job["stages"]] == [
        ("ingest", "completed"),
        ("clean", "completed"),
        ("preprocess", "completed"),
    ]
    ingest, clean, preprocess = job["stages"]
    assert clean["input_version_id"] == ingest["output_version_id"]
    assert preprocess["input_version_id"] == clean["output_version_id"]
    assert job["output_version_id"] == preprocess["output_version_id"]
    with Session(get_engine()) as session:
        chain = registry.lineage(session, job["output_version_id"])
        assert [v.stage for v in chain] == ["preprocess", "clean", "ingest"]
        assert all(v.created_by_job_id == job["id"] for v in chain)
        assert chain[0].config["max_side"] == 128
        rows = list(registry.load_version(session, get_settings().data_dir, chain[0].version_id))
    accepted = [r for r in rows if r["status"] == "accepted"]
    assert len(accepted) == 12 and all(r["derived_sha256"] for r in accepted)

    messages = [e["message"] for e in runner.events(job["id"])]
    assert "Hashing images (pHash) 12/12" in messages
    assert "Comparing hashes: 0 near-duplicate groups found" in messages
    assert "Resizing to 128px · normalizing 12/12" in messages


def test_ADR_0004_a_rerun_from_the_raw_version_reuses_both_stages(
    runner: Runner, fake_clean: None, tmp_path: Path
) -> None:
    api = Api(runner)
    body = {"stages": {"preprocess": {"max_side": 128}}}
    first = api.run(
        {
            "dataset": "memo",
            "source": {"path": _source(tmp_path, valid=6)},
            "stages": {"ingest": {"min_size": 32}, **body["stages"]},
        }
    )
    raw = first["stages"][0]["output_version_id"]

    again = api.run({"from_version": raw, **body})
    changed = api.run({"from_version": raw, "stages": {"preprocess": {"max_side": 96}}})

    assert again["state"] == "completed"
    assert [s["name"] for s in again["stages"]] == ["clean", "preprocess"]
    assert again["dataset"] == "memo", "the dataset comes from the version"
    assert all(s["cache_hit"] for s in again["stages"])
    assert again["output_version_id"] == first["output_version_id"]
    assert any(e["message"].startswith("Reusing ds_") for e in runner.events(again["id"]))
    assert changed["stages"][0]["cache_hit"], "clean's input and config are unchanged"
    assert not changed["stages"][1]["cache_hit"]
    assert changed["output_version_id"] != first["output_version_id"]


def test_API_from_version_requests_are_validated(
    runner: Runner, fake_clean: None, tmp_path: Path
) -> None:
    api = Api(runner)
    source = _source(tmp_path, valid=3)
    done = api.run(
        {"dataset": "fv", "source": {"path": source}, "stages": {"ingest": {"min_size": 32}}}
    )
    raw, last = done["stages"][0]["output_version_id"], done["output_version_id"]

    unknown = api.post({"from_version": "ds_000000000000"})
    both = api.post({"dataset": "fv", "source": {"path": source}, "from_version": raw})
    neither = api.post({"dataset": "fv"})
    no_dataset = api.post({"source": {"path": source}})
    other_dataset = api.post({"dataset": "other", "from_version": raw})
    nothing_left = api.post({"from_version": last})
    earlier_stage = api.post({"from_version": raw, "stages": {"ingest": {"min_size": 64}}})
    until_before = api.post({"from_version": raw, "until_stage": "ingest"})

    for response in (unknown, both, neither, no_dataset, other_dataset, nothing_left):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "validation_error"
    assert unknown.json()["errors"][0]["loc"] == ["from_version"]
    assert "nothing after it" in nothing_left.json()["detail"]
    assert earlier_stage.status_code == 422 and "ingest" in earlier_stage.json()["detail"]
    assert until_before.status_code == 422
    assert api.queued == [], "no rejected request queued anything"


def test_BUG1_a_registered_version_is_found_by_id_and_a_wrong_manifest_is_caught(
    runner: Runner, fake_clean: None, tmp_path: Path
) -> None:
    api = Api(runner)
    job = api.run(
        {
            "dataset": "bug1",
            "source": {"path": _source(tmp_path, valid=4)},
            "stages": {"ingest": {"min_size": 32}},
            "until_stage": "clean",
        }
    )
    data_dir = get_settings().data_dir
    raw, cleaned = (s["output_version_id"] for s in job["stages"])

    with Session(get_engine()) as session:
        version, path = registry.manifest_for(session, data_dir, cleaned)
        assert version.stage == "clean" and path.is_file()
        raw_path = registry.manifest_for(session, data_dir, raw)[1]
        # Put the raw manifest where the cleaned one should be: the metadata no longer matches.
        path.chmod(0o644)
        path.write_bytes(raw_path.read_bytes())
        with pytest.raises(registry.VersionDataMissing, match="does not match"):
            registry.manifest_for(session, data_dir, cleaned)
        path.unlink()
        with pytest.raises(registry.VersionDataMissing, match="missing"):
            list(registry.load_version(session, data_dir, cleaned))


def _owner_dedupe_written() -> bool:
    try:
        dedupe.group_exact_duplicates([])
    except NotImplementedError:
        return False
    return True


@pytest.mark.skipif(_owner_dedupe_written(), reason="DATA-2.1 is written; nothing to report")
def test_ADR_0012_until_dedupe_is_written_the_clean_stage_fails_with_a_clear_reason(
    runner: Runner, tmp_path: Path
) -> None:
    assert "fake_clean" not in os.environ.get("STRATAL_EXTRA_STAGES", "")
    job = Api(runner).run(
        {
            "dataset": "real",
            "source": {"path": _source(tmp_path, valid=3)},
            "stages": {"ingest": {"min_size": 32}},
        }
    )

    assert job["state"] == "failed"
    assert [s["status"] for s in job["stages"][:2]] == ["completed", "failed"]
    assert job["stages"][2]["status"] != "completed"
    error = job["stages"][1]["error"]
    assert error["code"] == "stage_failed"
    assert "DATA-2.1" in error["reason"]
    assert 'until_stage = "ingest"' in error["suggested_action"]
