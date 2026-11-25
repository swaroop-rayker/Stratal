"""DATA-7.1 and DATA-7.2 for M11b: the transform stage against the real PostgreSQL and Redis.

ADR-0019 Addendum B rules 25 to 28. A table is ingested, then its raw version is
transformed through the API and the job runtime: ``transform → clean_table →
split``. The split's owner units run as fakes, as in the M10 tests.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.records import table_schema_of
from stratal.db.engine import get_engine
from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi as Api

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("fake_units")]

OPS: list[dict[str, Any]] = [
    {"op": "drop", "columns": ["ash"]},
    {"op": "rename", "from": "od280/od315", "to": "od280_od315"},
    {"op": "set_kind", "column": "magnesium", "kind": "categorical"},
    {"op": "set_encoding", "column": "magnesium", "encoding": "embedding"},
]


def _source(root: Path) -> Path:
    root.mkdir(parents=True)
    lines = ["alcohol,ash,magnesium,od280/od315,cultivar"]
    for i in range(45):
        lines.append(
            f"{12 + i * 0.05:.2f},{2 + (i % 7) * 0.1:.1f},{90 + i % 9},{3.0 + i % 4},c{i % 3}"
        )
    lines.append("13.10,2.4,95,3.0,")  # no target: rejected at ingest as missing_target
    (root / "wine.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _rows(version_id: str) -> tuple[list[dict[str, Any]], Path]:
    data_dir = get_settings().data_dir
    with Session(get_engine()) as session:
        _, manifest = registry.manifest_for(session, data_dir, version_id)
        return list(registry.load_version(session, data_dir, version_id)), manifest


def _raw(api: Api, tmp_path: Path, slug: str) -> str:
    job = api.run(
        {
            "dataset": slug,
            "source": {"path": str(_source(tmp_path / "src"))},
            "dataset_type": "tabular",
            "stages": {"ingest": {"table": {"target": "cultivar"}}},
            "until_stage": "ingest",
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])


def test_DATA_7_1_transform_runs_on_the_raw_version_and_leaves_it_unchanged(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bwine")
    _, raw_manifest = _rows(raw)
    before = hashlib.sha256(raw_manifest.read_bytes()).hexdigest()

    job = api.run({"from_version": raw, "stages": {"transform": {"operations": OPS}}})

    assert job["state"] == "completed", job
    assert [s["name"] for s in job["stages"]] == ["transform", "clean_table", "split"]
    transform = next(s for s in job["stages"] if s["name"] == "transform")
    rows, manifest = _rows(transform["output_version_id"])
    assert hashlib.sha256(raw_manifest.read_bytes()).hexdigest() == before
    schema = table_schema_of(manifest)
    assert schema is not None
    assert [c.name for c in schema.columns] == ["alcohol", "magnesium", "od280_od315", "cultivar"]
    magnesium = next(c for c in schema.columns if c.name == "magnesium")
    assert (magnesium.kind, magnesium.encoding) == ("categorical", "embedding")
    first = json.loads(next(r for r in rows if r["status"] == "accepted")["record"])
    assert first == {"alcohol": 12.0, "magnesium": "90", "od280_od315": 3.0}
    with Session(get_engine()) as session:
        version = registry.get_version(session, transform["output_version_id"])
        assert version.parent_version_id == raw


def test_DATA_7_1_same_operations_reuse_the_version_one_change_makes_another(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bmemo")
    body = {
        "from_version": raw,
        "stages": {"transform": {"operations": OPS}},
        "until_stage": "transform",
    }

    first = api.run(body)
    again = api.run(body)
    changed = api.run({**body, "stages": {"transform": {"operations": [*OPS[:3]]}}})

    assert first["output_version_id"] == again["output_version_id"]
    assert again["stages"][0]["cache_hit"]
    assert changed["output_version_id"] != first["output_version_id"]


def test_DATA_7_1_a_transform_of_a_transform_is_refused(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11btwice")
    done = api.run(
        {
            "from_version": raw,
            "stages": {"transform": {"operations": OPS[:1]}},
            "until_stage": "transform",
        }
    )

    response = api.post(
        {
            "from_version": done["output_version_id"],
            "stages": {"transform": {"operations": OPS[:1]}},
        }
    )

    assert response.status_code == 422
    problem = response.json()
    assert "raw (ingest) version only" in problem["detail"]
    assert problem["errors"][0]["loc"][-1] == "transform"


def test_DATA_7_1_fitted_operations_refused_before_the_run(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bfit")

    response = api.post(
        {
            "from_version": raw,
            "stages": {"transform": {"operations": [{"op": "scale", "columns": ["ash"]}]}},
        }
    )

    assert response.status_code == 422
    msgs = " ".join(e["msg"] for e in response.json()["errors"])
    assert "scale is fitted on the data" in msgs and "model's preprocessing" in msgs


def test_DATA_7_2_a_refused_operation_names_its_column_before_the_run(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11brefuse")

    response = api.post(
        {
            "from_version": raw,
            "stages": {"transform": {"operations": [{"op": "drop", "columns": ["cultivar"]}]}},
        }
    )

    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["loc"] == ["stages", "transform", "operations", 0, "columns"]
    assert "cultivar is the target" in error["msg"]
    assert not api.queued


def test_DATA_7_2_a_new_target_readmits_and_clean_table_sees_the_new_duplicates(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11btarget")

    job = api.run(
        {
            "from_version": raw,
            "stages": {
                "transform": {
                    "operations": [
                        {"op": "set_target", "column": "alcohol"},
                        {"op": "drop", "columns": ["cultivar", "magnesium", "od280/od315"]},
                    ]
                }
            },
            "until_stage": "clean_table",
        }
    )

    assert job["state"] == "completed", job
    transform, clean = job["stages"][0], job["stages"][1]
    rows, _ = _rows(transform["output_version_id"])
    assert sum(1 for r in rows if r["status"] == "accepted") == 46  # the empty-target row is back
    assert transform["metrics"]["readmitted"] == 1
    clean_rows, _ = _rows(clean["output_version_id"])
    # Only ash is left: its 7 values repeat, so clean_table marks every repeat a duplicate.
    assert sum(1 for r in clean_rows if r["status"] == "accepted") == 7


def test_DATA_7_1_a_schema_only_transform_gives_its_own_downstream_versions(
    runner: Runner, tmp_path: Path
) -> None:
    """set_encoding and reorder leave every row as it was; clean_table and split record
    their input's schema hash, so they do not collide with the versions made from raw
    (ADR-0019 Addendum B, as built)."""
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bschema")
    straight = api.run({"from_version": raw})
    ops = [{"op": "set_encoding", "column": "ash", "encoding": "ignore"}]
    through = api.run({"from_version": raw, "stages": {"transform": {"operations": ops}}})

    assert straight["state"] == through["state"] == "completed", (straight, through)
    plain = {s["name"]: s["output_version_id"] for s in straight["stages"]}
    encoded = {s["name"]: s["output_version_id"] for s in through["stages"]}
    assert plain["clean_table"] != encoded["clean_table"]
    assert plain["split"] != encoded["split"]
    rows_plain, _ = _rows(plain["split"])
    rows_encoded, manifest = _rows(encoded["split"])
    assert [r["record"] for r in rows_plain] == [r["record"] for r in rows_encoded]
    schema = table_schema_of(manifest)
    assert schema is not None and next(c for c in schema.columns if c.name == "ash").ignored
    with Session(get_engine()) as session:
        clean = registry.get_version(session, encoded["clean_table"])
        assert clean.parent_version_id == encoded["transform"]
        assert (
            clean.config["input_schema"]
            != registry.get_version(session, plain["clean_table"]).config["input_schema"]
        )
    again = api.run({"from_version": raw, "stages": {"transform": {"operations": ops}}})
    assert all(s["cache_hit"] for s in again["stages"])


# ---------------------------------------------------------------------------- DATA-7.5


def test_DATA_7_5_the_preview_equals_the_stages_first_rows(runner: Runner, tmp_path: Path) -> None:
    from stratal.data.records import row_values

    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bpreview")
    ops = [
        *OPS,
        {"op": "set_target", "column": "alcohol"},
        {"op": "filter", "expression": "magnesium != '93'"},
    ]

    response = api.client.post(
        f"/api/v1/versions/{raw}/transform/preview", json={"operations": ops, "limit": 50}
    )
    job = api.run(
        {
            "from_version": raw,
            "stages": {"transform": {"operations": ops}},
            "until_stage": "transform",
        }
    )

    assert response.status_code == 200, response.text
    preview = response.json()
    rows, manifest = _rows(job["output_version_id"])
    schema = table_schema_of(manifest)
    assert schema is not None
    expected = [
        {
            "row_key": r["row_key"],
            "status": r["status"],
            "reason_code": r["reason_code"],
            "reason_detail": r["reason_detail"],
            "values": row_values(r, schema),
        }
        for r in rows[:50]
    ]
    assert preview["rows"] == json.loads(json.dumps(expected))
    assert [c["name"] for c in preview["columns"]] == [c.name for c in schema.columns]
    assert preview["counts"]["readmitted"] == 1
    assert len(preview["steps"]) == len(ops)
    assert [c["name"] for c in preview["steps"][1]["columns"]][2] == "od280_od315"
    assert preview["counts"]["by_status"]["filtered"] == preview["steps"][-1]["filtered"] > 0


def test_DATA_7_5_the_preview_refuses_with_the_jobs_locations(
    runner: Runner, tmp_path: Path
) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bprevbad")
    url = f"/api/v1/versions/{raw}/transform/preview"

    bad = api.client.post(url, json={"operations": [{"op": "drop", "columns": ["cultivar"]}]})
    fitted = api.client.post(url, json={"operations": [{"op": "impute", "columns": ["ash"]}]})
    empty = api.client.post(url, json={})

    assert bad.status_code == fitted.status_code == 422
    assert bad.json()["errors"][0]["loc"] == ["stages", "transform", "operations", 0, "columns"]
    assert fitted.json()["errors"][0]["loc"] == ["stages", "transform", "operations", 0]
    assert "impute is fitted on the data" in fitted.json()["errors"][0]["msg"]
    assert empty.status_code == 200 and empty.json()["steps"] == []
    assert len(empty.json()["rows"]) == 46


def _expected_columns(version_id: str) -> dict[str, dict[str, Any]]:
    """What the summary should say, computed straight from the manifest's accepted rows."""
    from stratal.data.records import row_values

    rows, manifest = _rows(version_id)
    schema = table_schema_of(manifest)
    assert schema is not None
    accepted = [row_values(r, schema) for r in rows if r["status"] == "accepted"]
    out: dict[str, dict[str, Any]] = {}
    for column in schema.columns:
        values = [v[column.name] for v in accepted]
        present = sorted(v for v in values if v is not None)
        out[column.name] = {
            "missing": len(values) - len(present),
            "distinct": len(set(present)),
            "kind": column.kind,
            "min": present[0] if present and column.kind == "numeric" else None,
            "max": present[-1] if present and column.kind == "numeric" else None,
            "count": len(present),
        }
    return out


