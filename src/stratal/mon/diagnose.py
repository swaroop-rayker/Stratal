"""Bottleneck diagnostics: expected against observed resource per running stage
(MON-3.2; ADR-0020 part B).

The expected resource comes from SPEC MON-3's table. For a stage on the GPU queue
whose GPU has been under 30 % over the last 30 s, :func:`diagnose` names the likely
bottleneck with three first rules (K5):

1. the job's own CPU saturated: data loading;
2. else disk reads at or above the threshold: the disk;
3. else everything low: small batches or waiting on synchronisation.

"CPU" here is the job's process tree in cores against what it can use, ``1 +
loader_workers`` capped by the logical CPUs (OC-12), never the machine's share. The
functions are pure; :func:`read_window` gathers their input from the samples, and the
watchdog (events) and the API (SCR-13's *Now*) both call them.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from stratal.db.models import Event as EventRow
from stratal.db.models import JobTransition, ProcessSample, ResourceSample

Resource = Literal["gpu", "cpu", "disk", "ram"]
WINDOW_S = 30.0
MIN_SAMPLES = 20
WARMUP_S = 60.0
"""A stage's first minute after it starts or resumes is loading and warm-up, not a bottleneck."""
GPU_LOW = 30.0
SATURATED = 0.85
"""The job's CPU counts as saturated at this share of its capacity."""

_EXPECTED: dict[str, tuple[Resource, ...]] = {
    "ingest": ("disk", "cpu"),
    "clean": ("cpu", "ram"),
    "clean_table": ("cpu", "ram"),
    "clean_text": ("cpu", "ram"),
    "preprocess": ("cpu", "ram"),
    "tokenize": ("cpu", "ram"),
    "split": ("cpu",),
    "filter": ("cpu",),
    "evaluate": ("cpu",),
    "label": ("gpu",),
    "train_classical": ("cpu",),
    "retention": ("disk",),
}
_GPU_WHEN_QUEUED = {"train", "test_eval", "study"}


def expected(stage: str, queue: str | None) -> tuple[Resource, ...]:
    """MON-3's main resource for a stage. Training and evaluation are GPU-bound on the GPU
    queue and CPU-bound on the CPU queue (MON-3: "or CPU for tiny models")."""
    if stage in _GPU_WHEN_QUEUED:
        return ("gpu",) if queue == "gpu" else ("cpu",)
    return _EXPECTED.get(stage, ("gpu",) if queue == "gpu" else ("cpu",))


@dataclass(frozen=True)
class Observed:
    samples: int
    gpu_percent: float | None
    cpu_cores: float | None
    capacity_cores: float
    loader_workers: int
    disk_read_mb_s: float


@dataclass(frozen=True)
class Diagnosis:
    code: Literal["diag_data_loading", "diag_disk", "diag_gpu_waiting"]
    message: str
    reason: str
    action: str


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def observe(
    *,
    gpu: list[float | None],
    disk_read: list[float],
    cpu_cores: list[float | None],
    loader_workers: int,
    logical_cpus: int,
) -> Observed:
    """Means over the window. A figure with no readings is ``None``."""
    return Observed(
        samples=len(disk_read),
        gpu_percent=_mean([g for g in gpu if g is not None]),
        cpu_cores=_mean([c for c in cpu_cores if c is not None]),
        capacity_cores=float(min(1 + max(loader_workers, 0), max(logical_cpus, 1))),
        loader_workers=loader_workers,
        disk_read_mb_s=_mean(disk_read) or 0.0,
    )


def diagnose(
    seen: Observed,
    *,
    queue: str | None,
    running_for_s: float,
    disk_threshold_mb_s: float,
) -> Diagnosis | None:
    """The bottleneck of a GPU stage whose GPU is under 30 %, or ``None``."""
    if queue != "gpu" or running_for_s < WARMUP_S or seen.samples < MIN_SAMPLES:
        return None
    if seen.gpu_percent is None or seen.gpu_percent >= GPU_LOW:
        return None
    gpu = f"GPU at {seen.gpu_percent:.0f}%"
    cores = (
        "unknown"
        if seen.cpu_cores is None
        else f"{seen.cpu_cores:.2f} of {seen.capacity_cores:.0f} "
        f"core{'s' if seen.capacity_cores != 1 else ''}"
    )
    if seen.cpu_cores is not None and seen.cpu_cores >= SATURATED * seen.capacity_cores:
        return Diagnosis(
            "diag_data_loading",
            f"Data loading is the bottleneck: {gpu} while the job's CPU is at {cores}",
            f"The GPU waits for batches: the job's processes use "
            f"{seen.cpu_cores / seen.capacity_cores:.0%} of what they can.",
            f"Increase DataLoader workers (now {seen.loader_workers}).",
        )
    if seen.disk_read_mb_s >= disk_threshold_mb_s:
        return Diagnosis(
            "diag_disk",
            f"The disk is the bottleneck: reading {seen.disk_read_mb_s:.0f} MB/s with the {gpu}",
            f"Disk reads are at or above {disk_threshold_mb_s:.0f} MB/s while the GPU "
            "and the job's CPU are not busy.",
            "Preprocess to a smaller size, or keep the data in memory.",
        )
    return Diagnosis(
        "diag_gpu_waiting",
        f"The GPU is waiting: {gpu}, the job's CPU at {cores}, the disk at "
        f"{seen.disk_read_mb_s:.0f} MB/s",
        "Nothing is busy: the steps are small, or the loop waits on the GPU often.",
        "Try a larger batch, or look for .item() or .cpu() calls inside the loop.",
    )


@dataclass(frozen=True)
class Window:
    observed: Observed
    running_for_s: float


def read_window(
    session: Session, *, job_id: str, stage: str, started_at: datetime | None, now: datetime
) -> Window:
    """The last :data:`WINDOW_S` of this job's machine and process samples, its loader
    workers (from the stage's metrics) and how long it has run since it started or
    resumed."""
    since = now - timedelta(seconds=WINDOW_S)
    machine = session.execute(
        select(ResourceSample.gpu_util_percent, ResourceSample.disk_read_mb_s).where(
            ResourceSample.job_id == job_id,
            ResourceSample.stage == stage,
            ResourceSample.ts >= since,
        )
    ).all()
    process = session.scalars(
        select(ProcessSample.cpu_cores).where(
            ProcessSample.job_id == job_id, ProcessSample.stage == stage, ProcessSample.ts >= since
        )
    ).all()
    workers = session.scalar(
        select(EventRow.metrics["loader_workers"].astext)
        .where(EventRow.job_id == job_id, EventRow.metrics.has_key("loader_workers"))
        .order_by(EventRow.id.desc())
        .limit(1)
    )
    resumed = session.scalar(
        select(JobTransition.at)
        .where(JobTransition.job_id == job_id, JobTransition.to_state == "running")
        .order_by(JobTransition.id.desc())
        .limit(1)
    )
    starts = [t for t in (started_at, resumed) if t is not None]
    running_for = (now - max(starts)).total_seconds() if starts else 0.0
    seen = observe(
        gpu=[row[0] for row in machine],
        disk_read=[float(row[1] or 0.0) for row in machine],
        cpu_cores=list(process),
        loader_workers=int(workers) if workers and workers.isdigit() else 0,
        logical_cpus=os.cpu_count() or 1,
    )
    return Window(seen, running_for)
