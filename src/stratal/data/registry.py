"""Dataset versions in the database: registration, lineage and loading (DATA-3.3, BUG1)."""

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict, NotFound, StratalError
from stratal.data.commit import VersionRecord
from stratal.data.versioning import VersionDeriver
from stratal.db.models import Dataset, DatasetVersion
from stratal.storage.manifest import Row, iter_rows, read_metadata


def ensure_dataset(
    session: Session, *, slug: str, modality: str, source_uri: str | None = None
) -> Dataset:
    """Return the dataset with this slug, creating it if needed."""
    dataset = session.scalar(select(Dataset).where(Dataset.slug == slug))
    if dataset is None:
        dataset = Dataset(slug=slug, modality=modality, source_uri=source_uri)
        session.add(dataset)
        session.flush()
    elif dataset.modality != modality:
        raise Conflict(
            f"Dataset {slug!r} already exists with modality {dataset.modality!r}.",
            suggested_action="Use a different dataset name.",
        )
    return dataset


def register_version(
    session: Session,
    *,
    dataset: Dataset,
    record: VersionRecord,
    new_blob_bytes: int,
    code_version: str | None = None,
    created_by_job_id: str | None = None,
) -> tuple[DatasetVersion, bool]:
    """Record a committed version. Returns the row and whether it was new.

    Registering the same version twice is not an error: the same content and
    config always give the same ID (DATA-3.2), so the existing row is returned.
    That holds when two jobs register it at the same moment too: the insert
    skips a row that already exists (or that another transaction is inserting,
    in which case it waits for that one), and the row is then read back. A row
    under the same ID with a different content hash is a real collision and
    is refused (BUG1).
    """
    existing = session.get(DatasetVersion, record.version_id)
    if existing is not None:
        return _same_version(existing, record), False
    if record.parent_version_id is not None:
        get_version(session, record.parent_version_id)
    values = dict(
        version_id=record.version_id,
        dataset_id=dataset.id,
        stage=record.stage,
        parent_version_id=record.parent_version_id,
        content_hash=record.content_hash,
        config_hash=record.config_hash,
        config=record.config,
        manifest_path=record.manifest_relpath,
        dtype=record.dtype,
        manifest_bytes=record.manifest_bytes,
        row_count=record.row_count,
        counts=record.counts,
        new_blob_bytes=new_blob_bytes,
        schema_version=record.schema_version,
        code_version=code_version,
        created_by_job_id=created_by_job_id,
    )
    inserted = session.execute(
        pg_insert(DatasetVersion)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[DatasetVersion.version_id])
        .returning(DatasetVersion.version_id)
    ).scalar_one_or_none()
    version = session.get(DatasetVersion, record.version_id, populate_existing=True)
    if version is None:  # pragma: no cover - the row was inserted or already committed
        raise Conflict(f"Version {record.version_id} could not be registered.")
    if inserted is None:
        return _same_version(version, record), False
    return version, True


def _same_version(existing: DatasetVersion, record: VersionRecord) -> DatasetVersion:
    """``existing`` if it is the version ``record`` describes; a collision otherwise."""
    if existing.content_hash != record.content_hash or existing.config_hash != record.config_hash:
        raise Conflict(
            f"Version {record.version_id} is already registered with different content "
            f"(content {existing.content_hash[:12]} against {record.content_hash[:12]}, "
            f"config {existing.config_hash[:12]} against {record.config_hash[:12]}).",
            suggested_action="This should not happen: report it with both jobs' IDs.",
        )
    return existing


def get_version(session: Session, version_id: str) -> DatasetVersion:
    version = session.get(DatasetVersion, version_id)
    if version is None:
        raise NotFound(f"No dataset version {version_id!r}.")
    return version


class VersionDataMissing(StratalError):
    """A version is registered but its manifest is missing or is not the one registered."""


