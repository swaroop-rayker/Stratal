"""NFR-2 (E-024): opening a 2-million-row table's version killed the api (E-024).

The rows and distributions endpoints read the whole manifest (348 MB on disk) into
memory and the api hit its 400 MiB limit. Both now stream it in batches. These tests
forbid whole-file reads and use tiny batches, so every page and total crosses batch
boundaries.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from stratal.data import profile
from stratal.storage import manifest
from stratal.storage.manifest import Row, iter_rows, read_all, select_rows, write_manifest


def _rows(n: int) -> Iterator[Row]:
    for i in range(n):
        rejected = i % 5 == 0
        yield {
            "row_key": f"{i:06d}",
            "sha256": "a" * 64,
            "status": "rejected" if rejected else "accepted",
            "decided_at_stage": "clean" if rejected else None,
            "reason_code": "missing_value" if rejected else None,
            "split": None if rejected else ("train", "val", "test")[i % 3],
            "target_value": float(i % 17),
            "record": f'{{"a": {i}}}',
        }


@pytest.fixture
def table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "m.parquet"
    write_manifest(path, _rows(1000), metadata={}, batch_size=128)
    monkeypatch.setattr(manifest, "_READ_BATCH", 7)
    monkeypatch.setattr(manifest, "_PAGE_BATCH", 7)
    monkeypatch.setattr("pyarrow.parquet.read_table", _whole)
    return path


def _whole(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the manifest was read whole")


def test_NFR_2_rows_are_paged_without_reading_the_whole_manifest(table: Path) -> None:
    first = select_rows(table, equals={"status": "rejected"}, limit=50)
    after = select_rows(
        table, equals={"status": "rejected"}, after_key=first.rows[-1]["row_key"], limit=50
    )

    assert first.matched == 200 and after.matched == 200
    assert [r["row_key"] for r in first.rows] == [f"{i:06d}" for i in range(0, 250, 5)]
    assert after.rows[0]["row_key"] == "000250" and len(after.rows) == 50
    assert first.rows[0]["record"] == '{"a": 0}'


def test_NFR_2_unfiltered_and_key_filtered_pages(table: Path) -> None:
    page = select_rows(table, equals={}, after_key="000995", limit=50)
    keyed = select_rows(table, equals={}, key_contains="99", limit=5)

    assert page.matched == 1000 and page.rows[0]["row_key"] == "000996"
    assert len(page.rows) == 4
    assert keyed.matched == 19
    assert [r["row_key"] for r in keyed.rows] == ["000099", "000199", "000299", "000399", "000499"]


def test_NFR_2_distributions_stream_and_match_a_whole_read(
    table: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = [profile.Step("ds_a", "ingest", {}), profile.Step("ds_b", "split", {})]
    streamed = profile.distributions(
        iter_rows(table, columns=list(profile.DISTRIBUTION_FIELDS), batch_size=7),
        dtype="tabular",
        lineage=steps,
    )
    monkeypatch.undo()
    whole = profile.distributions(read_all(table), dtype="tabular", lineage=steps)

    assert streamed == whole
    assert streamed["row_count"] == 1000 and streamed["accepted"] == 800
    assert streamed["funnel"][-1]["parts"] == {"train": 267, "val": 267, "test": 266}
    target = streamed["columns"][0]
    assert target["key"] == "target_value" and target["raw"]["n"] == 1000
    assert sum(b["raw"] for b in target["bins"]) == 1000


def _version(path: Path, keys: range, *, changed: bool) -> Path:
    rows: list[Row] = []
    for i in keys:
        flipped = changed and i % 10 == 0
        worded = i % 10 == 1
        rows.append(
            {
                "row_key": f"{i:06d}",
                "sha256": "a" * 64,
                "status": "rejected" if flipped or worded else "accepted",
                "decided_at_stage": "clean" if flipped or worded else None,
                "reason_code": "missing_value" if flipped or worded else None,
                "reason_detail": f"{'B' if changed else 'A'}: no value" if worded else None,
                "caption": f"row {i}" if changed and i % 3 == 0 else None,
            }
        )
    write_manifest(path, rows, metadata={}, batch_size=16)
    return path


def test_NFR_2_the_diff_walks_both_versions_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _version(tmp_path / "a.parquet", range(100), changed=False)
    b = _version(tmp_path / "b.parquet", range(5, 105), changed=True)
    monkeypatch.setattr(manifest, "_PAGE_BATCH", 7)
    monkeypatch.setattr("pyarrow.parquet.read_table", _whole)

    def rows(path: Path) -> Iterator[Row]:
        return iter_rows(path, columns=list(manifest.COLUMNS), batch_size=7)

    steps = [profile.Step("ds_a", "clean", {})]
    body = profile.diff(
        rows(a),
        rows(b),
        a_steps=steps,
        b_steps=steps,
        a_filled=manifest.filled_columns(a),
        b_filled=manifest.filled_columns(b),
    )

    assert (body["added"], body["removed"]) == (5, 5)
    assert (body["changed"], body["wording_only"], body["same"]) == (9, 9, 77)
    assert body["only_in_b"] == ["caption"] and body["only_in_a"] == []
    assert body["by_field"]["status"] == 9
    assert [r["row_key"] for r in body["changed_rows"]][:2] == ["000010", "000020"]
    assert body["wording_rows"][0] == {
        "row_key": "000011",
        "a": "A: no value",
        "b": "B: no value",
    }
    assert profile.filled_in(rows(b)) == manifest.filled_columns(b)


def test_NFR_2_histogram_counts_the_top_edge_in_the_last_bin() -> None:
    assert profile.histogram([0.0, 1.0, 1.5, 2.0, 3.0], [0.0, 1.0, 2.0]) == [1, 3]
