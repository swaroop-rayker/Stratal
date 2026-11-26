"""DATA-1.4.AC2: a table saved as delimited text is detected and converted to Parquet.

The UCI household power file is the case that needed it: ``;``-separated, a
header row, ``?`` for a missing value, saved as ``.txt``. A converted table must
ingest with the same rows and values as the same table saved as CSV.
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from stratal.core.errors import ValidationFailed
from stratal.data.delimited import Dialect, sniff
from stratal.data.ingest import IngestConfig, TextIngest, run_ingest
from stratal.data.records import table_schema_of
from stratal.data.tables import TableIngest, find_table, read_table
from stratal.data.versioning import derive_version_identity
from stratal.storage.manifest import iter_rows
from stratal.testing.fakes import ListSink

POWER = """Date;Time;Global_active_power;Voltage;Sub_metering_3
16/12/2006;17:24:00;4.216;234.840;17.000
16/12/2006;17:25:00;5.360;233.630;16.000
21/12/2006;11:23:00;?;?;?
21/12/2006;11:24:00;0.330;240.430;0.000
"""


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _ingest(source: Path, data: Path, target: str) -> tuple[str, list[dict[str, object]], ListSink]:
    sink = ListSink()
    result = run_ingest(
        source=source,
        data_dir=data,
        config=IngestConfig(table=TableIngest(target=target)),
        derive=derive_version_identity,
        sink=sink,
    )
    rows = list(iter_rows(data / result.record.manifest_relpath))
    return result.record.version_id, rows, sink


# -- detection ---------------------------------------------------------------------------


def test_DATA_1_4_AC2_semicolon_table_with_header(tmp_path: Path) -> None:
    dialect = sniff(_write(tmp_path, "power.txt", POWER))
    assert dialect == Dialect(
        ";", True, ("Date", "Time", "Global_active_power", "Voltage", "Sub_metering_3")
    )


@pytest.mark.parametrize("sep", ["\t", ",", "|"])
def test_DATA_1_4_AC2_other_separators(tmp_path: Path, sep: str) -> None:
    text = f"a{sep}b{sep}y\n1{sep}2{sep}x\n3{sep}4{sep}z\n"
    dialect = sniff(_write(tmp_path, "t.dat", text))
    assert dialect is not None
    assert (dialect.delimiter, dialect.header, dialect.names) == (sep, True, ("a", "b", "y"))


def test_DATA_1_4_AC2_no_header_names_the_columns(tmp_path: Path) -> None:
    dialect = sniff(
        _write(tmp_path, "t.data", "5.1,3.5,setosa\n4.9,3.0,setosa\n6.2,2.9,virginica\n")
    )
    assert dialect == Dialect(",", False, ("column_1", "column_2", "column_3"))


def test_DATA_1_4_AC2_quoted_separator_is_one_field(tmp_path: Path) -> None:
    dialect = sniff(_write(tmp_path, "t.txt", 'name;n\n"a;b";1\nc;2\n'))
    assert dialect is not None and dialect.names == ("name", "n")


@pytest.mark.parametrize(
    "text",
    [
        "The cat sat on the mat.\nIt was a sunny day.\nThen it rained.\n",
        "one line only;with;fields\n",
        "a;b;c\n1;2\n3;4;5\n",
        "",
    ],
    ids=["prose", "one-line", "ragged", "empty"],
)
def test_DATA_1_4_AC2_text_is_not_a_table(tmp_path: Path, text: str) -> None:
    assert sniff(_write(tmp_path, "t.txt", text)) is None


def test_DATA_1_4_AC2_binary_is_not_a_table(tmp_path: Path) -> None:
    path = tmp_path / "t.dat"
    path.write_bytes(b"\xff\xfe\x00\x01;\x80\x81\n" * 10)
    assert sniff(path) is None


# -- finding the table ------------------------------------------------------------------


def test_DATA_1_4_AC2_a_csv_wins_over_delimited_text(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", POWER)
    _write(tmp_path, "t.csv", "a,y\n1,x\n")
    assert find_table(tmp_path, ["notes.txt", "t.csv"]) == "t.csv"


def test_DATA_1_4_AC2_the_one_delimited_table_is_found(tmp_path: Path) -> None:
    _write(tmp_path, "power.txt", POWER)
    _write(tmp_path, "README.txt", "Household power, from UCI.\nSee the licence.\n")
    assert find_table(tmp_path, ["README.txt", "power.txt"]) == "power.txt"


def test_DATA_1_4_AC2_no_table_says_how_text_is_read(tmp_path: Path) -> None:
    _write(tmp_path, "README.txt", "Just words.\nMore words.\n")
    with pytest.raises(ValidationFailed, match="delimited text") as caught:
        find_table(tmp_path, ["README.txt"])
    assert "same number of fields" in (caught.value.suggested_action or "")


# -- conversion -------------------------------------------------------------------------


def test_DATA_1_4_AC2_same_rows_and_values_as_the_csv(tmp_path: Path) -> None:
    as_text = _write(tmp_path / "txt", "power.txt", POWER)
    # The same table as a CSV, with ? written as an empty value.
    _write(tmp_path / "csv", "power.csv", POWER.replace(";", ",").replace("?", ""))
    before = as_text.read_bytes()

    _, text_rows, sink = _ingest(tmp_path / "txt", tmp_path / "data-t", "Sub_metering_3")
    _, csv_rows, _ = _ingest(tmp_path / "csv", tmp_path / "data-c", "Sub_metering_3")

    def comparable(rows: list[dict[str, object]]) -> list[tuple[object, ...]]:
        return [
            (
                str(r["row_key"]).split("#")[1],
                r["sha256"],
                r["record"],
                r["status"],
                r.get("reason_code"),
                r.get("target_value"),
            )
            for r in rows
        ]

    assert comparable(text_rows) == comparable(csv_rows)
    assert all(r["format"] == "TXT" for r in text_rows)
    assert str(text_rows[0]["row_key"]).startswith("power.txt#")
    record = json.loads(str(text_rows[2]["record"]))
    assert record["Global_active_power"] is None and record["Voltage"] is None
    assert text_rows[2]["status"] == "rejected"  # its target is missing
    assert as_text.read_bytes() == before
    assert [e for e in sink.events if e.get("substage") == "convert"]


def test_DATA_1_4_AC2_kinds_and_schema(tmp_path: Path) -> None:
    _write(tmp_path / "src", "power.txt", POWER)
    data = tmp_path / "data"
    read = read_table(tmp_path / "src", "power.txt", data)
    assert read.dialect is not None and read.row_format == "TXT"
    assert read.inferred == {
        "Date": "categorical",
        "Time": "categorical",
        "Global_active_power": "numeric",
        "Voltage": "numeric",
        "Sub_metering_3": "numeric",
    }
    assert read.count == 4
    assert pq.ParquetFile(read.path).metadata.num_rows == 4
    assert read.path.parent == data / "converted"


def test_DATA_1_4_AC2_converted_once_and_same_version_id(tmp_path: Path) -> None:
    _write(tmp_path / "src", "power.txt", POWER)
    data = tmp_path / "data"
    first, _, _ = _ingest(tmp_path / "src", data, "Voltage")
    converted = list((data / "converted").iterdir())
    stamp = converted[0].stat().st_mtime_ns
    second, _, _ = _ingest(tmp_path / "src", data, "Voltage")
    assert first == second
    assert list((data / "converted").iterdir()) == converted
    assert converted[0].stat().st_mtime_ns == stamp
    assert not list((data / "tmp").glob("converted-*"))


def test_DATA_1_4_AC2_question_mark_in_a_text_column_is_missing(tmp_path: Path) -> None:
    _write(tmp_path / "src", "t.tsv", "colour\tn\tsize\nred\t1\t2\n?\t2\t3\nblue\t3\t?\n")
    version_id, rows, _ = _ingest(tmp_path / "src", tmp_path / "data", "n")
    records = [json.loads(str(r["record"])) for r in rows]
    assert [r["colour"] for r in records] == ["red", None, "blue"]
    assert [r["size"] for r in records] == [2.0, 3.0, None]
    schema = table_schema_of(tmp_path / "data" / "manifests" / f"{version_id}.parquet")
    assert schema is not None
    assert {c.name: c.kind for c in schema.columns} == {
        "colour": "categorical",
        "n": "numeric",
        "size": "numeric",
    }


def test_DATA_1_4_AC2_text_sources_still_read_txt_as_text(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "pos", "a.txt", "good;fine\nnice;ok\n")
    _write(tmp_path / "src" / "neg", "b.txt", "bad;poor\nawful;meh\n")
    result = run_ingest(
        source=tmp_path / "src",
        data_dir=tmp_path / "data",
        config=IngestConfig(text=TextIngest()),
        derive=derive_version_identity,
        sink=ListSink(),
    )
    rows = list(iter_rows(tmp_path / "data" / result.record.manifest_relpath))
    assert {r["format"] for r in rows} == {"TXT"}
    assert sorted(str(r["label"]) for r in rows) == ["neg", "neg", "pos", "pos"]
    assert not (tmp_path / "data" / "converted").exists()
