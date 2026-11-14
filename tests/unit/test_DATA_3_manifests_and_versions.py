"""DATA-3.1 and DATA-3.4, plus the storage invariants in docs/DATA_MODEL.md §8.

These use a stand-in for the version identity (DATA-3.2 is owner-implemented),
so they test the plumbing around it, not the identity itself.
"""

from pathlib import Path

import pytest

from stratal.data.commit import commit_version
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import (
    COLUMNS,
    ManifestError,
    Row,
    iter_rows,
    read_metadata,
    write_manifest,
)
from stratal.testing.fakes import ListSink, fake_version_identity
from stratal.testing.synthetic import make_image_folder


def _row(key: str, **extra: object) -> Row:
    return {"row_key": key, "sha256": "a" * 64, "status": "accepted", **extra}


def test_DATA_3_1_ingest_writes_a_manifest_with_status_and_content_hash(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=5, corrupt=2)

    result = run_ingest(
        source=folder.root,
        data_dir=tmp_path / "data",
        config=IngestConfig(),
        derive=fake_version_identity,
        sink=ListSink(),
    )

    manifest = tmp_path / "data" / result.record.manifest_relpath
    assert manifest.is_file()
    assert manifest.name == f"{result.record.version_id}.parquet"
    rows = list(iter_rows(manifest))
    assert len(rows) == 7
    for row in rows:
        assert set(row) == set(COLUMNS)
        assert row["status"] in {"accepted", "rejected"}
        assert isinstance(row["sha256"], str) and len(row["sha256"]) == 64
    assert read_metadata(manifest) == {
        "stratal.schema_version": "1",
        "stratal.stage": "ingest",
        "stratal.parent_version_id": "",
    }


def test_DATA_3_1_any_stage_commit_produces_a_manifest(tmp_path: Path) -> None:
    record = commit_version(
        data_dir=tmp_path,
        stage="clean",
        parent_version_id="ds_parent",
        rows=[
            _row("a.png"),
            _row(
                "b.png", status="duplicate", decided_at_stage="clean", reason_code="exact_duplicate"
            ),
        ],
        config={"threshold": 4},
        derive=fake_version_identity,
        sink=ListSink(),
    )

    assert (tmp_path / record.manifest_relpath).is_file()
    assert record.stage == "clean"
    assert record.parent_version_id == "ds_parent"
    assert record.row_count == 2
    assert record.counts["duplicate"] == 1
    assert (
        read_metadata(tmp_path / record.manifest_relpath)["stratal.parent_version_id"]
        == "ds_parent"
    )


def test_DATA_3_1_manifest_round_trips_values_and_nulls(tmp_path: Path) -> None:
    path = tmp_path / "m.parquet"
    rows = [
        _row("a.png", width=640, height=480, caption="a dog at night", clip_score=0.25),
        _row("b.png"),
    ]

    stats = write_manifest(path, rows, metadata={"stratal.stage": "label"})
    back = list(iter_rows(path))

    assert stats.row_count == 2
    assert stats.bytes_written == path.stat().st_size
    assert back[0]["caption"] == "a dog at night"
    assert back[0]["clip_score"] == pytest.approx(0.25)
    assert back[1]["caption"] is None and back[1]["width"] is None


def test_DATA_3_1_streams_in_batches(tmp_path: Path) -> None:
    path = tmp_path / "m.parquet"
    write_manifest(path, (_row(f"{i:06d}.png") for i in range(5000)), metadata={}, batch_size=512)

    assert sum(1 for _ in iter_rows(path, batch_size=700)) == 5000
    assert [r["row_key"] for r in iter_rows(path, columns=["row_key"])][:2] == [
        "000000.png",
        "000001.png",
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([_row("b.png"), _row("a.png")], "sorted"),
        ([_row("a.png"), _row("a.png")], "sorted"),
        ([{"row_key": "a.png", "status": "accepted", "colour": "red"}], "unknown manifest columns"),
        ([{"row_key": "a.png", "status": "gone"}], "invalid status"),
        ([{"row_key": "a.png", "status": "rejected"}], "decided_at_stage"),
        ([{"row_key": "", "status": "accepted"}], "row_key"),
    ],
)
def test_DATA_3_1_invalid_rows_are_refused_and_leave_no_file(
    tmp_path: Path, rows: list[Row], message: str
) -> None:
    path = tmp_path / "m.parquet"

    with pytest.raises(ManifestError, match=message):
        write_manifest(path, rows, metadata={})

    assert list(tmp_path.iterdir()) == []


