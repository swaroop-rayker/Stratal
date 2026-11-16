"""DATA-5.1 in the pipeline: the filter stage validates the image–caption schema.

Rows still accepted after the CLIP rule that lack a caption or an image file are
marked ``rejected`` and listed in the version's validation report (AC1); the
version is then of type ``image_caption``. Rejecting a missing file, rather than
failing the stage, is the owner's choice of 2026-11-16.
"""

from pathlib import Path

import pytest

from stratal.data.commit import VersionRecord, commit_version
from stratal.data.dataset_types import validate_image_caption
from stratal.data.versioning import derive_version_identity
from stratal.label.filtering import filter_decisions
from stratal.label.stage import LabelModels, LabelUnits
from stratal.storage.blobs import BlobStore
from stratal.testing import stagekit
from stratal.testing.fake_clean import FAKE_UNITS as FAKE_DEDUPE
from stratal.testing.fake_models import (
    ConstantSimilarity,
    FakeCaptioner,
    FakeClipEncoder,
    FakeGpu,
    PassthroughVram,
    caption_all_at_once,
    filter_nothing,
)
from stratal.testing.fakes import ListSink
from stratal.testing.synthetic import make_image_folder

MODELS = LabelModels(
    gpu=FakeGpu,
    load_captioner=lambda _config: FakeCaptioner(),
    load_clip=lambda _config: FakeClipEncoder(),
)
UNITS = LabelUnits(
    caption=caption_all_at_once,
    similarity=ConstantSimilarity(0.3),
    vram=lambda gpu, sink: PassthroughVram(gpu, sink),
)


@pytest.fixture
def labelled(tmp_path: Path) -> tuple[Path, VersionRecord]:
    folder = make_image_folder(tmp_path / "src", valid=6, corrupt=1)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    cleaned = stagekit.clean(data, raw, FAKE_DEDUPE).record
    preprocessed = stagekit.preprocess(data, cleaned).record
    return data, stagekit.label(data, preprocessed, models=MODELS, units=UNITS).record


def _accepted(data: Path, record: VersionRecord) -> list[str]:
    rows = stagekit.rows_of(data, record)
    return sorted(k for k, r in rows.items() if r["status"] == "accepted")


def test_DATA_5_1_a_lost_preprocessed_copy_is_rejected_with_a_report(
    labelled: tuple[Path, VersionRecord],
) -> None:
    data, parent = labelled
    victim = _accepted(data, parent)[0]
    derived = stagekit.rows_of(data, parent)[victim]["derived_sha256"]
    path = BlobStore(data).path_for(derived)
    path.chmod(0o644)
    path.unlink()
    sink = ListSink()

    result = stagekit.filter_stage(data, parent, rule=filter_decisions, sink=sink)

    after = stagekit.rows_of(data, result.record)
    assert len(after) == len(stagekit.rows_of(data, parent)), "no row is dropped"
    assert after[victim]["status"] == "rejected"
    assert (after[victim]["reason_code"], after[victim]["decided_at_stage"]) == (
        "missing_file",
        "filter",
    )
    report = result.record.counts["validation"]
    assert report["by_reason"] == {"missing_file": 1}
    assert report["rejected_rows"] == [
        {
            "row_key": victim,
            "reason_code": "missing_file",
            "detail": "preprocessed copy is not in the store",
        }
    ]
    assert result.metrics["rejected_missing_file"] == 1
    assert result.metrics["accepted"] == 5
    warnings = [e for e in sink.events if e["level"] == "WARN"]
    assert len(warnings) == 1 and warnings[0]["reason"] and warnings[0]["suggested_action"]


def test_DATA_5_1_a_caption_the_rule_let_through_blank_is_rejected(
    labelled: tuple[Path, VersionRecord],
) -> None:
    data, parent = labelled
    rows = list(stagekit.rows_of(data, parent).values())
    blank = _accepted(data, parent)[1]
    for row in rows:
        if row["row_key"] == blank:
            row["caption"] = "  "
    edited = commit_version(
        data_dir=data,
        stage="label",
        parent_version_id=parent.parent_version_id,
        rows=rows,
        config={"edited": True},
        derive=derive_version_identity,
        sink=ListSink(),
    )

    result = stagekit.filter_stage(data, edited, rule=filter_nothing)

    after = stagekit.rows_of(data, result.record)
    assert (after[blank]["status"], after[blank]["reason_code"]) == ("rejected", "missing_caption")
    assert result.record.counts["validation"]["by_reason"] == {"missing_caption": 1}


def test_DATA_5_1_a_valid_filter_version_is_of_type_image_caption(
    labelled: tuple[Path, VersionRecord],
) -> None:
    data, parent = labelled

    result = stagekit.filter_stage(data, parent, rule=filter_decisions)

    assert result.record.dtype == "image_caption"
    report = result.record.counts["validation"]
    assert (report["checked"], report["valid"], report["rejected_rows"]) == (6, 6, [])


def test_DATA_5_1_the_validator_checks_the_preprocessed_copy_too() -> None:
    rows = [
        {
            "row_key": "a.jpg",
            "sha256": "1" * 64,
            "derived_sha256": "2" * 64,
            "status": "accepted",
            "caption": "x",
        },
        {
            "row_key": "b.jpg",
            "sha256": "1" * 64,
            "derived_sha256": None,
            "status": "accepted",
            "caption": "x",
        },
    ]

    report = validate_image_caption(rows, lambda sha: sha == "1" * 64)

    assert [(p.row_key, p.reason_code) for p in report.problems] == [("a.jpg", "missing_file")]
