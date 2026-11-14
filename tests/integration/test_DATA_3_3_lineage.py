"""DATA-3.3: lineage (parent version, stage, config, timestamp) for each version.

Runs against the real PostgreSQL. The version identity is a stand-in here;
what is under test is recording and walking lineage.
"""

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict, NotFound
from stratal.data import registry
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.db.models import DatasetVersion
from stratal.storage.manifest import iter_rows
from stratal.testing.fakes import ListSink, fake_version_identity
from stratal.testing.synthetic import make_image_folder

pytestmark = pytest.mark.integration


def _raw(tmp_path: Path) -> tuple[VersionRecord, int]:
    folder = make_image_folder(tmp_path / "src", valid=6, corrupt=1)
    result = run_ingest(
        source=folder.root,
        data_dir=tmp_path / "data",
        config=IngestConfig(),
        derive=fake_version_identity,
        sink=ListSink(),
    )
    return result.record, result.new_blob_bytes


def _child(tmp_path: Path, parent: VersionRecord, stage: str, **config: int) -> VersionRecord:
    return commit_version(
        data_dir=tmp_path / "data",
        stage=stage,
        parent_version_id=parent.version_id,
        rows=iter_rows(tmp_path / "data" / parent.manifest_relpath),
        config=config,
        derive=fake_version_identity,
        sink=ListSink(),
    )


def test_DATA_3_3_lineage_can_be_followed_from_any_version_back_to_raw(
    session: Session, tmp_path: Path
) -> None:
    dataset = registry.ensure_dataset(session, slug="lineage-walk", modality="image")
    raw, new_bytes = _raw(tmp_path)
    clean = _child(tmp_path, raw, "clean", threshold=4)
    label = _child(tmp_path, clean, "label", batch_size=16)
    for record in (raw, clean, label):
        registry.register_version(session, dataset=dataset, record=record, new_blob_bytes=new_bytes)

    chain = registry.lineage(session, label.version_id)

    assert [v.version_id for v in chain] == [label.version_id, clean.version_id, raw.version_id]
    assert [v.stage for v in chain] == ["label", "clean", "ingest"]
    assert chain[-1].parent_version_id is None
    assert [v.version_id for v in registry.lineage(session, clean.version_id)] == [
        clean.version_id,
        raw.version_id,
    ]
    assert [v.version_id for v in registry.lineage(session, raw.version_id)] == [raw.version_id]


def test_DATA_3_3_each_version_records_parent_stage_config_and_timestamp(
    session: Session, tmp_path: Path
) -> None:
    dataset = registry.ensure_dataset(session, slug="lineage-fields", modality="image")
    raw, new_bytes = _raw(tmp_path)
    clean = _child(tmp_path, raw, "clean", threshold=4)
    registry.register_version(session, dataset=dataset, record=raw, new_blob_bytes=new_bytes)
    version, created = registry.register_version(
        session, dataset=dataset, record=clean, new_blob_bytes=0, code_version="abc1234"
    )
    session.refresh(version)

    assert created is True
    assert version.parent_version_id == raw.version_id
    assert version.stage == "clean"
    assert version.config == {"threshold": 4}
    assert version.config_hash == clean.config_hash
    assert version.created_at is not None and version.created_at.tzinfo is not None
    assert version.row_count == 7
    assert version.counts["rejected"] == 1
    assert version.manifest_path == clean.manifest_relpath
    assert version.code_version == "abc1234"


def test_DATA_3_3_registering_the_same_version_twice_returns_the_first(
    session: Session, tmp_path: Path
) -> None:
    dataset = registry.ensure_dataset(session, slug="lineage-idempotent", modality="image")
    raw, new_bytes = _raw(tmp_path)

    first, created_first = registry.register_version(
        session, dataset=dataset, record=raw, new_blob_bytes=new_bytes
    )
    second, created_second = registry.register_version(
        session, dataset=dataset, record=raw, new_blob_bytes=0
    )

    assert (created_first, created_second) == (True, False)
    assert second.version_id == first.version_id
    assert second.new_blob_bytes == new_bytes
    assert [v.version_id for v in registry.list_versions(session, "lineage-idempotent")] == [
        raw.version_id
    ]


def test_DATA_3_3_a_version_cannot_name_a_parent_that_does_not_exist(
    session: Session, tmp_path: Path
) -> None:
    dataset = registry.ensure_dataset(session, slug="lineage-orphan", modality="image")
    raw, _ = _raw(tmp_path)
    orphan = _child(tmp_path, raw, "clean", threshold=4)

    with pytest.raises(NotFound):
        registry.register_version(session, dataset=dataset, record=orphan, new_blob_bytes=0)


def test_DATA_3_3_the_database_itself_refuses_a_dangling_parent(session: Session) -> None:
    dataset = registry.ensure_dataset(session, slug="lineage-fk", modality="image")
    session.add(
        DatasetVersion(
            version_id="ds_dangling",
            dataset_id=dataset.id,
            stage="clean",
            parent_version_id="ds_does_not_exist",
            content_hash="0" * 64,
            config_hash="0" * 64,
            config={},
            manifest_path="manifests/ds_dangling.parquet",
            manifest_bytes=1,
            row_count=0,
            counts={},
            new_blob_bytes=0,
            schema_version=1,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_DATA_3_3_unknown_version_and_dataset_are_typed_errors(session: Session) -> None:
    with pytest.raises(NotFound):
        registry.lineage(session, "ds_nope")
    with pytest.raises(NotFound):
        registry.list_versions(session, "no-such-dataset")


def test_DATA_3_3_a_dataset_slug_keeps_its_modality(session: Session) -> None:
    registry.ensure_dataset(session, slug="lineage-modality", modality="image")

    assert registry.ensure_dataset(session, slug="lineage-modality", modality="image").id
    with pytest.raises(Conflict):
        registry.ensure_dataset(session, slug="lineage-modality", modality="tabular")