def test_DATA_3_1_manifests_and_blobs_are_read_only_once_written(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=2)
    result = run_ingest(
        source=folder.root,
        data_dir=tmp_path / "data",
        config=IngestConfig(),
        derive=fake_version_identity,
        sink=ListSink(),
    )

    manifest = tmp_path / "data" / result.record.manifest_relpath
    store = BlobStore(tmp_path / "data")
    blob = store.path_for(next(iter_rows(manifest))["sha256"])

    assert manifest.stat().st_mode & 0o222 == 0
    assert blob.stat().st_mode & 0o222 == 0


def test_DATA_3_1_every_accepted_row_has_its_blob(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=6, undersized=2, corrupt=2)
    result = run_ingest(
        source=folder.root,
        data_dir=tmp_path / "data",
        config=IngestConfig(),
        derive=fake_version_identity,
        sink=ListSink(),
    )

    store = BlobStore(tmp_path / "data")
    accepted = [
        r
        for r in iter_rows(tmp_path / "data" / result.record.manifest_relpath)
        if r["status"] == "accepted"
    ]

    assert len(accepted) == 6
    assert all(store.exists(row["sha256"]) for row in accepted)


def test_DATA_3_4_a_new_version_adds_only_manifest_bytes(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=30, undersized=5)
    data_dir = tmp_path / "data"
    store = BlobStore(data_dir)

    def ingest(min_size: int):
        return run_ingest(
            source=folder.root,
            data_dir=data_dir,
            config=IngestConfig(min_size=min_size),
            derive=fake_version_identity,
            sink=ListSink(),
        )

    first = ingest(256)
    blob_bytes_after_first = store.total_bytes()
    second = ingest(64)

    assert first.new_blob_bytes == blob_bytes_after_first > 0
    assert second.record.version_id != first.record.version_id
    assert second.new_blob_bytes == 0
    assert store.total_bytes() == blob_bytes_after_first
    manifests = sorted(p.name for p in (data_dir / "manifests").iterdir())
    assert manifests == sorted(
        [f"{first.record.version_id}.parquet", f"{second.record.version_id}.parquet"]
    )
    assert second.record.manifest_bytes < blob_bytes_after_first


def test_DATA_3_4_new_files_cost_only_their_own_bytes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    small = make_image_folder(tmp_path / "src", valid=10, seed=1)

    def ingest():
        return run_ingest(
            source=small.root,
            data_dir=data_dir,
            config=IngestConfig(),
            derive=fake_version_identity,
            sink=ListSink(),
        )

    ingest()
    before = BlobStore(data_dir).total_bytes()
    extra = small.root / "valid" / "added.png"
    extra.write_bytes((small.root / small.valid[0]).read_bytes() + b"\0")
    result = ingest()

    assert result.new_blob_bytes == extra.stat().st_size
    assert BlobStore(data_dir).total_bytes() == before + extra.stat().st_size


def test_DATA_3_4_recommitting_identical_content_keeps_the_existing_manifest(
    tmp_path: Path,
) -> None:
    folder = make_image_folder(tmp_path / "src", valid=4)

    def ingest():
        return run_ingest(
            source=folder.root,
            data_dir=tmp_path / "data",
            config=IngestConfig(),
            derive=fake_version_identity,
            sink=ListSink(),
        )

    first, second = ingest(), ingest()

    assert second.record.version_id == first.record.version_id
    assert first.record.reused_manifest is False
    assert second.record.reused_manifest is True
    assert len(list((tmp_path / "data" / "manifests").iterdir())) == 1
    assert list((tmp_path / "data" / "tmp").iterdir()) == []
