"""RUN-1.3, RUN-1.6, RUN-1.7: the training limits stored in the database and edited on
SCR-14 (OC-9), against the real PostgreSQL and Redis."""

from pathlib import Path
from typing import Any

import pytest

from stratal.core.bus import Bus
from stratal.core.settings import get_settings
from stratal.db.engine import get_engine
from stratal.mon.__main__ import _training_limits  # pyright: ignore[reportPrivateUsage]
from stratal.mon.watchdog import Watchdog
from stratal.testing.harness import Runner
from stratal.testing.labelled import labelled_split
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _training(api: PipelineApi) -> dict[str, Any]:
    sections = api.client.get("/api/v1/settings").json()["sections"]
    return next(s for s in sections if s["name"] == "training")


def _save(api: PipelineApi, **changes: Any) -> Any:
    section = _training(api)
    body = {
        "section": "training",
        "values": {**section["values"], **changes},
        "version": section["version"],
    }
    return api.client.put("/api/v1/settings", json=body)


def test_RUN_1_7_a_saved_stall_timeout_applies_to_the_watchdog_at_once(runner: Runner) -> None:
    api = PipelineApi(runner)
    env = get_settings()
    section = _training(api)
    assert section["values"]["stall_timeout_s"] == round(env.stall_timeout_s), "env default"
    assert section["schema"]["properties"]["stall_timeout_s"]["x-applies"] == "at once"
    watchdog = Watchdog(
        get_engine(),
        enqueue=lambda _item: None,
        temperature=lambda: None,
        limits=lambda: _training_limits(get_engine(), env),
    )

    saved = _save(api, stall_timeout_s=600)

    assert saved.status_code == 200, saved.text
    watchdog.run_once()
    assert watchdog.stall_after_s == 600.0
    assert api.client.get("/api/v1/system").json()["stall_timeout_s"] == 600


def test_RUN_1_6_thermal_limits_are_checked_and_saved(runner: Runner, bus: Bus) -> None:
    api = PipelineApi(runner)
    bus.set_system({"gpu_slowdown_c": 87.0})
    assert _training(api)["context"]["gpu_slowdown_c"] == 87.0

    too_close = _save(api, thermal_pause_c=80, thermal_resume_c=78)
    too_hot = _save(api, thermal_pause_c=90, thermal_resume_c=75)

    assert too_close.status_code == 422
    [problem] = too_close.json()["errors"]
    assert problem["loc"] == ["values", "thermal_resume_c"] and "at most 75" in problem["msg"]
    assert too_hot.status_code == 422
    assert too_hot.json()["errors"][0]["loc"] == ["values", "thermal_pause_c"]
    assert _save(api, thermal_pause_c=80, thermal_resume_c=70).status_code == 200
    system = api.client.get("/api/v1/system").json()
    assert (system["thermal_pause_c"], system["thermal_resume_c"]) == (80, 70)
    assert system["gpu_slowdown_c"] == 87.0


def test_RUN_1_6_a_stale_settings_version_is_refused(runner: Runner) -> None:
    api = PipelineApi(runner)
    stale = _training(api)
    assert _save(api, stall_timeout_s=900).status_code == 200

    refused = api.client.put(
        "/api/v1/settings",
        json={"section": "training", "values": stale["values"], "version": stale["version"]},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "conflict" and refused.json()["version"]


def test_RUN_1_3_the_checkpoint_interval_is_the_default_of_new_runs(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    version = labelled_split(runner, tmp_path / "src", "m8ckpt")
    assert _save(api, checkpoint_interval_min=10).status_code == 200
    config = {"model": {"name": "mlp"}, "device": "cpu"}

    plain = api.client.post(
        "/api/v1/jobs", json={"kind": "train", "dataset_version": version, "config": config}
    ).json()
    given = api.client.post(
        "/api/v1/jobs",
        json={
            "kind": "train",
            "dataset_version": version,
            "config": {**config, "checkpoint_every_s": 120},
        },
    ).json()

    assert plain["stages"][0]["config"]["checkpoint_every_s"] == 600
    assert given["stages"][0]["config"]["checkpoint_every_s"] == 120, "a run's own value wins"
