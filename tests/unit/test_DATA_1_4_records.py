"""DATA-1.4: tabular (CSV, Parquet) and text (JSONL, TXT) ingestion; DATA-1.5: labels from a CSV.

ADR-0019 rules 1 and 3: one manifest row per record, the table's schema with
the version, the target's kind deciding the task.
"""

import json
from pathlib import Path

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stratal.core.errors import ValidationFailed
from stratal.data.ingest import CsvLabels, IngestConfig, IngestResult, run_ingest
from stratal.data.records import LABELLED_TEXT, TABULAR, table_schema_of, table_task
from stratal.data.tables import TableIngest
from stratal.data.texts import TextIngest
from stratal.data.versioning import derive_version_identity
from stratal.storage.manifest import Row, iter_rows
from stratal.testing.fakes import ListSink, fake_version_identity
from stratal.testing.synthetic import make_image_folder

CSV = """sepal,petal,colour,species
5.1,1.4,red,setosa
4.9,,blue,setosa
6.3,4.9,red,virginica
7.0,4.7,,versicolor
5.8,5.1,green,
"""


def _ingest(
    source: Path, data_dir: Path, config: IngestConfig, *, real_ids: bool = False
) -> tuple[IngestResult, list[Row], ListSink]:
    sink = ListSink()
    result = run_ingest(
        source=source,
        data_dir=data_dir,
        config=config,
        derive=derive_version_identity if real_ids else fake_version_identity,
        sink=sink,
    )
    rows = list(iter_rows(data_dir / result.record.manifest_relpath))
    return result, rows, sink


def _table(tmp_path: Path, text: str = CSV, name: str = "flowers.csv") -> Path:
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / name).write_text(text, encoding="utf-8")
    return source


def test_DATA_1_4_a_sample_csv_ingests_one_row_per_record(tmp_path: Path) -> None:
    config = IngestConfig(table=TableIngest(target="species"))

    result, rows, _ = _ingest(_table(tmp_path), tmp_path / "data", config)

    assert result.record.dtype == TABULAR
    assert [r["row_key"] for r in rows] == [f"flowers.csv#{n:07d}" for n in range(1, 6)]
    first = rows[0]
    assert json.loads(first["record"]) == {"sepal": 5.1, "petal": 1.4, "colour": "red"}
    assert (first["label"], first["target_value"], first["format"]) == ("setosa", None, "CSV")
    assert first["status"] == "accepted" and len(first["sha256"]) == 64
    assert json.loads(rows[1]["record"])["petal"] is None, "an empty feature is kept as null"
    assert rows[1]["status"] == "accepted", "imputing it is preprocessing (ML-1.3)"
    assert (rows[4]["status"], rows[4]["reason_code"]) == ("rejected", "missing_target")


def test_DATA_1_4_the_schema_travels_with_the_version_and_sets_the_task(tmp_path: Path) -> None:
    config = IngestConfig(table=TableIngest(target="species"))

    result, _, _ = _ingest(_table(tmp_path), tmp_path / "data", config)

    schema = table_schema_of(tmp_path / "data" / result.record.manifest_relpath)
    assert schema is not None
    kinds = {c.name: (c.kind, c.role) for c in schema.columns}
    assert kinds == {
        "sepal": ("numeric", "feature"),
        "petal": ("numeric", "feature"),
        "colour": ("categorical", "feature"),
        "species": ("categorical", "target"),
    }
    assert table_task(schema) == "classification"


def test_DATA_1_4_a_numeric_target_is_regression_and_kinds_can_be_set(tmp_path: Path) -> None:
    source = _table(tmp_path, "age,code,price\n30,1,10.5\n41,2,12.0\n", "houses.csv")
    config = IngestConfig(table=TableIngest(target="price", kinds={"code": "categorical"}))

    result, rows, _ = _ingest(source, tmp_path / "data", config)

    schema = table_schema_of(tmp_path / "data" / result.record.manifest_relpath)
    assert schema is not None and table_task(schema) == "regression"
    assert rows[0]["target_value"] == 10.5 and rows[0]["label"] is None
    assert json.loads(rows[0]["record"]) == {"age": 30.0, "code": "1"}


