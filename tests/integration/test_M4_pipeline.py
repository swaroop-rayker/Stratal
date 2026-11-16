"""M4 against the real PostgreSQL and Redis: the pipeline through label and filter as jobs.

The owner's units and the models run as fakes (``stratal.testing.fake_clean`` and
``stratal.testing.fake_label``, loaded in the child processes through
``STRATAL_EXTRA_STAGES``), so this runs without a GPU. Everything else, the
derivation cache, memoization, version registry, lease plumbing and job
runtime, is the production code. The real models are tested in ``tests/gpu``.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from stratal.api.app import create_app
from stratal.api.jobs import get_enqueue
from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.clean import OWNER_UNITS as DEDUPE_UNITS
from stratal.data.clean import CleanConfig
from stratal.data.split_stage import OWNER_UNITS as SPLIT_UNITS
from stratal.data.split_stage import SplitConfig
from stratal.db.engine import get_engine
from stratal.label.filter_stage import FilterConfig
from stratal.label.filtering import filter_decisions
from stratal.label.stage import OWNER_UNITS as LABEL_UNITS
from stratal.label.stage import LabelConfig
from stratal.runtime import jobs as job_service
from stratal.runtime import stages as stage_registry
from stratal.storage.manifest import STATUS_ACCEPTED
from stratal.testing.harness import Runner
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration

FAKES = (
    "stratal.testing.stages,stratal.testing.fake_clean,stratal.testing.fake_label,"
    "stratal.testing.fake_split"
)


def _restore_real_stages() -> None:
    register, StageDef = stage_registry.register, stage_registry.StageDef
    register(StageDef("clean", "cpu", CleanConfig, stage_registry.make_clean_runner(DEDUPE_UNITS)))
    register(
        StageDef(
            "label",
            "gpu",
            LabelConfig,
            stage_registry.make_label_runner(stage_registry.real_label_models, LABEL_UNITS),
        )
    )
    register(
        StageDef("filter", "cpu", FilterConfig, stage_registry.make_filter_runner(filter_decisions))
    )
    register(StageDef("split", "cpu", SplitConfig, stage_registry.make_split_runner(SPLIT_UNITS)))


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STRATAL_EXTRA_STAGES", FAKES)
    stage_registry.load_extra_stages()
    yield
    _restore_real_stages()


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


def _rows(version_id: str) -> dict[str, dict[str, Any]]:
    with Session(get_engine()) as session:
        return {
            r["row_key"]: r
            for r in registry.load_version(session, get_settings().data_dir, version_id)
        }


def _source(tmp_path: Path) -> str:
    return str(make_image_folder(tmp_path / "src", valid=8, corrupt=1).root)


def test_LABEL_1_1_the_pipeline_runs_all_five_stages_as_one_job(
    runner: Runner, fakes: None, tmp_path: Path
) -> None:
    job = Api(runner).run(
        {
            "dataset": "m4demo",
            "source": {"path": _source(tmp_path)},
            "stages": {"ingest": {"min_size": 32}, "preprocess": {"max_side": 128}},
            "until_stage": "filter",
        }
    )

    assert job["state"] == "completed", job
    assert [(s["name"], s["status"]) for s in job["stages"]] == [
        (name, "completed") for name in ("ingest", "clean", "preprocess", "label", "filter")
    ]
    label = job["stages"][3]["metrics"]
    assert (label["rows_labelled"], label["clip_score_mean"]) == (8, 0.5)
    rows = _rows(job["output_version_id"])
    accepted = [r for r in rows.values() if r["status"] == STATUS_ACCEPTED]
    assert len(accepted) == 8
    assert all(r["caption"].startswith("a ") and r["clip_score"] == 0.5 for r in accepted)
    assert {r["caption_model"] for r in accepted} == {"florence-2-base"}


def test_LABEL_1_4_a_new_threshold_reruns_only_the_filter(
    runner: Runner, fakes: None, tmp_path: Path
) -> None:
    api = Api(runner)
    first = api.run(
        {
            "dataset": "m4thr",
            "source": {"path": _source(tmp_path)},
            "stages": {"ingest": {"min_size": 32}},
        }
    )
    labelled = first["stages"][3]["output_version_id"]

    rerun = {
        "from_version": labelled,
        "stages": {"filter": {"clip_threshold": 0.3}},
        "until_stage": "filter",
    }
    changed = api.run(rerun)
    same = api.run(rerun)

    assert [s["name"] for s in changed["stages"]] == ["filter"], "nothing is captioned again"
    assert changed["output_version_id"] != first["output_version_id"]
    assert same["stages"][0]["cache_hit"] is True
    assert same["output_version_id"] == changed["output_version_id"]


def test_API_a_version_from_the_last_stage_has_nothing_after_it(
    runner: Runner, fakes: None, tmp_path: Path
) -> None:
    api = Api(runner)
    done = api.run(
        {
            "dataset": "m4end",
            "source": {"path": _source(tmp_path)},
            "stages": {"ingest": {"min_size": 32}},
        }
    )

    response = api.post({"from_version": done["output_version_id"]})

    assert response.status_code == 422
    assert "nothing after it" in response.json()["detail"]
