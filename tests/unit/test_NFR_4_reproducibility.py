"""NFR-4: same data, config and seed give the same splits and version IDs.

Two runs of the whole phase-1 chain, ingest to split, each in its own data
folder with its own cache, compared automatically (NFR-4.AC1). The dedupe,
caption-loop, scoring and filter units are the real ones; the models are
fakes (no GPU in the unit suite). The split runs once with fakes, which tests
the stage's own determinism now, and once with the owner's units, a strict
expected failure until DATA-4 is written.
"""

from pathlib import Path
from typing import Any

import pytest

from stratal.data.clean import OWNER_UNITS as DEDUPE_UNITS
from stratal.data.split_stage import OWNER_UNITS as SPLIT_UNITS
from stratal.data.split_stage import SplitConfig, SplitUnits
from stratal.label.filtering import filter_decisions
from stratal.label.stage import LabelModels, LabelUnits
from stratal.testing import stagekit
from stratal.testing.fake_models import (
    ConstantSimilarity,
    FakeCaptioner,
    FakeClipEncoder,
    FakeGpu,
    PassthroughVram,
    caption_all_at_once,
)
from stratal.testing.fake_split import FAKE_UNITS as FAKE_SPLIT
from stratal.testing.synthetic import make_image_folder

MODELS = LabelModels(
    gpu=FakeGpu,
    load_captioner=lambda _config: FakeCaptioner(),
    load_clip=lambda _config: FakeClipEncoder(),
)
LABEL_UNITS = LabelUnits(
    caption=caption_all_at_once,
    similarity=ConstantSimilarity(0.3),
    vram=lambda gpu, sink: PassthroughVram(gpu, sink),
)


def _run(source: Path, data: Path, split_units: SplitUnits, seed: int) -> dict[str, Any]:
    """The chain on ``source`` in ``data``: each stage's version ID, and the split columns."""
    raw = stagekit.ingest(source, data)
    cleaned = stagekit.clean(data, raw, DEDUPE_UNITS).record
    preprocessed = stagekit.preprocess(data, cleaned).record
    labelled = stagekit.label(data, preprocessed, models=MODELS, units=LABEL_UNITS).record
    filtered = stagekit.filter_stage(data, labelled, rule=filter_decisions).record
    split = stagekit.split(data, filtered, split_units, config=SplitConfig(seed=seed))
    rows = stagekit.rows_of(data, split.record)
    return {
        "ids": [r.version_id for r in (raw, cleaned, preprocessed, labelled, filtered)]
        + [split.record.version_id],
        "splits": {k: (r["split"], r["split_group"], r["split_stratum"]) for k, r in rows.items()},
        "report": split.report,
    }


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = make_image_folder(
        tmp_path_factory.mktemp("nfr4") / "src", valid=40, corrupt=2, exact_copies=3, jpeg_pairs=3
    )
    return folder.root


def test_NFR_4_two_runs_give_the_same_version_ids_and_splits(source: Path, tmp_path: Path) -> None:
    first = _run(source, tmp_path / "run1", FAKE_SPLIT, seed=11)
    second = _run(source, tmp_path / "run2", FAKE_SPLIT, seed=11)

    assert second["ids"] == first["ids"]
    assert second["splits"] == first["splits"]
    assert second["report"] == first["report"]
    assert sum(1 for s, _, _ in first["splits"].values() if s) == first["report"]["accepted"] > 0


def test_NFR_4_a_different_seed_changes_only_the_split(source: Path, tmp_path: Path) -> None:
    first = _run(source, tmp_path / "run1", FAKE_SPLIT, seed=11)
    other = _run(source, tmp_path / "run2", FAKE_SPLIT, seed=12)

    assert other["ids"][:-1] == first["ids"][:-1]
    assert other["ids"][-1] != first["ids"][-1]


@pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="owner units DATA-4.1 to 4.5 not written yet"
)
def test_NFR_4_two_runs_with_the_owner_split_units_agree(source: Path, tmp_path: Path) -> None:
    first = _run(source, tmp_path / "run1", SPLIT_UNITS, seed=11)
    second = _run(source, tmp_path / "run2", SPLIT_UNITS, seed=11)

    assert second["ids"] == first["ids"]
    assert second["splits"] == first["splits"]


def test_NFR_4_rejection_details_name_no_local_path(source: Path, tmp_path: Path) -> None:
    """E-010: decoder messages carried the blob path, so the ingest version ID
    depended on where the data folder was."""
    first = stagekit.ingest(source, tmp_path / "one")
    second = stagekit.ingest(source, tmp_path / "elsewhere" / "two")

    rows = stagekit.rows_of(tmp_path / "one", first).values()
    details = [str(r["reason_detail"]) for r in rows if r["reason_detail"]]
    assert details, "the folder has corrupt files"
    assert not any(str(tmp_path) in d or "/blobs/" in d for d in details), details
    assert second.version_id == first.version_id
