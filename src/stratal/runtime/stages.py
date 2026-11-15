"""The stages a job can run, and how each is called inside a child process.

A stage is registered once with its queue, its config model and a runner. The
runner receives a :class:`StageRun` and returns a :class:`StageResult`. More
stages register themselves here as their milestones arrive.
"""

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import StratalError
from stratal.data import registry
from stratal.data.clean import OWNER_UNITS, CleanConfig, DedupeUnits, run_clean
from stratal.data.commit import VersionRecord
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.preprocess import PreprocessConfig, run_preprocess
from stratal.data.versioning import derive_version_identity
from stratal.db.models import Dataset
from stratal.mon.events import MetricValue
from stratal.runtime.context import JobContext
from stratal.storage.derivations import PgDerivationCache

Queue = Literal["cpu", "gpu"]

# Order of the phase-1 pipeline (ARCHITECTURE.md §3). Only registered stages run.
PIPELINE_ORDER = ("ingest", "clean", "preprocess", "label", "filter", "split", "evaluate")


@dataclass(frozen=True)
class StageRun:
    ctx: JobContext
    spec: dict[str, Any]
    """The job's resolved request."""
    config: dict[str, Any]
    input_version_id: str | None
    data_dir: Path
    engine: Engine


@dataclass(frozen=True)
class StageResult:
    output_version_id: str | None = None
    cache_hit: bool = False
    metrics: dict[str, MetricValue] = field(default_factory=lambda: dict[str, MetricValue]())


@dataclass(frozen=True)
class StageDef:
    name: str
    queue: Queue
    config_model: type[BaseModel]
    run: Callable[[StageRun], StageResult]


_REGISTRY: dict[str, StageDef] = {}


def register(stage: StageDef) -> None:
    _REGISTRY[stage.name] = stage


def load_extra_stages() -> None:
    """Import the modules named in ``STRATAL_EXTRA_STAGES`` so they can register stages.

    Used by the test suite, whose synthetic stages must also exist in the child
    process.
    """
    for module in filter(None, os.environ.get("STRATAL_EXTRA_STAGES", "").split(",")):
        importlib.import_module(module.strip())


def get_stage(name: str) -> StageDef | None:
    load_extra_stages()
    return _REGISTRY.get(name)


def registered() -> dict[str, StageDef]:
    load_extra_stages()
    return dict(_REGISTRY)


def _run_ingest(run: StageRun) -> StageResult:
    source: dict[str, Any] = run.spec.get("source") or {}
    result = run_ingest(
        source=Path(str(source.get("path", ""))),
        data_dir=run.data_dir,
        config=IngestConfig.model_validate(run.config),
        derive=derive_version_identity,
        sink=run.ctx,
    )
    record = result.record
    run.ctx.set_version(record.version_id)
    with Session(run.engine) as session, session.begin():
        dataset = registry.ensure_dataset(
            session,
            slug=str(run.spec["dataset"]),
            modality="image",
            source_uri=str(source.get("path", "")),
        )
        registry.register_version(
            session,
            dataset=dataset,
            record=record,
            new_blob_bytes=result.new_blob_bytes,
            created_by_job_id=run.ctx.job_id,
        )
    counts = record.counts
    metrics: dict[str, MetricValue] = {
        "files": record.row_count,
        "files_per_s": round(record.row_count / max(result.seconds, 1e-9), 1),
        "accepted": int(counts.get("accepted", 0)),
        "rejected": int(counts.get("rejected", 0)),
        "new_blob_bytes": result.new_blob_bytes,
    }
    reasons: Any = counts.get("by_reason") or {}
    if isinstance(reasons, dict):
        for reason, count in reasons.items():  # pyright: ignore[reportUnknownVariableType]
            metrics[f"rejected_{reason}"] = int(count)  # pyright: ignore[reportUnknownArgumentType]
    return StageResult(output_version_id=record.version_id, metrics=metrics)


register(StageDef("ingest", "cpu", IngestConfig, _run_ingest))


@dataclass(frozen=True)
class DerivedInput:
    """What a stage that consumes a version is given by :func:`run_derived_stage`."""

    version_id: str
    manifest: Path
    config: dict[str, Any]
    """The stage's resolved config, as recorded in the version it makes."""


@dataclass(frozen=True)
class DerivedOutput:
    record: VersionRecord
    new_blob_bytes: int
    metrics: dict[str, MetricValue]


