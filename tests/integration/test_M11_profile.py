"""UI-2.2, DATA-3.5, UI-2.1 (M11, SCR-03 v2): a version's distributions, the diff of two
versions, and the models trained on a version, against the real PostgreSQL and Redis.

The owner's units run as fakes. One source folder is ingested twice into one dataset,
with ``min_size`` 64 and then 32: a 48-pixel image is rejected the first time and accepted
the second, and a 20-pixel image is rejected both times with a different reason text.
"""

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _folder(root: Path) -> Path:
    root.mkdir()
    colours = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40), (40, 200, 200)]
    for i, colour in enumerate(colours * 2):
        image = Image.new("RGB", (96 + 8 * i, 80), colour)
        image.putpixel((i, i), (255, 255, 255))
        image.save(root / f"photo_{i:02d}.png")
    Image.new("RGB", (48, 48), (90, 90, 90)).save(root / "small_48.png")
    Image.new("RGB", (20, 20), (10, 10, 10)).save(root / "tiny_20.png")
    return root


def _stage(job: dict[str, Any], name: str) -> str:
    return next(s for s in job["stages"] if s["name"] == name)["output_version_id"]


@pytest.fixture
def two_runs(runner: Runner, tmp_path: Path) -> tuple[PipelineApi, dict[str, Any], dict[str, Any]]:
    api = PipelineApi(runner)
    source = _folder(tmp_path / "src")
    first = api.run(
        {
            "dataset": "profile",
            "source": {"path": str(source)},
            "stages": {"ingest": {"min_size": 64}},
        }
    )
    second = api.run(
        {
            "dataset": "profile",
            "source": {"path": str(source)},
            "stages": {"ingest": {"min_size": 32}},
        }
    )
    assert first["state"] == second["state"] == "completed"
    return api, first, second


def test_UI_2_2_distributions_show_the_funnel_and_raw_against_here(two_runs: Any) -> None:
    api, first, _second = two_runs
    body = api.client.get(f"/api/v1/versions/{first['output_version_id']}/distributions").json()

    funnel = {s["stage"]: s for s in body["funnel"]}
    assert [s["stage"] for s in body["funnel"]][:2] == ["ingest", "clean"]
    assert body["funnel"][-1]["stage"] == "split" and body["funnel"][-1]["parts"]
    ingest = funnel["ingest"]
    assert (ingest["rows_in"], ingest["rows_out"]) == (12, 10)
    assert ingest["removed"] == [{"reason_code": "below_min_size", "status": "rejected", "rows": 2}]
    assert body["funnel"][-1]["rows_out"] == body["accepted"]
    assert sum(body["funnel"][-1]["parts"].values()) == body["accepted"]

    width = next(c for c in body["columns"] if c["key"] == "width")
    assert width["raw"]["n"] == 12 and width["here"]["n"] == body["accepted"]
    assert width["raw"]["min"] == 20 and width["removed"]["n"] == 2
    assert sum(b["raw"] for b in width["bins"]) == 12
    assert sum(b["here"] for b in width["bins"]) == body["accepted"]
    assert body["formats"] == {"PNG": 12}
    caption = next(c for c in body["columns"] if c["key"] == "caption_words")
    assert caption["raw_label"] == "with a value", "captions exist only from the label stage on"


def test_DATA_3_5_the_diff_lists_the_changes_correctly(two_runs: Any) -> None:
    api, first, second = two_runs
    a, b = _stage(first, "preprocess"), _stage(second, "preprocess")
    body = api.client.get(f"/api/v1/versions/{b}/diff", params={"with": a}).json()

    assert (body["added"], body["removed"]) == (0, 0)
    assert body["changed"] == 1, "the 48-pixel image: rejected, then accepted"
    (row,) = body["changed_rows"]
    assert row["row_key"] == "small_48.png"
    fields = {f["field"]: (f["a"], f["b"]) for f in row["fields"]}
    assert fields["status"] == ("rejected", "accepted")
    assert fields["reason_code"] == ("below_min_size", None)
    assert body["wording_only"] == 1 and body["wording_rows"][0]["row_key"] == "tiny_20.png"
    assert body["same"] == 10
    assert body["config_diff"] == [{"stage": "ingest", "field": "min_size", "a": 64, "b": 32}]
    assert not body["stages_differ"]

    across = api.client.get(
        f"/api/v1/versions/{_stage(second, 'label')}/diff", params={"with": a}
    ).json()
    assert across["stages_differ"] and "caption" in across["only_in_b"], "listed once, not per row"


def test_UI_2_1_models_trained_on_a_version_and_the_cluster_filter_on_an_old_manifest(
    two_runs: Any,
) -> None:
    api, first, _second = two_runs
    split = first["output_version_id"]
    assert api.client.get(f"/api/v1/versions/{split}/models").json() == {"items": []}
    rows = api.client.get(f"/api/v1/versions/{split}/rows", params={"semantic": "true"}).json()
    assert rows["matched"] == 0
    missing = api.client.get(f"/api/v1/versions/{split}/clusters/{'0' * 64}")
    assert missing.status_code == 404
