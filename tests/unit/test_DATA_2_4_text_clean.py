"""DATA-2.4: text cleaning (normalise, drop empty and over-length rows, log them), and
exact duplicates of records and texts (DATA-2.1 for ADR-0019's record types)."""

import json
from pathlib import Path

from stratal.data.dedupe import group_exact_duplicates
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.record_clean import CleanTableConfig, CleanTextConfig, run_clean_records
from stratal.data.records import META_TABLE_SCHEMA, normalise_text
from stratal.data.tables import TableIngest
from stratal.data.texts import TextIngest
from stratal.storage.manifest import Row, iter_rows, read_metadata
from stratal.testing.fakes import ListSink, fake_version_identity


def test_DATA_2_4_normalising_folds_unicode_and_whitespace() -> None:
    assert normalise_text("  Ｃafe  au\tlait \n") == "Cafe au lait"
    assert normalise_text("① two") == "1 two", "NFKC folds compatibility characters"
    assert normalise_text(" \t\n ") == ""


def _ingested(tmp_path: Path, config: IngestConfig, files: dict[str, str]) -> Path:
    source = tmp_path / "src"
    for name, body in files.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text(body, encoding="utf-8")
    result = run_ingest(
        source=source,
        data_dir=tmp_path / "data",
        config=config,
        derive=fake_version_identity,
        sink=ListSink(),
    )
    return tmp_path / "data" / result.record.manifest_relpath


def _clean(
    tmp_path: Path, manifest: Path, stage: str, config: CleanTableConfig | CleanTextConfig
) -> tuple[list[Row], ListSink, dict[str, object], Path]:
    sink = ListSink()
    result = run_clean_records(
        stage=stage,
        parent_manifest=manifest,
        parent_version_id="ds_parent",
        data_dir=tmp_path / "data",
        config=config,
        derive=fake_version_identity,
        sink=sink,
        exact=group_exact_duplicates,
    )
    out = tmp_path / "data" / result.record.manifest_relpath
    return list(iter_rows(out)), sink, dict(result.metrics), out


def test_DATA_2_4_planted_bad_rows_are_removed_and_logged(tmp_path: Path) -> None:
    long_text = "word " * 30
    lines = [
        {"text": "  A  dog runs. ", "label": "dog"},
        {"text": "", "label": "dog"},
        {"text": " \t ", "label": "cat"},
        {"text": long_text, "label": "cat"},
        {"text": "A dog runs.", "label": "dog"},
        {"text": "A cat.", "label": "cat"},
    ]
    manifest = _ingested(
        tmp_path,
        IngestConfig(text=TextIngest()),
        {"t.jsonl": "\n".join(json.dumps(line) for line in lines)},
    )

    rows, sink, metrics, _ = _clean(tmp_path, manifest, "clean_text", CleanTextConfig(max_chars=40))

    by_key = {r["row_key"][-1]: r for r in rows}
    assert by_key["1"]["text"] == "A dog runs." and by_key["1"]["status"] == "accepted"
    assert (by_key["2"]["status"], by_key["2"]["reason_code"]) == ("rejected", "empty_text")
    assert by_key["3"]["reason_code"] == "empty_text"
    assert by_key["4"]["reason_code"] == "text_too_long"
    assert (by_key["5"]["status"], by_key["5"]["dup_of"]) == ("duplicate", by_key["1"]["row_key"])
    assert by_key["6"]["status"] == "accepted"
    assert all(r["decided_at_stage"] == "clean_text" for r in rows if r["status"] != "accepted")
    assert metrics["rejected_empty_text"] == 2 and metrics["rejected_text_too_long"] == 1
    assert metrics["duplicates_exact"] == 1
    warned = [e["message"] for e in sink.events if e["level"] == "WARN"]
    assert any("2 rows as empty_text" in m and by_key["2"]["row_key"] in m for m in warned)
    assert any("1 rows as text_too_long" in m for m in warned)


def test_DATA_2_4_the_content_hash_follows_the_normalised_text(tmp_path: Path) -> None:
    manifest = _ingested(
        tmp_path, IngestConfig(text=TextIngest()), {"t.jsonl": '{"text": " a  b ", "label": "x"}'}
    )

    rows, _, _, _ = _clean(tmp_path, manifest, "clean_text", CleanTextConfig())

    raw = next(iter_rows(manifest))
    assert rows[0]["sha256"] != raw["sha256"]
    assert rows[0]["text"] == "a b"


def test_DATA_2_1_duplicate_records_are_marked_and_the_schema_passed_on(tmp_path: Path) -> None:
    manifest = _ingested(
        tmp_path,
        IngestConfig(table=TableIngest(target="y")),
        {"t.csv": "a,b,y\n1,x,p\n2,y,q\n1,x,r\n"},
    )

    rows, _, metrics, out = _clean(tmp_path, manifest, "clean_table", CleanTableConfig())

    assert [r["status"] for r in rows] == ["accepted", "accepted", "duplicate"]
    assert rows[2]["dup_of"] == rows[0]["row_key"], "same features: leakage across a split"
    assert metrics["duplicates_exact"] == 1
    assert read_metadata(out)[META_TABLE_SCHEMA] == read_metadata(manifest)[META_TABLE_SCHEMA]
