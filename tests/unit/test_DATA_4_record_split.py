"""DATA-4.2 and DATA-4.3 for tables and texts (ADR-0019 rule 6): rows of one text group in
one split, strata by class or by the target's quintile, and the leakage check."""

from pathlib import Path

import pytest

from stratal.core.errors import StratalError
from stratal.data.commit import commit_version
from stratal.data.records import LABELLED_TEXT, META_TABLE_SCHEMA, TABULAR
from stratal.data.split_stage import (
    OWNER_UNITS,
    SplitConfig,
    _Record,  # pyright: ignore[reportPrivateUsage]
    record_groups,
    record_leakage_check,
    record_strata,
    run_split,
)
from stratal.storage.manifest import Row, iter_rows, read_metadata
from stratal.testing.fakes import ListSink, fake_version_identity

RECORDS = SplitConfig(stratum_keys=(), leakage_distance=0)


def _rec(key: str, sha: str, group: str | None = None, target: str | float | None = "a") -> _Record:
    return _Record(key, sha, group, target)


def test_DATA_4_2_rows_of_one_text_group_form_one_group_anchored_by_its_smallest_hash() -> None:
    groups = record_groups(
        [_rec("r1", "c3", "img7"), _rec("r2", "a1", "img7"), _rec("r3", "b2"), _rec("r4", "d4")]
    )

    assert [(g.anchor, g.members) for g in groups] == [
        ("a1", ("r1", "r2")),
        ("b2", ("r3",)),
        ("d4", ("r4",)),
    ]


def test_DATA_4_3_strata_are_classes_or_target_quintiles() -> None:
    classes = record_strata(
        [_rec("a", "1", target="cat"), _rec("b", "2", target="dog")], numeric=False
    )
    assert classes == {"a": "cat", "b": "dog"}

    rows = [_rec(f"r{i}", str(i), target=float(i)) for i in range(10)]
    quintiles = record_strata(rows, numeric=True)
    assert [quintiles[f"r{i}"] for i in range(10)] == [
        "q1", "q1", "q2", "q2", "q3", "q3", "q4", "q4", "q5", "q5",
    ]  # fmt: skip


def test_DATA_4_2_the_record_leakage_check_finds_a_group_in_two_splits() -> None:
    rows = [_rec("r1", "a", "g"), _rec("r2", "b", "g"), _rec("r3", "c")]

    assert record_leakage_check(rows, {"r1": "train", "r2": "train", "r3": "test"})["passed"]
    found = record_leakage_check(rows, {"r1": "train", "r2": "val", "r3": "test"})
    assert found["violations"] == 1 and found["examples"][0]["rows"] == ["r1", "r2"]


def _parent(tmp_path: Path, rows: list[Row], stage: str, metadata: dict[str, str]) -> Path:
    record = commit_version(
        data_dir=tmp_path,
        stage=stage,
        parent_version_id="ds_ingest",
        rows=rows,
        config={},
        derive=fake_version_identity,
        sink=ListSink(),
        metadata=metadata,
    )
    return tmp_path / record.manifest_relpath


def _split(
    tmp_path: Path, manifest: Path, dtype: str, config: SplitConfig = RECORDS
) -> tuple[list[Row], Path]:
    result = run_split(
        parent_manifest=manifest,
        parent_version_id="ds_clean",
        data_dir=tmp_path,
        config=config,
        derive=fake_version_identity,
        sink=ListSink(),
        previous=None,
        units=OWNER_UNITS,
        dtype=dtype,
    )
    out = tmp_path / result.record.manifest_relpath
    assert result.record.dtype == dtype
    return list(iter_rows(out)), out


def test_DATA_4_2_captions_of_one_image_land_in_one_split(tmp_path: Path) -> None:
    rows: list[Row] = []
    for image in range(40):
        for caption in range(5):
            key = f"c.jsonl#{image * 5 + caption + 1:07d}"
            rows.append(
                {
                    "row_key": key,
                    "status": "accepted",
                    "sha256": f"{image:04d}{caption:060d}",
                    "text": f"caption {caption} of {image}",
                    "label": "cat" if image % 2 else "dog",
                    "text_group": str(image),
                }
            )
    manifest = _parent(tmp_path, rows, "clean_text", {})

    out, _ = _split(tmp_path, manifest, LABELLED_TEXT)

    by_group: dict[str, set[str]] = {}
    for row in out:
        by_group.setdefault(str(row["text_group"]), set()).add(str(row["split"]))
    assert all(len(splits) == 1 for splits in by_group.values())
    assert {s for splits in by_group.values() for s in splits} == {"train", "val", "test"}
    assert {r["split_stratum"] for r in out} == {"cat", "dog"}


def test_DATA_4_3_a_regression_table_is_stratified_by_target_quintile(tmp_path: Path) -> None:
    schema = (
        '{"columns":[{"kind":"numeric","name":"x","role":"feature"},'
        '{"kind":"numeric","name":"y","role":"target"}],"task":"regression"}'
    )
    rows: list[Row] = [
        {
            "row_key": f"t.csv#{i:07d}",
            "status": "accepted",
            "sha256": f"{i:064d}",
            "record": f'{{"x":{i}.0}}',
            "target_value": float(i),
        }
        for i in range(1, 51)
    ]
    manifest = _parent(tmp_path, rows, "clean_table", {META_TABLE_SCHEMA: schema})

    out, path = _split(tmp_path, manifest, TABULAR)

    assert {r["split_stratum"] for r in out} == {"q1", "q2", "q3", "q4", "q5"}
    assert read_metadata(path)[META_TABLE_SCHEMA] == schema, "the schema is passed on"


def test_DATA_4_3_image_stratum_keys_are_refused_for_records(tmp_path: Path) -> None:
    rows: list[Row] = [{"row_key": "a", "status": "accepted", "sha256": "1" * 64, "label": "x"}]
    manifest = _parent(tmp_path, rows, "clean_table", {})

    with pytest.raises(StratalError, match="stratum_keys must be empty"):
        _split(tmp_path, manifest, TABULAR, SplitConfig())
