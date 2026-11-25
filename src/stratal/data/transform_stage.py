"""The ``transform`` stage: a table's raw version through a list of operations (DATA-7.1).

ADR-0019 Addendum B rules 25, 27, 28 and 31. The input is the ``ingest``
version of a ``tabular`` dataset; a transform of anything else is refused (K2).
The rows stream from the parent's manifest through :class:`RowStep` into the new
manifest, so the table never has to fit in memory. The output's schema is the
plan's, stored as ``stratal.table_schema``, so it is part of the version's
identity (rule 28). The raw manifest is only read (DATA-7.1.AC1).
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from stratal.core.errors import ValidationFailed
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.dataset_types import TableSchema
from stratal.data.records import META_TABLE_SCHEMA, schema_to_json, table_schema_of
from stratal.data.transform import STAGE, Plan, RowStep, TransformConfig, plan
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.manifest import Row, iter_rows, read_metadata


@dataclass(frozen=True)
class TransformResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]
    seconds: float


def input_schema(manifest: Path, *, version_id: str) -> TableSchema:
    """The schema of a version a transform may start from: a tabular ``ingest`` version (K2)."""
    schema = table_schema_of(manifest)
    stage = read_metadata(manifest).get("stratal.stage")
    if schema is None:
        raise ValidationFailed(
            f"{version_id} is not a table: transforms apply to a tabular dataset's raw version.",
            suggested_action="Pick the ingest version of a tabular dataset.",
        )
    if stage != "ingest":
        raise ValidationFailed(
            f"{version_id} is a {stage} version; a transform applies to a table's raw (ingest) "
            "version only, so lineage stays raw → transform → clean → split (ADR-0019 rule 25).",
            suggested_action="Edit the operations and run them again on the raw version.",
        )
    return schema


def _rows(manifest: Path, step: RowStep, checkpoint: Callable[[], None]) -> Iterator[Row]:
    for n, row in enumerate(iter_rows(manifest), start=1):
        if n % 65_536 == 0:
            checkpoint()
        yield step(row)


def run_transform(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: TransformConfig,
    derive: VersionDeriver,
    sink: EventSink,
    checkpoint: Callable[[], None] = lambda: None,
) -> TransformResult:
    """Apply ``config``'s operations to the raw version and commit a ``transform`` version."""
    started = time.monotonic()
    schema = input_schema(parent_manifest, version_id=parent_version_id)
    checked: Plan = plan(schema, config.operations)
    sink.emit(
        stage=STAGE,
        substage="plan",
        message=f"{len(checked.steps)} operations checked against {len(schema.columns)} columns",
    )
    step = RowStep(checked)
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_rows(parent_manifest, step, checkpoint),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
        metadata={META_TABLE_SCHEMA: schema_to_json(checked.output)},
    )
    counts = step.counts
    cast_failed = sum(s.cast_failed for s in counts.steps)
    filtered = sum(s.filtered for s in counts.steps)
    filtered_null = sum(s.filtered_null for s in counts.steps)
    for s, c in zip(checked.steps, counts.steps, strict=True):
        done = s.summary
        if s.op == "filter":
            done += (
                f" · {c.filtered:,} rows filtered, {c.filtered_null:,} where it cannot be computed"
            )
        if c.cast_failed:
            sink.emit(
                stage=STAGE,
                substage=f"op{s.index + 1}",
                level="WARN",
                message=f"Operation {s.index + 1}: {done} · {c.cast_failed:,} rows cast_failed, "
                f"e.g. {', '.join(c.examples)}",
                reason="DATA-7.2: a value that will not convert to the new kind is rejected, "
                "kept in the manifest and counted.",
                suggested_action="Nothing to do if expected. Otherwise fix the source, or set the "
                "column's kind at ingest (stages.ingest.table.kinds).",
                metrics={f"op{s.index + 1}_cast_failed": c.cast_failed},
            )
        else:
            sink.emit(
                stage=STAGE, substage=f"op{s.index + 1}", message=f"Operation {s.index + 1}: {done}"
            )
    if counts.readmitted:
        sink.emit(
            stage=STAGE,
            substage="target",
            message=f"{counts.readmitted:,} rows rejected only for the old target's empty value "
            "were taken back (ADR-0019 rule 27)",
            metrics={"readmitted": counts.readmitted},
        )
    metrics: dict[str, MetricValue] = {
        "rows": counts.rows,
        "operations": len(checked.steps),
        "columns": len(checked.output.columns),
        "cast_failed": cast_failed,
        "filtered": filtered,
        "transform_null": filtered_null,
        "readmitted": counts.readmitted,
        "missing_target": counts.missing_target,
        "accepted": int(record.counts.get("accepted", 0)),
    }
    return TransformResult(record, metrics, round(time.monotonic() - started, 3))
