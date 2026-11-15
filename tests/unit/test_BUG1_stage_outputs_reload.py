"""BUG1: data after cleaning and preprocessing is stored, handled and retrieved properly.

For every stage: the output found by its version ID reloads to rows that give
the same version ID again (so the content is exactly what was committed), its
metadata names the right stage and parent, and every file it points at is in
the blob store under its own hash. The database half (finding the manifest
from the registered version) is in tests/integration/test_M3_pipeline.py.
"""

import hashlib
from pathlib import Path

from stratal.data.clean import DedupeUnits
from stratal.data.commit import VersionRecord, manifest_relpath
from stratal.data.preprocess import PreprocessConfig
from stratal.data.versioning import derive_version_identity
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import iter_rows, read_metadata
from stratal.testing import stagekit
from stratal.testing.fakes import fake_perceptual_hash, no_exact_duplicates, no_near_duplicates
from stratal.testing.synthetic import make_image_folder

FAKES = DedupeUnits(no_exact_duplicates, fake_perceptual_hash, no_near_duplicates)


def _assert_reloads(data: Path, record: VersionRecord) -> None:
    path = data / manifest_relpath(record.version_id)
    rows = list(iter_rows(path))

    again = derive_version_identity(iter(rows), record.config)
    assert again.version_id == record.version_id
    assert again.content_hash == record.content_hash
    assert read_metadata(path)["stratal.stage"] == record.stage
    assert read_metadata(path)["stratal.parent_version_id"] == (record.parent_version_id or "")
    assert len(rows) == record.row_count
    store = BlobStore(data)
    for row in rows:
        if row["status"] != "accepted":
            continue
        for column in ("sha256", "derived_sha256"):
            if row[column] is None:
                continue
            stored = store.path_for(row[column]).read_bytes()
            assert hashlib.sha256(stored).hexdigest() == row[column], f"{column} of {row}"


def test_BUG1_every_stage_output_reloads_by_version_id_and_equals_what_was_written(
    tmp_path: Path,
) -> None:
    folder = make_image_folder(
        tmp_path / "src", valid=12, corrupt=2, undersized=1, exif_rotated=2, odd_modes=3
    )
    data = tmp_path / "data"

    raw = stagekit.ingest(folder.root, data)
    cleaned = stagekit.clean(data, raw, FAKES).record
    prepared = stagekit.preprocess(data, cleaned, config=PreprocessConfig(max_side=128)).record

    for record in (raw, cleaned, prepared):
        _assert_reloads(data, record)
    assert [raw.stage, cleaned.stage, prepared.stage] == ["ingest", "clean", "preprocess"]
    assert prepared.parent_version_id == cleaned.version_id
    assert cleaned.parent_version_id == raw.version_id
    accepted = [r for r in iter_rows(data / prepared.manifest_relpath) if r["status"] == "accepted"]
    assert accepted and all(r["derived_sha256"] for r in accepted)
