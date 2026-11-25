"""DATA-1.4 and NFR-2: a table streams in batches (ADR-0019 Addendum B rule 31).

A CSV is read in two passes (kinds, then values) and a Parquet file in record
batches; the rows, kinds and version ID must be what reading the table whole with
PyArrow gave before M11b, however the batches fall.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import pytest

from stratal.data import tables
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.records import table_schema_of
from stratal.data.tables import TableIngest
from stratal.data.versioning import derive_version_identity
from stratal.storage.manifest import iter_rows
from stratal.testing.fakes import ListSink

QUIRKS = """size,code,flag,empty,note,y
1.5,7,true,,NA,a
NA,8,False,,x,b
2.5,,TRUE,,,a
3,9,false,,y,
"""


def _ingest(source: Path, data: Path) -> tuple[str, list[dict[str, object]], Path]:
    result = run_ingest(
        source=source,
        data_dir=data,
        config=IngestConfig(table=TableIngest(target="y")),
        derive=derive_version_identity,
        sink=ListSink(),
    )
    manifest = data / result.record.manifest_relpath
    return result.record.version_id, list(iter_rows(manifest)), manifest


def _source(tmp_path: Path, text: str, name: str = "t.csv") -> Path:
    source = tmp_path / "src"
    source.mkdir(parents=True)
    (source / name).write_text(text, encoding="utf-8")
    return source


def test_DATA_1_4_a_csv_keeps_pyarrows_kinds_and_values(tmp_path: Path) -> None:
    _, rows, manifest = _ingest(_source(tmp_path, QUIRKS), tmp_path / "data")
    whole = pa_csv.read_csv(tmp_path / "src" / "t.csv").to_pylist()

    schema = table_schema_of(manifest)
    assert schema is not None
    assert {c.name: c.kind for c in schema.columns} == {
        "size": "numeric",
        "code": "numeric",
        "flag": "categorical",
        "empty": "categorical",
        "note": "categorical",
        "y": "categorical",
    }
    records = [json.loads(str(r["record"])) for r in rows]
    assert [r["size"] for r in records] == [1.5, None, 2.5, 3.0]
    assert [r["code"] for r in records] == [7.0, 8.0, None, 9.0]
    assert [r["flag"] for r in records] == ["true", "false", "true", "false"]
    assert [r["empty"] for r in records] == [None] * 4
    assert [r["note"] for r in records] == ["NA", "x", None, "y"], "text keeps NA as written"
    assert [w["flag"] for w in whole] == [True, False, True, False], "as PyArrow read it whole"
    assert rows[3]["reason_code"] == "missing_target"


def test_DATA_1_4_a_csv_in_many_blocks_gives_the_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = ["a,b,y"] + [f"{i * 0.5},{'x' if i % 3 else 'z'},{i % 2}" for i in range(3000)]
    text = "\n".join(lines) + "\n"
    one, rows_one, _ = _ingest(_source(tmp_path / "one", text), tmp_path / "one" / "data")
    monkeypatch.setattr(tables, "_CSV_BLOCK_BYTES", 1024)
    many, rows_many, _ = _ingest(_source(tmp_path / "many", text), tmp_path / "many" / "data")

    assert one == many
    assert rows_one == rows_many and len(rows_many) == 3000


def test_DATA_1_4_a_parquet_file_streams_in_record_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    table = pa.table(
        {
            "a": [float(i) for i in range(1000)],
            "c": [f"k{i % 7}" for i in range(1000)],
            "y": [i % 3 for i in range(1000)],
        }
    )
    pq.write_table(table, source / "t.parquet")
    one, rows_one, _ = _ingest(source, tmp_path / "one")
    monkeypatch.setattr(tables, "BATCH_ROWS", 64)
    seen: list[int] = []
    real = pq.ParquetFile.iter_batches

    def spy(self: pq.ParquetFile, *args: Any, **kwargs: Any) -> Iterator[Any]:
        for batch in real(self, *args, **kwargs):
            if kwargs.get("batch_size") == 64:  # the source, not the manifest
                seen.append(batch.num_rows)
            yield batch

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", spy)
    many, rows_many, _ = _ingest(source, tmp_path / "many")

    assert one == many and rows_one == rows_many
    assert max(seen) == 64 and sum(seen) == 1000
