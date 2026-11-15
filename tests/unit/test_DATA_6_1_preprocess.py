"""DATA-6.1: a preprocessed copy of every accepted image, originals kept, transform recorded.

DATA-6.1.AC1: every accepted row points at a preprocessed file that exists and
respects the maximum; originals are still present; re-running with the same
config adds no new files; changing the maximum produces a new version ID.
"""

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from stratal.data.clean import DedupeUnits
from stratal.data.commit import VersionRecord
from stratal.data.preprocess import PreprocessConfig, target_size
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import MemoryDerivationCache
from stratal.testing import stagekit
from stratal.testing.fakes import (
    ListSink,
    fake_perceptual_hash,
    no_exact_duplicates,
    no_near_duplicates,
)
from stratal.testing.synthetic import ImageFolder, make_image_folder

FAKES = DedupeUnits(no_exact_duplicates, fake_perceptual_hash, no_near_duplicates)
SMALL = PreprocessConfig(max_side=128)


def _cleaned(tmp_path: Path, **planted: int) -> tuple[ImageFolder, Path, VersionRecord]:
    folder = make_image_folder(tmp_path / "src", **planted)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    return folder, data, stagekit.clean(data, raw, FAKES).record


def _open(data: Path, sha256: str) -> Image.Image:
    image = Image.open(BlobStore(data).path_for(sha256))
    image.load()
    return image


def test_DATA_6_1_default_maximum_side_is_768_px() -> None:
    assert PreprocessConfig().max_side == 768, "OC-3, accepted 2026-11-15"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((256, 256), (128, 128)),
        ((256, 170), (128, 85)),
        ((170, 256), (85, 128)),
        ((64, 64), (64, 64)),
        ((128, 40), (128, 40)),
        ((4000, 1), (128, 1)),
    ],
)
def test_DATA_6_1_target_size_fits_the_maximum_keeps_aspect_and_never_enlarges(
    size: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert target_size(*size, max_side=128) == expected


def test_DATA_6_1_every_accepted_row_points_at_a_copy_that_respects_the_maximum(
    tmp_path: Path,
) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=8, corrupt=2, undersized=2)

    result = stagekit.preprocess(data, cleaned, config=SMALL)
    rows = stagekit.rows_of(data, result.record)

    accepted = [r for r in rows.values() if r["status"] == "accepted"]
    assert len(accepted) == 10, "8 valid and 2 undersized (64 px is above min_size 32)"
    for row in accepted:
        copy = _open(data, row["derived_sha256"])
        assert max(copy.size) <= 128
        assert copy.size == (row["derived_width"], row["derived_height"])
        assert copy.format == "JPEG" and copy.mode == "RGB"
    for row in rows.values():
        if row["status"] != "accepted":
            assert row["derived_sha256"] is None, "only accepted rows get a copy"


def test_DATA_6_1_originals_are_still_present_and_unchanged(tmp_path: Path) -> None:
    folder, data, cleaned = _cleaned(tmp_path, valid=5)

    result = stagekit.preprocess(data, cleaned, config=SMALL)

    store = BlobStore(data)
    for key, row in stagekit.rows_of(data, result.record).items():
        original = store.path_for(row["sha256"]).read_bytes()
        assert original == (folder.root / key).read_bytes()
        assert hashlib.sha256(original).hexdigest() == row["sha256"]
        assert row["derived_sha256"] != row["sha256"]


def test_DATA_6_1_rerun_with_the_same_config_adds_no_files(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=6, odd_modes=3)
    store = BlobStore(data)

    first = stagekit.preprocess(data, cleaned, config=SMALL)
    bytes_after_first = store.total_bytes()
    second = stagekit.preprocess(data, cleaned, config=SMALL, cache=MemoryDerivationCache())

    assert first.new_blob_bytes > 0
    assert second.new_blob_bytes == 0, "same bytes, recomputed with an empty cache"
    assert store.total_bytes() == bytes_after_first
    assert second.record.version_id == first.record.version_id
    assert second.record.reused_manifest


