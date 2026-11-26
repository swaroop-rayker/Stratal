"""DATA-1.4.AC2 through the API: a delimited-text table is listed with its columns and
ingests as a tabular dataset, converted to Parquet in the data folder.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi as Api

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]

HEADER = "Date;Time;Global_active_power;Voltage;Sub_metering_3"


def _power(folder: Path) -> Path:
    folder.mkdir(parents=True)
    lines = [HEADER]
    for i in range(40):
        lines.append(f"16/12/2006;17:{i:02d}:00;{4 + i * 0.01:.3f};{234 + i % 5:.3f};{i % 3}.000")
    lines.append("21/12/2006;11:23:00;?;?;?")
    (folder / "household_power_consumption.txt").write_text("\n".join(lines) + "\n", "utf-8")
    (folder / "README.txt").write_text("Household power, from UCI.\nOne minute a row.\n", "utf-8")
    return folder


def test_DATA_1_4_AC2_sources_list_a_text_tables_columns(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sources"
    _power(root / "power")
    (root / "notes").mkdir()
    (root / "notes" / "a.txt").write_text("Just words.\nMore words.\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "sources_dir", root)
    sources = {s["name"]: s for s in Api(runner).client.get("/api/v1/sources").json()["items"]}
    assert sources["power"]["columns"] == HEADER.split(";")
    assert sources["notes"]["columns"] is None


def test_DATA_1_4_AC2_a_text_table_ingests_and_runs_the_tabular_pipeline(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    job = api.run(
        {
            "dataset": "power-txt",
            "source": {"path": str(_power(tmp_path / "src"))},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "Global_active_power"}}},
        }
    )
    assert job["state"] == "completed", job
    events: list[dict[str, Any]] = api.client.get(f"/api/v1/jobs/{job['id']}/events").json()[
        "items"
    ]
    assert any(e.get("substage") == "convert" for e in events)

    data_dir = get_settings().data_dir
    with Session(get_engine()) as session:
        lineage = registry.lineage(session, str(job["output_version_id"]))
        raw_id = lineage[-1].version_id
        rows = list(registry.load_version(session, data_dir, raw_id))
    by_key = {str(r["row_key"]): r for r in rows}
    table = [r for k, r in by_key.items() if k.startswith("household_power_consumption.txt#")]
    assert len(table) == 41
    assert {r["format"] for r in table} == {"TXT"}
    last = table[-1]
    assert last["status"] == "rejected" and last["reason_code"] == "missing_target"
    assert json.loads(str(last["record"]))["Voltage"] is None
    assert by_key["README.txt"]["reason_code"] == "unsupported_format"
    assert list((data_dir / "converted").glob("*.parquet"))
