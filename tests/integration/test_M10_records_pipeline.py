"""DATA-1.4, DATA-2.4, DATA-1.5 and DATA-5.2 for M10 against the real PostgreSQL and Redis.

Tabular and labelled-text pipelines run through the API and the job runtime
(ADR-0019): ``ingest → clean_table → split`` and ``ingest → clean_text → split →
tokenize``. Labelled images can take their labels from a CSV. The split's
owner units run as fakes, as in the M5 and M8 tests; the record split's own
grouping (text groups) is real.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.records import table_schema_of, table_task
from stratal.data.tokenize import decode_tokens, vocab_of
from stratal.db.engine import get_engine
from stratal.db.models import Dataset
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi as Api
from stratal.testing.synthetic import make_image_folder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]


def _version(version_id: str) -> tuple[str | None, dict[str, Any], list[dict[str, Any]], Path]:
    data_dir = get_settings().data_dir
    with Session(get_engine()) as session:
        version = registry.get_version(session, version_id)
        _, manifest = registry.manifest_for(session, data_dir, version_id)
        rows = list(registry.load_version(session, data_dir, version_id))
        return version.dtype, dict(version.counts), rows, manifest


def _modality(slug: str) -> str:
    with Session(get_engine()) as session:
        dataset = session.query(Dataset).filter_by(slug=slug).one()
        return dataset.modality


def _table_source(root: Path) -> Path:
    root.mkdir(parents=True)
    lines = ["radius,texture,shape,diagnosis"]
    for i in range(60):
        label = "malignant" if i % 3 == 0 else "benign"
        lines.append(
            f"{10 + i * 0.37:.2f},{15 + (i * 7) % 11},{'round' if i % 2 else 'oval'},{label}"
        )
    lines.append(lines[1])  # an exact duplicate of the first record
    lines.append("12.5,,oval,")  # no target
    (root / "cells.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_DATA_1_4_a_sample_csv_runs_through_the_tabular_pipeline(
    runner: Runner, tmp_path: Path
) -> None:
    body = {
        "dataset": "m10table",
        "source": {"path": str(_table_source(tmp_path / "src"))},
        "dataset_type": "tabular",
        "stages": {"ingest": {"table": {"target": "diagnosis"}}},
    }

    job = Api(runner).run(body)

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]] == ["ingest", "clean_table", "split"]
    dtype, counts, rows, manifest = _version(job["output_version_id"])
    assert dtype == "tabular"
    assert _modality("m10table") == "tabular"
    schema = table_schema_of(manifest)
    assert schema is not None and table_task(schema) == "classification"
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    assert by_status == {"accepted": 60, "duplicate": 1, "rejected": 1}
    accepted = [r for r in rows if r["status"] == "accepted"]
    assert all(r["split"] in {"train", "val", "test"} for r in accepted)
    assert {r["split_stratum"] for r in accepted} == {"benign", "malignant"}
    assert counts["split"]["leakage_check"]["passed"]
    assert json.loads(accepted[0]["record"]).keys() == {"radius", "texture", "shape"}


def _text_source(root: Path) -> Path:
    root.mkdir(parents=True)
    lines: list[str] = []
    for image in range(30):
        label = ("dog", "cat", "bird")[image % 3]
        for caption in range(3):
            text = f"A {label} number {image}, caption {caption}."
            lines.append(json.dumps({"text": text, "label": label, "group": image}))
    lines.append(json.dumps({"text": "   ", "label": "dog"}))  # planted: empty after normalising
    lines.append(json.dumps({"text": "x" * 50, "label": "cat"}))  # planted: too long
    lines.append(json.dumps({"text": "A  dog number 0,\tcaption 0.", "label": "dog"}))  # duplicate
    (root / "captions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_DATA_2_4_a_sample_jsonl_is_cleaned_split_by_group_and_tokenized(
    runner: Runner, tmp_path: Path
) -> None:
    body = {
        "dataset": "m10text",
        "source": {"path": str(_text_source(tmp_path / "src"))},
        "dataset_type": "labelled_text",
        "stages": {"clean_text": {"max_chars": 40}, "tokenize": {"min_freq": 1}},
    }

    job = Api(runner).run(body)

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]] == ["ingest", "clean_text", "split", "tokenize"]
    assert _modality("m10text") == "text"
    clean = next(s for s in job["stages"] if s["name"] == "clean_text")
    _, clean_counts, clean_rows, _ = _version(clean["output_version_id"])
    reasons = {r["reason_code"] for r in clean_rows if r["status"] != "accepted"}
    assert reasons == {"empty_text", "text_too_long", "exact_duplicate"}
    assert clean_counts["by_reason"] == {"empty_text": 1, "exact_duplicate": 1, "text_too_long": 1}
    assert clean["metrics"]["rejected_empty_text"] == 1

    dtype, counts, rows, manifest = _version(job["output_version_id"])
    assert dtype == "labelled_text"
    accepted = [r for r in rows if r["status"] == "accepted"]
    assert len(accepted) == 90
    groups: dict[str, set[str]] = {}
    for row in accepted:
        groups.setdefault(str(row["text_group"]), set()).add(str(row["split"]))
    assert all(len(s) == 1 for s in groups.values()), "captions of one image share a split"
    vocab = vocab_of(manifest)
    assert vocab is not None and vocab["size"] == counts["tokenize"]["vocab_size"]
    length = int(vocab["max_length"])
    assert all(len(decode_tokens(str(r["tokens"]))) == length for r in accepted)
    assert all(r["token_count"] > 0 for r in accepted)


def test_DATA_1_5_labelled_images_take_their_labels_from_a_csv(
    runner: Runner, tmp_path: Path
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=8, size=48)
    paths = sorted(folder.valid)
    csv_lines = ["file,cls", *(f"{p},{'cat' if i % 2 else 'dog'}" for i, p in enumerate(paths[:7]))]
    csv_lines.append("valid/gone.png,cat")
    (folder.root / "labels.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    label_from = {"csv": "labels.csv", "path_column": "file", "label_column": "cls"}
    body = {
        "dataset": "m10csv",
        "source": {"path": str(folder.root)},
        "dataset_type": "labelled_images",
        "stages": {"ingest": {"min_size": 32, "label_from": label_from}},
        "until_stage": "ingest",
    }

    job = Api(runner).run(body)

    assert job["state"] == "completed", job
    dtype, counts, rows, _ = _version(job["output_version_id"])
    assert dtype == "labelled_images"
    by_key = {r["row_key"]: r for r in rows}
    assert "labels.csv" not in by_key
    assert [by_key[p]["label"] for p in paths[:2]] == ["dog", "cat"]
    assert by_key[paths[7]]["reason_code"] == "no_label"
    assert counts["labels_csv"]["missing_files"]["examples"] == [
        {"line": 9, "path": "valid/gone.png"}
    ]


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        ({"dataset_type": "tabular"}, "table"),
        ({"dataset_type": "labelled_text", "stages": {"ingest": {"text": None}}}, "text"),
        (
            {"dataset_type": "labelled_images", "stages": {"ingest": {"table": {"target": "y"}}}},
            "ingest",  # the type's label_from: folder, and a table: at most one of them
        ),
    ],
)
def test_DATA_5_2_ingest_settings_must_match_the_dataset_type(
    runner: Runner, tmp_path: Path, body: dict[str, Any], loc: str
) -> None:
    (tmp_path / "src").mkdir()
    response = Api(runner).post(
        {"dataset": "m10bad", "source": {"path": str(tmp_path / "src")}, **body}
    )

    assert response.status_code == 422, response.text
    assert [e["loc"][-1] for e in response.json()["errors"]] == [loc]