def run_derived_stage(
    run: StageRun,
    *,
    stage: str,
    config_model: type[BaseModel],
    body: Callable[[DerivedInput], DerivedOutput],
) -> StageResult:
    """The frame of every stage that turns one version into the next.

    Finds the input by version ID (BUG1), reuses an existing version made from
    the same input with the same config (stage memoization, ARCHITECTURE.md §3),
    otherwise runs ``body`` and registers its version under the parent's dataset.
    """
    if run.input_version_id is None:
        raise StratalError(
            f"Stage {stage} needs an input version and was given none.",
            suggested_action="Start the pipeline from a source folder or from an existing "
            "version (from_version).",
        )
    resolved: dict[str, Any] = config_model.model_validate(run.config).model_dump(mode="json")
    with Session(run.engine) as session:
        parent, manifest = registry.manifest_for(session, run.data_dir, run.input_version_id)
        dataset_id = parent.dataset_id
        memo = registry.find_memoized(
            session,
            run.data_dir,
            stage=stage,
            parent_version_id=parent.version_id,
            config=resolved,
            derive=derive_version_identity,
        )
        reused = (memo.version_id, dict(memo.counts)) if memo is not None else None
    if reused is not None:
        version_id, counts = reused
        run.ctx.set_version(version_id)
        run.ctx.emit(
            stage=stage,
            message=f"Reusing {version_id}: made earlier from {parent.version_id} "
            "with the same config",
            metrics={"version_id": version_id},
        )
        return StageResult(
            output_version_id=version_id,
            cache_hit=True,
            metrics={"accepted": int(counts.get("accepted", 0)), "reused": True},
        )

    output = body(DerivedInput(parent.version_id, manifest, resolved))
    record = output.record
    run.ctx.set_version(record.version_id)
    with Session(run.engine) as session, session.begin():
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise StratalError(f"Dataset {dataset_id} of {parent.version_id} has disappeared.")
        registry.register_version(
            session,
            dataset=dataset,
            record=record,
            new_blob_bytes=output.new_blob_bytes,
            created_by_job_id=run.ctx.job_id,
        )
    return StageResult(output_version_id=record.version_id, metrics=output.metrics)


def make_clean_runner(units: DedupeUnits) -> Callable[[StageRun], StageResult]:
    """The clean stage's runner, calling ``units``. Tests register one built with fakes."""

    def _run_clean(run: StageRun) -> StageResult:
        def body(given: DerivedInput) -> DerivedOutput:
            result = run_clean(
                parent_manifest=given.manifest,
                parent_version_id=given.version_id,
                data_dir=run.data_dir,
                config=CleanConfig.model_validate(given.config),
                derive=derive_version_identity,
                sink=run.ctx,
                cache=PgDerivationCache(run.engine, job_id=run.ctx.job_id),
                units=units,
                checkpoint=run.ctx.raise_if_cancelled,
            )
            return DerivedOutput(result.record, 0, result.metrics)

        try:
            return run_derived_stage(run, stage="clean", config_model=CleanConfig, body=body)
        except NotImplementedError as exc:
            raise StratalError(
                f"{exc}",
                suggested_action="The clean stage calls owner-implemented code that is not "
                "written yet (DATA-2.1, DATA-2.2). Until it is, run the pipeline with "
                'until_stage = "ingest".',
            ) from exc

    return _run_clean


def _run_preprocess(run: StageRun) -> StageResult:
    def body(given: DerivedInput) -> DerivedOutput:
        result = run_preprocess(
            parent_manifest=given.manifest,
            parent_version_id=given.version_id,
            data_dir=run.data_dir,
            config=PreprocessConfig.model_validate(given.config),
            derive=derive_version_identity,
            sink=run.ctx,
            cache=PgDerivationCache(run.engine, job_id=run.ctx.job_id),
            checkpoint=run.ctx.raise_if_cancelled,
        )
        return DerivedOutput(result.record, result.new_blob_bytes, result.metrics)

    return run_derived_stage(run, stage="preprocess", config_model=PreprocessConfig, body=body)


register(StageDef("clean", "cpu", CleanConfig, make_clean_runner(OWNER_UNITS)))
register(StageDef("preprocess", "cpu", PreprocessConfig, _run_preprocess))
