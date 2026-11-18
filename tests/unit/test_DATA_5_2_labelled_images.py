"""DATA-5.2, labelled-images part (delivered early in M8, ADR-0014 rule 3).

Ingest with ``label_from: folder`` records each image's class from its first
folder; the labelled-images validator checks the rows against the type's schema.
"""

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from stratal.data.dataset_types import DATASET_TYPES, validate_labelled_images
from stratal.data.ingest import IngestConfig, folder_label, run_ingest
from stratal.storage.manifest import Row, iter_rows
from stratal.testing.fakes import ListSink, fake_version_identity


def _class_folder(root: Path) -> Path:
    """Two classes, two 32-px images each, and one image at the root."""
    for index, (label, colour) in enumerate(
        [("cat", "red"), ("cat", "orange"), ("dog", "blue"), ("dog", "navy")]
    ):
        path = root / label / f"train-{index:05d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), colour).save(path)
    Image.new("RGB", (32, 32), "green").save(root / "stray.png")
    return root


def _ingest(source: Path, data_dir: Path, **config: Any) -> tuple[Any, dict[str, Row]]:
    result = run_ingest(
        source=source,
        data_dir=data_dir,
        config=IngestConfig(**config),
        derive=fake_version_identity,
        sink=ListSink(),
    )
    rows = {r["row_key"]: r for r in iter_rows(data_dir / result.record.manifest_relpath)}
    return result, rows


@pytest.mark.parametrize(
    ("relpath", "label"),
    [
        ("airplane/train-00001.png", "airplane"),
        ("dog/sub/x.png", "dog"),
        ("stray.png", None),
        ("/x.png", None),
    ],
)
def test_DATA_5_2_the_label_is_the_first_folder(relpath: str, label: str | None) -> None:
    assert folder_label(relpath) == label


def test_DATA_5_2_ingest_records_the_class_folder_as_label(tmp_path: Path) -> None:
    source = _class_folder(tmp_path / "src")

    result, rows = _ingest(source, tmp_path / "data", min_size=32, label_from="folder")

    assert result.record.dtype == "labelled_images"
    assert result.record.config == {"min_size": 32, "label_from": "folder"}
    assert {k: r["label"] for k, r in rows.items() if r["status"] == "accepted"} == {
        "cat/train-00000.png": "cat",
        "cat/train-00001.png": "cat",
        "dog/train-00002.png": "dog",
        "dog/train-00003.png": "dog",
    }


def test_DATA_5_2_a_file_outside_any_class_folder_is_rejected_not_dropped(
    tmp_path: Path,
) -> None:
    source = _class_folder(tmp_path / "src")

    result, rows = _ingest(source, tmp_path / "data", min_size=32, label_from="folder")

    assert result.record.row_count == 5
    stray = rows["stray.png"]
    assert (stray["status"], stray["reason_code"], stray["decided_at_stage"]) == (
        "rejected",
        "no_label",
        "ingest",
    )
    assert stray["sha256"], "the file is still stored and hashed"


def test_DATA_5_2_without_label_from_ingest_is_unchanged(tmp_path: Path) -> None:
    """Versions made before labels existed keep their config, and so their ID (DATA-3.2)."""
    source = _class_folder(tmp_path / "src")

    result, rows = _ingest(source, tmp_path / "data", min_size=32)

    assert IngestConfig().resolved() == {"min_size": 256}
    assert result.record.config == {"min_size": 32}
    assert result.record.dtype is None
    assert all(r["label"] is None for r in rows.values())
    assert rows["stray.png"]["status"] == "accepted"


def _rows() -> list[Row]:
    return [
        {"row_key": "a.png", "status": "accepted", "sha256": "s_a", "label": "cat"},
        {"row_key": "b.png", "status": "accepted", "sha256": "s_b", "label": "  "},
        {"row_key": "c.png", "status": "accepted", "sha256": "s_c"},
        {"row_key": "d.png", "status": "accepted", "sha256": "gone", "label": "dog"},
        {"row_key": "e.png", "status": "accepted", "sha256": "s_e", "derived_sha256": "gone"},
        {"row_key": "f.png", "status": "rejected", "sha256": "gone"},
    ]


def _exists(sha256: str) -> bool:
    return sha256 != "gone"


def test_DATA_5_2_rows_missing_a_label_or_file_are_reported() -> None:
    report = validate_labelled_images(_rows(), _exists)

    assert report.dtype == "labelled_images"
    assert (report.checked, report.valid) == (5, 1)
    assert {(p.row_key, p.reason_code) for p in report.problems} == {
        ("b.png", "missing_label"),
        ("c.png", "missing_label"),
        ("d.png", "missing_file"),
        ("e.png", "missing_file"),
    }


def test_DATA_5_2_type_is_registered_by_name() -> None:
    assert DATASET_TYPES["labelled_images"] is validate_labelled_images
