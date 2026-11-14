"""DATA-1.1 to DATA-1.3: ingestion into stage 0."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from stratal.core.errors import SourceNotFound
from stratal.data.ingest import IngestConfig, IngestResult, run_ingest
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import Row, iter_rows
from stratal.testing.fakes import ListSink, fake_version_identity
from stratal.testing.synthetic import make_image_folder


def _ingest(
    source: Path, data_dir: Path, **config: int
) -> tuple[IngestResult, list[Row], ListSink]:
    sink = ListSink()
    result = run_ingest(
        source=source,
        data_dir=data_dir,
        config=IngestConfig(**config),
        derive=fake_version_identity,
        sink=sink,
    )
    rows = list(iter_rows(data_dir / result.record.manifest_relpath))
    return result, rows, sink


def test_DATA_1_1_ten_thousand_files_give_ten_thousand_rows(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=10_000, size=32)

    result, rows, _ = _ingest(folder.root, tmp_path / "data", min_size=16)

    assert folder.total_files == 10_000
    assert result.record.row_count == 10_000
    assert len(rows) == 10_000
    assert len({row["row_key"] for row in rows}) == 10_000


def test_DATA_1_1_records_path_size_format_and_dimensions(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=3, jpeg_pairs=1, size=300)

    _, rows, _ = _ingest(folder.root, tmp_path / "data")

    by_key = {row["row_key"]: row for row in rows}
    assert set(by_key) == {*folder.valid, *folder.jpeg_pairs[0]}
    png = by_key[folder.valid[0]]
    assert png["format"] == "PNG"
    assert (png["width"], png["height"]) == (300, 300)
    assert png["size_bytes"] == (folder.root / folder.valid[0]).stat().st_size
    assert len(png["sha256"]) == 64
    assert png["status"] == "accepted"
    assert by_key[folder.jpeg_pairs[0][0]]["format"] == "JPEG"


def test_DATA_1_1_rows_are_sorted_by_path_and_cover_subfolders(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=4, undersized=2, not_images=1)

    _, rows, _ = _ingest(folder.root, tmp_path / "data")

    keys = [row["row_key"] for row in rows]
    assert keys == sorted(keys)
    assert len(keys) == folder.total_files == 7


def test_DATA_1_2_five_corrupt_files_give_five_rejected_rows_with_reasons(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=10, corrupt=5)

    result, rows, _ = _ingest(folder.root, tmp_path / "data")

    rejected = [row for row in rows if row["status"] == "rejected"]
    assert sorted(row["row_key"] for row in rejected) == sorted(folder.corrupt)
    assert len(rejected) == 5
    for row in rejected:
        assert row["reason_code"] == "corrupt_image"
        assert row["reason_detail"]
        assert row["decided_at_stage"] == "ingest"
    assert result.record.counts["rejected"] == 5
    assert result.record.counts["accepted"] == 10
    assert result.record.counts["by_reason"] == {"corrupt_image": 5}


def test_DATA_1_2_non_image_files_are_rejected_not_skipped(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=2, not_images=3)

    _, rows, _ = _ingest(folder.root, tmp_path / "data")

    others = [row for row in rows if row["row_key"] in folder.not_images]
    assert len(others) == 3
    assert {row["reason_code"] for row in others} == {"unsupported_format"}


def test_DATA_1_2_missing_source_folder_is_a_typed_error(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFound) as caught:
        _ingest(tmp_path / "nowhere", tmp_path / "data")

    assert caught.value.code == "source_not_found"
    assert caught.value.suggested_action


def test_DATA_1_3_default_minimum_is_256_and_undersized_images_are_flagged(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=4, undersized=3)

    _, rows, _ = _ingest(folder.root, tmp_path / "data")

    assert IngestConfig().min_size == 256
    flagged = [row for row in rows if row["reason_code"] == "below_min_size"]
    assert sorted(row["row_key"] for row in flagged) == sorted(folder.undersized)
    assert all(row["status"] == "rejected" and row["width"] == 64 for row in flagged)


def test_DATA_1_3_threshold_can_be_changed_by_config(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=4, undersized=3)

    _, lenient, _ = _ingest(folder.root, tmp_path / "data", min_size=64)
    _, strict, _ = _ingest(folder.root, tmp_path / "data", min_size=512)

    assert all(row["status"] == "accepted" for row in lenient)
    assert all(row["reason_code"] == "below_min_size" for row in strict)


def test_DATA_1_3_out_of_range_threshold_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IngestConfig(min_size=0)


def test_DATA_1_1_ingest_reports_what_it_is_doing(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=6, corrupt=1)

    _, _, sink = _ingest(folder.root, tmp_path / "data")

    messages = sink.messages()
    assert "Scanning folder… 7 files found" in messages
    assert "Reading metadata 7/7" in messages
    progress = next(e for e in sink.events if e["message"] == "Reading metadata 7/7")
    assert progress["metrics"]["accepted"] == 6
    assert progress["metrics"]["rejected"] == 1
    assert any(message.startswith("Recording lineage → ds_") for message in messages)


def test_DATA_1_1_identical_files_are_stored_once(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=5, exact_copies=3)

    _, rows, _ = _ingest(folder.root, tmp_path / "data")

    assert len(rows) == 8
    assert len({row["sha256"] for row in rows}) == 5
    store = BlobStore(tmp_path / "data")
    assert all(store.exists(row["sha256"]) for row in rows)
