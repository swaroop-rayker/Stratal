"""MON-2.1, RUN-1.1 slice, UI-2.1: the endpoints the phase-1 screens need beyond §3.2:
``GET /system`` (SCR-01), the config schemas (SCR-02's drawer) and the SCR-07 job filters.
"""

from pathlib import Path

import pytest

from stratal.core.bus import Bus
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration

V1 = "/api/v1"


def test_MON_2_1_system_reports_totals_and_the_vram_cap(runner: Runner, bus: Bus) -> None:
    client = PipelineApi(runner).client

    unknown = client.get(f"{V1}/system").json()
    assert unknown["vram_total_mb"] is None and unknown["vram_cap_mb"] is None, "null, not zero"

    bus.set_system(
        {
            "gpu_name": "RTX 4050",
            "gpu_error": None,
            "vram_total_mb": 6141.0,
            "ram_total_mb": 10000.0,
            "cpu_count": 16,
        }
    )
    known = client.get(f"{V1}/system").json()

    assert known["gpu_name"] == "RTX 4050" and known["ram_total_mb"] == 10000.0
    assert known["vram_cap_mb"] == pytest.approx(6141.0 * known["vram_cap_fraction"])


def test_UI_2_1_config_schemas_and_defaults_cover_every_stage(runner: Runner) -> None:
    client = PipelineApi(runner).client

    names = [s["name"] for s in client.get(f"{V1}/config/schemas").json()["items"]]
    assert names[:7] == ["ingest", "clean", "preprocess", "label", "filter", "split", "evaluate"]

    schema = client.get(f"{V1}/config/schemas/preprocess").json()
    assert schema["type"] == "object" and "max_side" in schema["properties"]
    defaults = client.get(f"{V1}/config/defaults").json()
    assert defaults["preprocess"]["max_side"] == schema["properties"]["max_side"]["default"]
    assert client.get(f"{V1}/config/schemas/nope").json()["code"] == "not_found"


@pytest.mark.usefixtures("fake_units")
def test_RUN_1_1_jobs_filter_by_state_list_and_dataset_with_counts(
    runner: Runner, tmp_path: Path
) -> None:
    api = PipelineApi(runner)
    source = make_image_folder(tmp_path / "src", valid=6).root
    body = {
        "source": {"path": str(source)},
        "stages": {"ingest": {"min_size": 32}},
        "until_stage": "ingest",
    }
    done = api.run({"dataset": "m6a", **body})
    queued = api.post({"dataset": "m6b", **body}).json()  # never run: stays queued
    api.queued.clear()
    client = api.client

    both = client.get(f"{V1}/jobs", params={"state": "completed,queued"}).json()
    assert {j["id"] for j in both["items"]} == {done["id"], queued["id"]}
    assert both["counts"]["completed"] == 1 and both["counts"]["queued"] == 1
    assert both["counts"]["failed"] == 0

    only_b = client.get(f"{V1}/jobs", params={"dataset": "m6b"}).json()
    assert [j["id"] for j in only_b["items"]] == [queued["id"]]
    assert only_b["counts"]["completed"] == 0, "counts honour the other filters"

    bad = client.get(f"{V1}/jobs", params={"state": "running,lost"})
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error"
