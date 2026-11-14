"""Table models. The authoritative description is docs/DATA_MODEL.md §4."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from stratal.db.base import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        Index("ix_dataset_versions_dataset_created", "dataset_id", "created_at"),
        Index("ix_dataset_versions_memo", "stage", "parent_version_id", "config_hash"),
        Index("ix_dataset_versions_parent", "parent_version_id"),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("datasets.id"))
    stage: Mapped[str] = mapped_column(Text)
    parent_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dataset_versions.version_id")
    )
    content_hash: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dtype: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str] = mapped_column(Text)
    manifest_bytes: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_blob_bytes: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer)
    code_version: Mapped[str | None] = mapped_column(Text)
    # Becomes a foreign key to jobs.id when the jobs table arrives in M2.
    created_by_job_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
