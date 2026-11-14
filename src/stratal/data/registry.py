"""Dataset versions in the database: registration and lineage (DATA-3.3)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.core.errors import Conflict, NotFound
from stratal.data.commit import VersionRecord
from stratal.db.models import Dataset, DatasetVersion


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
    """
    existing = session.get(DatasetVersion, record.version_id)
    if existing is not None:
        return existing, False
    if record.parent_version_id is not None:
        get_version(session, record.parent_version_id)
    version = DatasetVersion(
        version_id=record.version_id,
        dataset_id=dataset.id,
        stage=record.stage,
        parent_version_id=record.parent_version_id,
        content_hash=record.content_hash,
        config_hash=record.config_hash,
        config=record.config,
        manifest_path=record.manifest_relpath,
        manifest_bytes=record.manifest_bytes,
        row_count=record.row_count,
        counts=record.counts,
        new_blob_bytes=new_blob_bytes,
        schema_version=record.schema_version,
        code_version=code_version,
        created_by_job_id=created_by_job_id,
    )
    session.add(version)
    session.flush()
    return version, True


def get_version(session: Session, version_id: str) -> DatasetVersion:
    version = session.get(DatasetVersion, version_id)
    if version is None:
        raise NotFound(f"No dataset version {version_id!r}.")
    return version


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
