"""DATA-5.1: the image–caption pairs dataset type, with schema validation."""

from stratal.data.dataset_types import DATASET_TYPES, apply_report, validate_image_caption
from stratal.storage.manifest import Row

PRESENT = {"1" * 64, "2" * 64, "3" * 64, "4" * 64}


def _exists(sha256: str) -> bool:
    return sha256 in PRESENT


def _rows() -> list[Row]:
    return [
        {"row_key": "a.png", "sha256": "1" * 64, "status": "accepted", "caption": "a dog at night"},
        {"row_key": "b.png", "sha256": "2" * 64, "status": "accepted", "caption": None},
        {"row_key": "c.png", "sha256": "3" * 64, "status": "accepted", "caption": "   "},
        {"row_key": "d.png", "sha256": "9" * 64, "status": "accepted", "caption": "a cat"},
        {"row_key": "e.png", "sha256": None, "status": "accepted", "caption": "a bird"},
        {
            "row_key": "f.png",
            "sha256": "4" * 64,
            "status": "rejected",
            "caption": None,
            "decided_at_stage": "ingest",
            "reason_code": "corrupt_image",
        },
    ]


def test_DATA_5_1_rows_missing_a_caption_or_file_are_reported() -> None:
    report = validate_image_caption(_rows(), _exists)

    assert report.dtype == "image_caption"
    assert report.checked == 5
    assert report.valid == 1
    assert not report.is_valid
    assert {(p.row_key, p.reason_code) for p in report.problems} == {
        ("b.png", "missing_caption"),
        ("c.png", "missing_caption"),
        ("d.png", "missing_file"),
        ("e.png", "missing_file"),
    }
    assert report.by_reason() == {"missing_caption": 2, "missing_file": 2}


def test_DATA_5_1_reported_rows_are_marked_rejected_not_dropped() -> None:
    rows = _rows()
    report = validate_image_caption(rows, _exists)

    out = list(apply_report(rows, report, stage="filter"))

    assert len(out) == len(rows)
    by_key = {row["row_key"]: row for row in out}
    assert by_key["a.png"]["status"] == "accepted"
    assert by_key["b.png"]["status"] == "rejected"
    assert by_key["b.png"]["reason_code"] == "missing_caption"
    assert by_key["b.png"]["decided_at_stage"] == "filter"
    assert by_key["d.png"]["reason_code"] == "missing_file"
    assert by_key["f.png"]["reason_code"] == "corrupt_image", "earlier rejections are untouched"
    assert rows[1]["status"] == "accepted", "input rows are not mutated"


def test_DATA_5_1_a_clean_dataset_validates() -> None:
    report = validate_image_caption(_rows()[:1], _exists)

    assert report.is_valid
    assert report.checked == report.valid == 1


def test_DATA_5_1_type_is_registered_by_name() -> None:
    assert DATASET_TYPES["image_caption"] is validate_image_caption
