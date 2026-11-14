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

from stratal.data import registry
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.versioning import derive_version_identity
from stratal.mon.events import MetricValue
from stratal.runtime.context import JobContext

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