def test_DATA_7_5_the_column_summary_matches_the_manifest(runner: Runner, tmp_path: Path) -> None:
    api = Api(runner)
    raw = _raw(api, tmp_path, "m11bcols")
    job = api.run({"from_version": raw, "stages": {"transform": {"operations": OPS}}})
    versions = [raw, *(s["output_version_id"] for s in job["stages"])]

    for version_id in versions:
        response = api.client.get(f"/api/v1/versions/{version_id}/columns")
        assert response.status_code == 200, response.text
        body = response.json()
        expected = _expected_columns(version_id)
        assert [c["name"] for c in body["columns"]] == list(expected)
        for column in body["columns"]:
            want = expected[column["name"]]
            assert column["missing"] == want["missing"], (version_id, column)
            assert column["distinct"] == want["distinct"], (version_id, column)
            dist = column["distribution"]
            if want["kind"] == "numeric":
                assert (dist["min"], dist["max"]) == (want["min"], want["max"])
                assert sum(dist["histogram"]["counts"]) == sum(dist["sparkline"]) == want["count"]
            else:
                assert sum(t["count"] for t in dist["top"]) == want["count"]
    raw_body = api.client.get(f"/api/v1/versions/{raw}/columns").json()
    assert raw_body["counted"] == 45 and raw_body["not_counted"] == {"missing_target": 1}
    split_body = api.client.get(f"/api/v1/versions/{versions[-1]}/columns").json()
    magnesium = next(c for c in split_body["columns"] if c["name"] == "magnesium")
    assert (magnesium["kind"], magnesium["encoding"]) == ("categorical", "embedding")


def test_DATA_5_2_dataset_types_list_their_stages_and_sources_their_columns(
    runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(runner)
    types = {t["name"]: t for t in api.client.get("/api/v1/dataset-types").json()}
    root = tmp_path / "sources"
    _source(root / "wine")
    (root / "two").mkdir()
    (root / "two" / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (root / "two" / "b.csv").write_text("x\n1\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "sources_dir", root)
    sources = {s["name"]: s for s in api.client.get("/api/v1/sources").json()["items"]}

    assert [(s["name"], s["optional"]) for s in types["tabular"]["stages"]] == [
        ("ingest", False),
        ("transform", True),
        ("clean_table", False),
        ("split", False),
    ]
    assert types["tabular"]["source"] == "one .csv or .parquet table"
    assert [s["name"] for s in types["labelled_text"]["stages"]][-1] == "tokenize"
    assert sources["wine"]["columns"] == ["alcohol", "ash", "magnesium", "od280/od315", "cultivar"]
    assert sources["two"]["columns"] is None
