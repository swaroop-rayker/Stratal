"""The stages a job can run, and how each is called inside a child process.

A stage is registered once with its queue, its config model and a runner. The
runner receives a :class:`StageRun` and returns a :class:`StageResult`. More
stages register themselves here as their milestones arrive.
"""

import dataclasses
import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import StratalError, ValidationFailed
from stratal.data import registry
from stratal.data.clean import OWNER_UNITS, CleanConfig, DedupeUnits, run_clean
from stratal.data.commit import VersionRecord
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.preprocess import PreprocessConfig, run_preprocess
from stratal.data.split_stage import OWNER_UNITS as SPLIT_UNITS
from stratal.data.split_stage import PreviousSplit, SplitConfig, SplitUnits, run_split
from stratal.data.versioning import derive_version_identity
from stratal.db.models import Dataset, EvalReport
from stratal.eval.report import OWNER_STATS, EvaluateConfig, ReportStats, assemble_phase1_report
from stratal.label import review as reviews
from stratal.label.filter_stage import FilterConfig, FilterRule, run_filter
from stratal.label.filtering import filter_decisions
from stratal.label.stage import OWNER_UNITS as LABEL_UNITS
from stratal.label.stage import LabelConfig, LabelModels, LabelUnits, run_label
from stratal.mon.events import MetricValue
from stratal.runtime.context import JobContext
from stratal.runtime.gpu_lease import gpu_lease
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

        return run_derived_stage(run, stage="clean", config_model=CleanConfig, body=body)

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


def _owner_unit_missing(stage: str, exc: NotImplementedError) -> StratalError:
    return StratalError(
        f"Stage {stage} needs an owner unit that is not written yet: {exc}",
        suggested_action='Stop the pipeline before it with "until_stage", or write the unit '
        "(ADR-0012; its tests are strict expected failures until then).",
    )


def real_label_models(data_dir: Path) -> LabelModels:
    """The real models, built in the GPU worker only: this is where PyTorch is imported."""
    from stratal.label import hf

    models_dir = data_dir / "models"
    return LabelModels(
        gpu=hf.TorchGpu,
        load_captioner=lambda c: hf.load_captioner(
            c.captioner,
            models_dir,
            dtype=c.dtype,
            task=c.caption_task,
            max_new_tokens=c.max_new_tokens,
            num_beams=c.num_beams,
        ),
        load_clip=lambda c: hf.load_clip(c.clip_model, models_dir, dtype=c.dtype),
        prepare=lambda c: hf.set_vram_cap(c.vram_fraction),
        library_versions=hf.library_versions(),
    )


def make_label_runner(
    models: Callable[[Path], LabelModels], units: LabelUnits
) -> Callable[[StageRun], StageResult]:
    """The label stage's runner. Tests register one built with fake models and units."""

    def _run_label(run: StageRun) -> StageResult:
        def body(given: DerivedInput) -> DerivedOutput:
            try:
                result = run_label(
                    parent_manifest=given.manifest,
                    parent_version_id=given.version_id,
                    data_dir=run.data_dir,
                    config=LabelConfig.model_validate(given.config),
                    derive=derive_version_identity,
                    sink=run.ctx,
                    cache=PgDerivationCache(run.engine, job_id=run.ctx.job_id),
                    models=models(run.data_dir),
                    units=units,
                    lease=lambda: gpu_lease(
                        run.engine, holder=f"{run.ctx.job_id}/label", wait_s=60
                    ),
                    checkpoint=run.ctx.raise_if_cancelled,
                )
            except NotImplementedError as exc:
                raise _owner_unit_missing("label", exc) from None
            return DerivedOutput(result.record, 0, result.metrics)

        return run_derived_stage(run, stage="label", config_model=LabelConfig, body=body)

    return _run_label


def make_filter_runner(rule: FilterRule) -> Callable[[StageRun], StageResult]:
    def _run_filter(run: StageRun) -> StageResult:
        def body(given: DerivedInput) -> DerivedOutput:
            try:
                result = run_filter(
                    parent_manifest=given.manifest,
                    parent_version_id=given.version_id,
                    data_dir=run.data_dir,
                    config=FilterConfig.model_validate(given.config),
                    derive=derive_version_identity,
                    sink=run.ctx,
                    rule=rule,
                )
            except NotImplementedError as exc:
                raise _owner_unit_missing("filter", exc) from None
            return DerivedOutput(result.record, 0, result.metrics)

        return run_derived_stage(run, stage="filter", config_model=FilterConfig, body=body)

    return _run_filter


register(StageDef("label", "gpu", LabelConfig, make_label_runner(real_label_models, LABEL_UNITS)))
register(StageDef("filter", "cpu", FilterConfig, make_filter_runner(filter_decisions)))


