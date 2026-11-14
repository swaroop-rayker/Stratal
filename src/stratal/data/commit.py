"""The commit step that ends every stage (ARCHITECTURE.md §3, DATA-3.1).

Write the manifest, derive the version's identity, move the manifest to its
final name. The database row is added afterwards by
:func:`stratal.data.registry.register_version`, so a version row can exist only
if its manifest exists (INV-02).
"""

import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stratal.data.versioning import VersionDeriver
from stratal.mon.sink import EventSink
from stratal.storage.manifest import SCHEMA_VERSION, iter_rows, write_manifest


@dataclass(frozen=True)
class VersionRecord:
    """Everything known about a committed version, before it is registered."""

    version_id: str
    stage: str
    parent_version_id: str | None
    content_hash: str
    config_hash: str
    config: dict[str, Any]
    manifest_relpath: str
    manifest_bytes: int
    row_count: int
    counts: dict[str, Any]
    schema_version: int
    reused_manifest: bool
    """True if an identical version already existed and its manifest was kept."""


def manifest_relpath(version_id: str) -> str:
    return f"manifests/{version_id}.parquet"


def commit_version(
    *,
    data_dir: Path,
    stage: str,
    parent_version_id: str | None,
    rows: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    derive: VersionDeriver,
    sink: EventSink,
) -> VersionRecord:
    """Persist ``rows`` as a new immutable version and return its record."""
    staging = data_dir / "tmp" / f"manifest-{uuid.uuid4().hex}.parquet"
    try:
        sink.emit(stage=stage, substage="staging", message="Writing manifest")
        stats = write_manifest(
            staging,
            rows,
            metadata={
                "stratal.stage": stage,
                "stratal.parent_version_id": parent_version_id or "",
            },
        )
        sink.emit(stage=stage, substage="staging", message="Computing version hash")
        identity = derive(iter_rows(staging), config)
        relpath = manifest_relpath(identity.version_id)
        final = data_dir / relpath
        reused = final.exists()
        if not reused:
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            final.chmod(0o444)
    finally:
        staging.unlink(missing_ok=True)

    sink.emit(
        stage=stage,
        substage="staging",
        message=f"Recording lineage → {identity.version_id}",
        metrics={
            "rows": stats.row_count,
            "version_id": identity.version_id,
            "bytes_written": 0 if reused else stats.bytes_written,
        },
    )
    return VersionRecord(
        version_id=identity.version_id,
        stage=stage,
        parent_version_id=parent_version_id,
        content_hash=identity.content_hash,
        config_hash=identity.config_hash,
        config=dict(config),
        manifest_relpath=relpath,
        manifest_bytes=final.stat().st_size,
        row_count=stats.row_count,
        counts=stats.as_counts(),
        schema_version=SCHEMA_VERSION,
        reused_manifest=reused,
    )