def manifest_for(session: Session, data_dir: Path, version_id: str) -> tuple[DatasetVersion, Path]:
    """The version and its manifest file, checked against what was registered (BUG1).

    The one way a stage finds its input: by version ID, never by a path it was
    handed. Raises :class:`NotFound` for an unknown ID and
    :class:`VersionDataMissing` when the file is gone or its metadata disagrees.
    """
    version = get_version(session, version_id)
    path = data_dir / version.manifest_path
    if not path.is_file():
        raise VersionDataMissing(
            f"Version {version_id} is registered but its manifest {version.manifest_path} "
            "is missing from the data volume.",
            suggested_action="Check that the stratal-data volume is the one this database "
            "was used with, then rerun the stage that made the version.",
        )
    metadata = read_metadata(path)
    expected = {
        "stratal.stage": version.stage,
        "stratal.parent_version_id": version.parent_version_id or "",
    }
    wrong = {k: metadata.get(k) for k, v in expected.items() if metadata.get(k) != v}
    if wrong:
        raise VersionDataMissing(
            f"The manifest of {version_id} does not match its registration: {wrong}.",
            suggested_action="Rerun the stage that made the version.",
        )
    return version, path


def load_version(
    session: Session, data_dir: Path, version_id: str, *, columns: list[str] | None = None
) -> Iterator[Row]:
    """Stream a version's rows by its ID (API.md §5 ``load_version``)."""
    _, path = manifest_for(session, data_dir, version_id)
    return iter_rows(path, columns=columns)


def find_memoized(
    session: Session,
    data_dir: Path,
    *,
    stage: str,
    parent_version_id: str,
    config: Mapping[str, Any],
    derive: VersionDeriver,
) -> DatasetVersion | None:
    """An existing version made by ``stage`` from this parent with this config, if any.

    The memo lookup of ARCHITECTURE.md §3. The config hash comes from the same
    derivation that named the version; it depends only on the config
    (DATA_MODEL.md §3, rule 4), so it is derived here from no rows. A version
    whose manifest is missing is not reused.
    """
    config_hash = derive(iter(()), config).config_hash
    query = (
        select(DatasetVersion)
        .where(
            DatasetVersion.stage == stage,
            DatasetVersion.parent_version_id == parent_version_id,
            DatasetVersion.config_hash == config_hash,
        )
        .order_by(DatasetVersion.created_at)
    )
    for version in session.scalars(query):
        if (data_dir / version.manifest_path).is_file():
            return version
    return None


SPLIT_IDENTITY_KEYS = ("train", "val", "test", "seed")
"""The config fields that decide whether one split continues another (DATA-4.5)."""


def find_previous_split(
    session: Session, *, dataset_id: int, parent_version_id: str, config: Mapping[str, Any]
) -> DatasetVersion | None:
    """The split the next split of ``parent_version_id`` continues (DATA-4.5), if any.

    The dataset's most recent ``split`` version made with the same proportions
    and seed, leaving out splits of this same parent. Leaving those out keeps a
    rerun reproducible (DATA-4.4.AC1): it continues the same earlier split as
    the first run did, gets the same config, and so the same version.
    """
    query = (
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.stage == "split",
            DatasetVersion.parent_version_id != parent_version_id,
        )
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.version_id)
    )
    for version in session.scalars(query):
        if all(version.config.get(key) == config.get(key) for key in SPLIT_IDENTITY_KEYS):
            return version
    return None


def lineage(session: Session, version_id: str) -> list[DatasetVersion]:
    """The chain from ``version_id`` back to its raw version, nearest first."""
    chain: list[DatasetVersion] = []
    seen: set[str] = set()
    current: str | None = version_id
    while current is not None:
        if current in seen:
            raise Conflict(f"Lineage of {version_id!r} loops at {current!r}.")
        seen.add(current)
        version = get_version(session, current)
        chain.append(version)
        current = version.parent_version_id
    return chain


def list_versions(session: Session, slug: str) -> list[DatasetVersion]:
    dataset = session.scalar(select(Dataset).where(Dataset.slug == slug))
    if dataset is None:
        raise NotFound(f"No dataset {slug!r}.")
    query = (
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset.id)
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.version_id)
    )
    return list(session.scalars(query))