def make_split_runner(units: SplitUnits) -> Callable[[StageRun], StageResult]:
    """The split stage's runner (DATA-4). Tests register one built with fakes.

    Before the usual frame, it decides which earlier split this one continues
    (DATA-4.5) and writes that into the config, so the choice is part of the
    version's identity and of its memo key (DATA-4.4).
    """

    def _run_split(run: StageRun) -> StageResult:
        if run.input_version_id is None:
            return run_derived_stage(run, stage="split", config_model=SplitConfig, body=_never)
        asked = SplitConfig.model_validate(run.config)
        with Session(run.engine) as session:
            parent = registry.get_version(session, run.input_version_id)
            found = registry.find_previous_split(
                session,
                dataset_id=parent.dataset_id,
                parent_version_id=parent.version_id,
                config=asked.model_dump(mode="json"),
            )
            previous = None
            if found is not None:
                _, manifest = registry.manifest_for(session, run.data_dir, found.version_id)
                previous = PreviousSplit(found.version_id, manifest)
        continues = previous.version_id if previous is not None else None
        if asked.continues_version_id is not None and asked.continues_version_id != continues:
            raise ValidationFailed(
                f"continues_version_id is set by the split stage; it would be {continues!r}, "
                f"not {asked.continues_version_id!r}.",
                suggested_action="Leave continues_version_id out of the split config.",
            )
        config = {**asked.model_dump(mode="json"), "continues_version_id": continues}

        def body(given: DerivedInput) -> DerivedOutput:
            try:
                result = run_split(
                    parent_manifest=given.manifest,
                    parent_version_id=given.version_id,
                    data_dir=run.data_dir,
                    config=SplitConfig.model_validate(given.config),
                    derive=derive_version_identity,
                    sink=run.ctx,
                    previous=previous,
                    units=units,
                    checkpoint=run.ctx.raise_if_cancelled,
                )
            except NotImplementedError as exc:
                raise _owner_unit_missing("split", exc) from None
            return DerivedOutput(result.record, 0, result.metrics)

        return run_derived_stage(
            dataclasses.replace(run, config=config),
            stage="split",
            config_model=SplitConfig,
            body=body,
        )

    return _run_split


def _never(_given: DerivedInput) -> DerivedOutput:
    raise AssertionError("run_derived_stage refuses a stage without an input first")


register(StageDef("split", "cpu", SplitConfig, make_split_runner(SPLIT_UNITS)))


def make_evaluate_runner(stats: ReportStats) -> Callable[[StageRun], StageResult]:
    """The evaluate stage's runner (EVAL-1.1). Tests register one built with fakes.

    It writes no dataset version: it stores a report in ``eval_reports`` and
    passes its input version on as its output, so a pipeline's output is still
    the version the report describes.
    """

    def _run_evaluate(run: StageRun) -> StageResult:
        if run.input_version_id is None:
            raise StratalError(
                "Stage evaluate needs a version to report on and was given none.",
                suggested_action="Report on an existing version (POST /versions/{id}/reports).",
            )
        config = EvaluateConfig.model_validate(run.config)
        with Session(run.engine) as session:
            version, manifest = registry.manifest_for(session, run.data_dir, run.input_version_id)
            version_id, stage = version.version_id, version.stage
            review = reviews.review_for_report(session, version_id, config.review_id)
        with_review = f" with review {review.review_id}" if review is not None else ""
        run.ctx.emit(
            stage="evaluate",
            message=f"Building the phase-1 report of {version_id}{with_review}",
            metrics={"version_id": version_id},
        )
        try:
            report = assemble_phase1_report(
                manifest=manifest,
                version_id=version_id,
                stage=stage,
                config=config,
                review=review,
                stats=stats,
            )
        except NotImplementedError as exc:
            raise _owner_unit_missing("evaluate", exc) from None
        with Session(run.engine) as session, session.begin():
            row = EvalReport(
                version_id=version_id,
                kind=config.kind,
                report=report,
                review_session_id=review.review_id if review is not None else None,
                job_id=run.ctx.job_id,
            )
            session.add(row)
            session.flush()
            report_id = row.id
        metrics: dict[str, MetricValue] = {
            "report_id": report_id,
            "rows": int(report["rows"]["total"]),
            "accepted": int(report["rows"]["accepted"]),
        }
        if report["filter_rate"] is not None:
            metrics["filter_rate"] = round(float(report["filter_rate"]), 4)
        if review is not None:
            metrics["review_id"] = review.review_id
        run.ctx.emit(
            stage="evaluate", message=f"Stored report {report_id} for {version_id}", metrics=metrics
        )
        return StageResult(output_version_id=version_id, metrics=metrics)

    return _run_evaluate


register(StageDef("evaluate", "cpu", EvaluateConfig, make_evaluate_runner(OWNER_STATS)))
