"""Run the data stages as library calls on a temporary data folder, for unit tests.

No database and no job: the real version identity (DATA-3.2), an in-memory
derivation cache and a list sink. Integration tests run the same stages as jobs.
"""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from stratal.data.clean import CleanConfig, CleanResult, DedupeUnits, run_clean
from stratal.data.commit import VersionRecord
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.preprocess import PreprocessConfig, PreprocessResult, run_preprocess
from stratal.data.versioning import derive_version_identity
from stratal.label.filter_stage import FilterConfig, FilterResult, FilterRule, run_filter
from stratal.label.stage import (
    LabelConfig,
    LabelModels,
    LabelResult,
    LabelUnits,
    LeaseFactory,
    run_label,
)
from stratal.storage.derivations import DerivationCache, MemoryDerivationCache
from stratal.storage.manifest import Row, iter_rows
from stratal.testing.fakes import ListSink


def manifest_path(data_dir: Path, record: VersionRecord) -> Path:
    return data_dir / record.manifest_relpath


def rows_of(data_dir: Path, record: VersionRecord) -> dict[str, Row]:
    """A version's rows by row key."""
    return {row["row_key"]: row for row in iter_rows(manifest_path(data_dir, record))}


def ingest(source: Path, data_dir: Path, *, min_size: int = 32) -> VersionRecord:
    result = run_ingest(
        source=source,
        data_dir=data_dir,
        config=IngestConfig(min_size=min_size),
        derive=derive_version_identity,
        sink=ListSink(),
    )
    return result.record


def clean(
    data_dir: Path,
    parent: VersionRecord,
    units: DedupeUnits,
    *,
    config: CleanConfig | None = None,
    cache: DerivationCache | None = None,
    sink: ListSink | None = None,
) -> CleanResult:
    return run_clean(
        parent_manifest=manifest_path(data_dir, parent),
        parent_version_id=parent.version_id,
        data_dir=data_dir,
        config=config or CleanConfig(),
        derive=derive_version_identity,
        sink=sink or ListSink(),
        cache=cache if cache is not None else MemoryDerivationCache(),
        units=units,
    )


def preprocess(
    data_dir: Path,
    parent: VersionRecord,
    *,
    config: PreprocessConfig | None = None,
    cache: DerivationCache | None = None,
    sink: ListSink | None = None,
) -> PreprocessResult:
    return run_preprocess(
        parent_manifest=manifest_path(data_dir, parent),
        parent_version_id=parent.version_id,
        data_dir=data_dir,
        config=config or PreprocessConfig(),
        derive=derive_version_identity,
        sink=sink or ListSink(),
        cache=cache if cache is not None else MemoryDerivationCache(),
    )


def label(
    data_dir: Path,
    parent: VersionRecord,
    *,
    models: LabelModels,
    units: LabelUnits,
    config: LabelConfig | None = None,
    cache: DerivationCache | None = None,
    sink: ListSink | None = None,
    lease: LeaseFactory = nullcontext,
    checkpoint: Callable[[], None] = lambda: None,
) -> LabelResult:
    return run_label(
        parent_manifest=manifest_path(data_dir, parent),
        parent_version_id=parent.version_id,
        data_dir=data_dir,
        config=config or LabelConfig(),
        derive=derive_version_identity,
        sink=sink or ListSink(),
        cache=cache if cache is not None else MemoryDerivationCache(),
        models=models,
        units=units,
        lease=lease,
        checkpoint=checkpoint,
    )


def filter_stage(
    data_dir: Path,
    parent: VersionRecord,
    *,
    rule: FilterRule,
    config: FilterConfig | None = None,
    sink: ListSink | None = None,
) -> FilterResult:
    return run_filter(
        parent_manifest=manifest_path(data_dir, parent),
        parent_version_id=parent.version_id,
        data_dir=data_dir,
        config=config or FilterConfig(),
        derive=derive_version_identity,
        sink=sink or ListSink(),
        rule=rule,
    )