def test_DATA_6_1_a_new_maximum_gives_a_new_version_id(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=4)

    at_128 = stagekit.preprocess(data, cleaned, config=SMALL)
    at_96 = stagekit.preprocess(data, cleaned, config=PreprocessConfig(max_side=96))

    assert at_96.record.version_id != at_128.record.version_id
    assert at_96.record.config["max_side"] == 96


def test_DATA_6_1_the_transform_is_recorded_in_the_version_config(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=2)

    record = stagekit.preprocess(data, cleaned).record

    assert record.config == {
        "max_side": 768,
        "jpeg_quality": 95,
        "resample": "lanczos",
        "exif_transpose": True,
        "mode": "RGB",
        "output_format": "JPEG",
    }
    assert record.stage == "preprocess" and record.parent_version_id == cleaned.version_id


def test_DATA_6_1_exif_orientation_is_applied(tmp_path: Path) -> None:
    folder, data, cleaned = _cleaned(tmp_path, valid=1, exif_rotated=2)

    rows = stagekit.rows_of(data, stagekit.preprocess(data, cleaned, config=SMALL).record)

    for key in folder.exif_rotated:
        row = rows[key]
        assert row["width"] > row["height"], "stored landscape"
        assert row["derived_height"] > row["derived_width"], "the copy is upright: portrait"
        assert (row["derived_width"], row["derived_height"]) == (85, 128)


def test_DATA_6_1_other_modes_become_rgb_and_transparency_is_flattened_onto_white(
    tmp_path: Path,
) -> None:
    folder, data, cleaned = _cleaned(tmp_path, valid=1, odd_modes=3)

    rows = stagekit.rows_of(data, stagekit.preprocess(data, cleaned, config=SMALL).record)

    for key in folder.odd_modes:
        assert _open(data, rows[key]["derived_sha256"]).mode == "RGB"
    # The first odd-mode file is RGBA at alpha 128. Over white, each channel becomes
    # c·128/255 + 255·127/255, at least 127 whatever the colour c; over black it could be 0.
    translucent = _open(data, rows[folder.odd_modes[0]]["derived_sha256"])
    assert all(mean >= 125 for mean in ImageStat.Stat(translucent).mean)


def test_DATA_6_1_cached_copies_are_reused_and_a_lost_copy_is_made_again(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=4)
    cache = MemoryDerivationCache()

    first = stagekit.preprocess(data, cleaned, config=SMALL, cache=cache)
    rows = stagekit.rows_of(data, first.record)
    lost = BlobStore(data).path_for(next(iter(rows.values()))["derived_sha256"])
    lost.chmod(0o644)
    lost.unlink()
    second = stagekit.preprocess(data, cleaned, config=SMALL, cache=cache)

    assert first.metrics["computed"] == 4
    assert (second.metrics["computed"], second.metrics["cache_hits"]) == (1, 3)
    assert lost.is_file(), "INV-04: the copy is back"
    assert second.record.version_id == first.record.version_id


def test_INV_06_preprocess_keeps_every_row(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=3, corrupt=1, not_images=1)

    result = stagekit.preprocess(data, cleaned, config=SMALL)

    assert result.record.row_count == cleaned.row_count == 5


def test_MON_3_1_preprocessing_emits_its_messages_and_metrics(tmp_path: Path) -> None:
    _, data, cleaned = _cleaned(tmp_path, valid=70)
    sink = ListSink()

    result = stagekit.preprocess(data, cleaned, sink=sink, config=SMALL)

    resize = [e for e in sink.events if e["substage"] == "resize"]
    assert resize[0]["message"] == "Resizing to 128px · normalizing · RGB · JPEG q95"
    assert [e["message"] for e in resize[1:]] == [
        "Resizing to 128px · normalizing 64/70",
        "Resizing to 128px · normalizing 70/70",
    ]
    assert resize[-1]["step"] == resize[-1]["total"] == 70
    order = [e["substage"] for e in sink.events]
    assert order.index("resize") < order.index("staging"), "work is reported before the commit"
    metrics = result.metrics
    assert metrics["images"] == 70 and metrics["resized"] == 70
    assert metrics["derived_max_side"] == 128
    assert metrics["derived_mean_width"] == metrics["derived_mean_height"] == 128.0
    assert "images_per_s" in metrics
