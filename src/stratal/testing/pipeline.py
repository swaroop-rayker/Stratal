"""Run pipeline jobs through the real API and job runtime, with the owner's units faked.

For integration tests from M5 on: :class:`PipelineApi` posts a job, runs its
stages in this process's harness and returns the finished job. The
``fake_units`` fixture in ``tests/integration/conftest.py`` names
:data:`FAKE_MODULES` in ``STRATAL_EXTRA_STAGES`` (so child processes load them
too) and calls :func:`restore_real_units` afterwards.
"""

from typing import Any

from fastapi.testclient import TestClient

from stratal.api.app import create_app
from stratal.api.jobs import get_enqueue
from stratal.data.clean import OWNER_UNITS as DEDUPE_UNITS
from stratal.data.clean import CleanConfig
from stratal.data.split_stage import OWNER_UNITS as SPLIT_UNITS
from stratal.data.split_stage import SplitConfig
from stratal.eval.report import OWNER_STATS, EvaluateConfig
from stratal.label.filter_stage import FilterConfig
from stratal.label.filtering import filter_decisions
from stratal.label.stage import OWNER_UNITS as LABEL_UNITS
from stratal.label.stage import LabelConfig
from stratal.runtime import jobs as job_service
from stratal.runtime import stages as stage_registry
from stratal.testing.harness import Runner

FAKE_MODULES = (
    "stratal.testing.stages",
    "stratal.testing.fake_clean",
    "stratal.testing.fake_label",
    "stratal.testing.fake_split",
    "stratal.testing.fake_eval",
)


def restore_real_units() -> None:
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
    register(
        StageDef(
            "evaluate", "cpu", EvaluateConfig, stage_registry.make_evaluate_runner(OWNER_STATS)
        )
    )


class PipelineApi:
    """The API app with enqueueing redirected to a test :class:`Runner`."""

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