def test_DATA_1_4_a_parquet_table_ingests_like_a_csv(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    table = pa.table({"x": [1.0, 2.0], "y": ["a", "b"]})
    pq.write_table(table, source / "t.parquet")

    _, rows, _ = _ingest(source, tmp_path / "data", IngestConfig(table=TableIngest(target="y")))

    assert [r["format"] for r in rows] == ["PARQUET", "PARQUET"]
    assert [r["label"] for r in rows] == ["a", "b"]


def test_DATA_1_4_a_bad_target_or_two_tables_are_refused(tmp_path: Path) -> None:
    source = _table(tmp_path)
    with pytest.raises(ValidationFailed, match=r"not in flowers\.csv"):
        _ingest(source, tmp_path / "d1", IngestConfig(table=TableIngest(target="nope")))
    (source / "second.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(ValidationFailed, match="exactly one"):
        _ingest(source, tmp_path / "d2", IngestConfig(table=TableIngest(target="species")))


def test_DATA_1_4_other_files_beside_the_table_are_rejected_not_skipped(tmp_path: Path) -> None:
    source = _table(tmp_path)
    (source / "README.md").write_text("about the data", encoding="utf-8")

    _, rows, _ = _ingest(
        source, tmp_path / "data", IngestConfig(table=TableIngest(target="species"))
    )

    readme = next(r for r in rows if r["row_key"] == "README.md")
    assert (readme["status"], readme["reason_code"]) == ("rejected", "unsupported_format")


def _texts(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    lines = [
        {"text": "A dog runs on the beach.", "label": "dog", "group": 7},
        {"text": "  Two  dogs\tplay.  ", "label": "dog", "group": 7},
        {"text": "A cat sleeps.", "label": "cat"},
        {"text": "No class here."},
        {"text": "   ", "label": "cat"},
    ]
    body = "\n".join(json.dumps(line) for line in lines) + "\n\nnot json\n"
    (source / "captions.jsonl").write_text(body, encoding="utf-8")
    (source / "cat").mkdir()
    (source / "cat" / "more.txt").write_text("A kitten.\nA tabby cat.\n", encoding="utf-8")
    (source / "notes.pdf").write_bytes(b"%PDF")
    return source


def test_DATA_1_4_a_sample_jsonl_and_txt_ingest_one_row_per_text(tmp_path: Path) -> None:
    result, rows, _ = _ingest(_texts(tmp_path), tmp_path / "data", IngestConfig(text=TextIngest()))

    assert result.record.dtype == LABELLED_TEXT
    by_key = {r["row_key"]: r for r in rows}
    first = by_key["captions.jsonl#0000001"]
    assert (first["text"], first["label"], first["text_group"]) == (
        "A dog runs on the beach.",
        "dog",
        "7",
    )
    assert first["format"] == "JSONL" and first["status"] == "accepted"
    assert by_key["captions.jsonl#0000003"]["text_group"] is None
    assert by_key["captions.jsonl#0000004"]["reason_code"] == "missing_label"
    assert by_key["captions.jsonl#0000005"]["status"] == "accepted", "clean drops it (DATA-2.4)"
    assert "captions.jsonl#0000006" not in by_key, "a blank line is not a record"
    assert by_key["captions.jsonl#0000007"]["reason_code"] == "unreadable"
    assert [by_key[f"cat/more.txt#000000{n}"]["label"] for n in (1, 2)] == ["cat", "cat"]
    assert by_key["notes.pdf"]["reason_code"] == "unsupported_format"
    assert [r["row_key"] for r in rows] == sorted(by_key)


def test_DATA_1_4_ingest_kinds_are_exclusive() -> None:
    with pytest.raises(ValueError, match="at most one"):
        IngestConfig(text=TextIngest(), table=TableIngest(target="y"))


def _labelled_flat(tmp_path: Path, csv_text: str) -> Path:
    folder = make_image_folder(tmp_path / "src", valid=4, size=48)
    (folder.root / "labels.csv").write_text(csv_text, encoding="utf-8")
    return folder.root


def _paths(source: Path) -> list[str]:
    return sorted(p.relative_to(source).as_posix() for p in source.rglob("*.png"))


def test_DATA_1_5_labels_come_from_the_csv_and_problems_are_reported(tmp_path: Path) -> None:
    source = _labelled_flat(tmp_path, "")
    a, b, c, d = _paths(source)
    csv_text = (
        f"path,label\n{a},cat\n./{b},dog\nmissing.png,cat\n{c},cat\n{c},cat\n{d},cat\n{d},dog\n"
    )
    (source / "labels.csv").write_text(csv_text, encoding="utf-8")
    config = IngestConfig(min_size=32, label_from=CsvLabels(csv="labels.csv"))

    result, rows, sink = _ingest(source, tmp_path / "data", config)

    by_key = {r["row_key"]: r for r in rows}
    assert "labels.csv" not in by_key, "the CSV itself is not ingested"
    assert (by_key[a]["label"], by_key[b]["label"], by_key[c]["label"]) == ("cat", "dog", "cat")
    assert (by_key[d]["status"], by_key[d]["reason_code"]) == ("rejected", "conflicting_labels")
    report = result.record.counts["labels_csv"]
    assert report["missing_files"] == {
        "count": 1,
        "examples": [{"line": 4, "path": "missing.png"}],
    }
    assert report["repeated_paths"]["count"] == 2
    assert {e["path"]: e["lines"] for e in report["repeated_paths"]["examples"]} == {
        c: [5, 6],
        d: [7, 8],
    }
    assert result.record.dtype == "labelled_images"
    assert any(e["level"] == "WARN" and "missing files" in e["message"] for e in sink.events)


def test_DATA_1_5_an_image_with_no_row_is_rejected_as_no_label(tmp_path: Path) -> None:
    source = _labelled_flat(tmp_path, "")
    a, *rest = _paths(source)
    (source / "labels.csv").write_text(f"file,class\n{a},cat\n", encoding="utf-8")
    config = IngestConfig(
        min_size=32,
        label_from=CsvLabels(csv="labels.csv", path_column="file", label_column="class"),
    )

    _, rows, _ = _ingest(source, tmp_path / "data", config)

    by_key = {r["row_key"]: r for r in rows}
    assert by_key[a]["label"] == "cat"
    assert all(by_key[k]["reason_code"] == "no_label" for k in rest)


def test_DATA_1_5_the_csv_content_is_part_of_the_version_identity(tmp_path: Path) -> None:
    source = _labelled_flat(tmp_path, "")
    a, b, *_ = _paths(source)
    config = IngestConfig(min_size=32, label_from=CsvLabels(csv="labels.csv"))

    def version(text: str, run: str) -> str:
        (source / "labels.csv").write_text(text, encoding="utf-8")
        result, _, _ = _ingest(source, tmp_path / run, config, real_ids=True)
        return result.record.version_id

    first = version(f"path,label\n{a},cat\n{b},dog\n", "d1")
    again = version(f"path,label\n{a},cat\n{b},dog\n", "d2")
    changed = version(f"path,label\n{a},cat\n{b},cat\n", "d3")
    assert first == again
    assert changed != first


def test_DATA_1_5_a_missing_csv_or_column_is_refused(tmp_path: Path) -> None:
    source = _labelled_flat(tmp_path, "path,name\nx.png,cat\n")
    with pytest.raises(ValidationFailed, match="no column 'label'"):
        _ingest(source, tmp_path / "d1", IngestConfig(label_from=CsvLabels(csv="labels.csv")))
    with pytest.raises(ValidationFailed, match="not in the source folder"):
        _ingest(source, tmp_path / "d2", IngestConfig(label_from=CsvLabels(csv="other.csv")))
