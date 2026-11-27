"""DATA-5.3: instruction pairs through ingest, clean_text and split, against the real
database; bad rows rejected with reasons; the pairs' adapter on a stored row."""

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.corpus import chat_messages
from stratal.db.engine import get_engine
from stratal.storage.manifest import iter_rows
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _rows(version_id: str) -> list[dict[str, object]]:
    with Session(get_engine()) as session:
        _, manifest = registry.manifest_for(session, get_settings().data_dir, version_id)
    return list(iter_rows(manifest))


def test_DATA_5_3_instruction_pairs_are_ingested_cleaned_and_split(
    runner: Runner, tmp_path: Path
) -> None:
    source = tmp_path / "pairs"
    source.mkdir()
    lines = [
        {"instruction": f"Question {i}?", "input": f"context {i}", "output": f"Answer {i}."}
        for i in range(30)
    ]
    lines.append(lines[0])  # an exact duplicate
    lines.append({"instruction": "No answer.", "output": " "})
    (source / "pairs.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8"
    )
    job = PipelineApi(runner).run(
        {"dataset": "pairs", "source": {"path": str(source)}, "dataset_type": "instruction_pairs"}
    )
    assert job["state"] == "completed", job
    versions = {s["name"]: s["output_version_id"] for s in job["stages"]}
    assert list(versions) == ["ingest", "clean_text", "split"]
    rows = _rows(versions["split"])
    reasons = sorted(str(r["reason_code"]) for r in rows if r["status"] == "rejected")
    assert reasons == ["empty_output", "not_json"]
    assert sum(1 for r in rows if r["status"] == "duplicate") == 1
    accepted = [r for r in rows if r["status"] == "accepted"]
    assert len(accepted) == 30
    parts = {r["split"] for r in accepted}
    assert "train" in parts and parts <= {"train", "val", "test"}  # every pair has a part
    record = json.loads(str(accepted[0]["record"]))
    messages = chat_messages(record)
    assert messages[0]["role"] == "user" and record["instruction"] in messages[0]["content"]
    assert messages[1] == {"role": "assistant", "content": record["output"]}
