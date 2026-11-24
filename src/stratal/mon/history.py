"""Trends across past runs of one stage (MON-4.5; ADR-0020 rule 12; SCR-13's History tab).

Pure functions over what a completed stage stored: its config, final metrics, MON-2.5
peaks, moving-average throughput and timing. The API reads the rows and calls these, so
the rules below are tested without a database.

- **Rate** counts work done, not work reused, in the stage's own unit: training samples a
  second (its moving average of steps a second while training, times the batch); captions
  computed per second of running for ``label``; ``ingest`` files and ``clean`` and
  ``preprocess`` images a second from their own metrics. Other stages have no rate.
- **Time** is running time across attempts, pauses and queueing removed (as MON-4.4
  aligns runs); for training it is per epoch, since runs have different epoch counts.
- **Groups** put like with like: training runs by model structure (labels left out, so a
  renamed spec stays in its group), dataset and batch; ``label`` by captioner and batch;
  every other stage is one group.
"""

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from stratal.build.spec import GraphSpec, structure_hash

TRAINING_STAGES = ("train",)
RATE_UNITS = {
    "train": "samples/s",
    "label": "captions/s",
    "ingest": "files/s",
    "clean": "images/s",
    "preprocess": "images/s",
}
_RATE_METRIC = {"ingest": "files_per_s", "clean": "images_per_s", "preprocess": "images_per_s"}
NOTES = ("paused", "resumed", "fine_tune", "pruned", "reused_work", "peaks_missing")
"""Codes a run can carry; the screen words them ("paused once", "worker lost, resumed")."""


@dataclass(frozen=True)
class StageRun:
    """One completed run of a stage, as stored."""

    job_id: str
    kind: str
    stage: str
    queue: str
    started_at: datetime | None
    config: Mapping[str, Any] = field(default_factory=dict[str, Any])
    metrics: Mapping[str, Any] = field(default_factory=dict[str, Any])
    peaks: Mapping[str, Any] = field(default_factory=dict[str, Any])
    throughput: float | None = None
    """The stage's moving average of steps (items) a second (MON-1.5)."""
    cache_hit: bool = False
    stage_seconds: float | None = None
    """``finished_at - started_at`` of the stage's last attempt."""
    running_s: float | None = None
    """The job's time running across attempts, for a job with this one stage."""
    paused: bool = False
    worker_lost: bool = False
    dataset_slug: str | None = None
    lineage_name: str | None = None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def seconds(run: StageRun) -> float | None:
    """Running time across attempts where it is known, else the last attempt's."""
    return run.running_s if run.running_s is not None else run.stage_seconds


def epochs_run(run: StageRun) -> int | None:
    """Epochs a training run went through: to its pruning step, else its config."""
    pruned = number(run.metrics.get("pruned_at_step"))
    if pruned is not None and pruned > 0:
        return int(pruned)
    epochs = number(run.config.get("epochs"))
    return int(epochs) if epochs else None


def rate(run: StageRun) -> float | None:
    """The stage's rate in ``RATE_UNITS[stage]``; ``None`` when it has none or did no work."""
    if run.cache_hit:
        return None
    if run.stage in TRAINING_STAGES:
        batch = number(run.config.get("batch_size"))
        if run.throughput is None or not batch:
            return None
        return round(run.throughput * batch, 1)
    if run.stage == "label":
        computed = number(run.metrics.get("captions_computed"))
        secs = seconds(run)
        if not computed or not secs:
            return None
        return round(computed / secs, 2)
    metric = _RATE_METRIC.get(run.stage)
    return number(run.metrics.get(metric)) if metric else None


def time_value(run: StageRun) -> tuple[float | None, str]:
    """``(value, unit)``: seconds per epoch for training, else seconds."""
    secs = seconds(run)
    if run.stage in TRAINING_STAGES:
        epochs = epochs_run(run)
        if secs is None or not epochs:
            return None, "s/epoch"
        return round(secs / epochs, 2), "s/epoch"
    return (round(secs, 1) if secs is not None else None), "s"


def notes(run: StageRun) -> list[str]:
    """What makes a run unusual, as codes from ``NOTES``, in that order."""
    out: list[str] = []
    if run.paused:
        out.append("paused")
    if run.worker_lost:
        out.append("resumed")
    if run.config.get("init_from"):
        out.append("fine_tune")
    if run.metrics.get("pruned_at_step"):
        out.append("pruned")
    if run.stage == "label" and not run.cache_hit and rate(run) is None:
        out.append("reused_work")
    if run.peaks.get("vram_used_mb") is None and run.peaks.get("ram_used_mb") is None:
        out.append("peaks_missing")
    return out


def _hash(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def group(run: StageRun) -> tuple[str, str]:
    """``(key, label)`` of the run's group."""
    if run.stage in TRAINING_STAGES:
        raw: Any = run.config.get("model")
        model: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}  # pyright: ignore[reportUnknownArgumentType]
        name = str(model.get("name") or "model")
        found: Any = model.get("graph")
        graph: dict[str, Any] | None = dict(found) if isinstance(found, Mapping) else None  # pyright: ignore[reportUnknownArgumentType]
        structure = f"ref:{name}"
        if graph is not None:
            try:
                structure = structure_hash(GraphSpec.model_validate(graph))
            except ValidationError:
                structure = f"graph:{_hash(graph)}"
        batch = run.config.get("batch_size")
        slug = run.dataset_slug or "?"
        if graph is not None:
            base = run.lineage_name or f"{graph.get('label') or name} · {slug}"
        else:
            base = f"{name} (reference) · {slug}"
        return _hash(run.stage, structure, slug, batch), f"{base} · batch {batch}"
    if run.stage == "label":
        captioner = run.config.get("captioner") or "captioner"
        batch = run.config.get("caption_batch_size")
        return _hash(run.stage, captioner, batch), f"{captioner} · batch {batch}"
    return _hash(run.stage), "all runs"


def median(values: Sequence[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(statistics.median(kept), 2) if kept else None
