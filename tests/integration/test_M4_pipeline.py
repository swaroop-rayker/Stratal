"""M4 against the real PostgreSQL and Redis: the pipeline through label and filter as jobs.

The owner's units and the models run as fakes (``stratal.testing.fake_clean`` and
``stratal.testing.fake_label``, loaded in the child processes through
``STRATAL_EXTRA_STAGES``), so this runs without a GPU. Everything else, the
derivation cache, memoization, version registry, lease plumbing and job
runtime, is the production code. The real models are tested in ``tests/gpu``.
"""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.storage.manifest import STATUS_ACCEPTED
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi as Api
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration


@pytest.fixture
def fakes(fake_units: None) -> None:
    """The owner's units and the models as fakes (the shared ``fake_units`` fixture)."""


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


def test_API_a_pipeline_from_the_final_version_runs_only_the_report(
    runner: Runner, fakes: None, tmp_path: Path
) -> None:
    """Since M5 the last stage, evaluate, makes no version: a pipeline started from
    the final (split) version only reports on it again. Before M5 this was refused
    as having nothing after it."""
    api = Api(runner)
    done = api.run(
        {
            "dataset": "m4end",
            "source": {"path": _source(tmp_path)},
            "stages": {"ingest": {"min_size": 32}},
        }
    )

    again = api.run({"from_version": done["output_version_id"]})

    assert [s["name"] for s in again["stages"]] == ["evaluate"]
    assert again["output_version_id"] == done["output_version_id"]
