# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Choosing the device for ``device: auto`` (RUN-1.9, ADR-0014 rule 8).

A model with fewer than :data:`MAX_PARAMETERS` parameters may train faster on
the CPU than on the GPU, where every step pays for transfers and kernel
launches. So the run times :data:`TIMED` training steps on each device, on the
same batches, and keeps the faster. Spike S-08 showed the parameter count alone
does not predict the winner: the timing decides.

The result is cached by (model spec, input shape, batch size, number of
classes), in ``cache/device_choice/`` on the data volume, so an identical run
skips the benchmark, and a run moved to the CPU worker can still report the
timings that sent it there.
"""

import hashlib
import json
import os
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import torch
from torch import nn

from stratal.train.config import ModelSpec
from stratal.train.reference import build_model

MAX_PARAMETERS = 1_000_000
"""Below this, ``auto`` times both devices; above it, it uses the GPU (ADR-0014 rule 8)."""
WARMUP = 5
TIMED = 20
TIMINGS_ENV = "STRATAL_TEST_DEVICE_TIMINGS"
"""Test-only: ``gpu=<ms>,cpu=<ms>`` replaces the measurement, so a test can make either
device win (RUN-1.9.AC1)."""

Device = Literal["cpu", "cuda"]


@dataclass(frozen=True)
class DeviceChoice:
    device: Device
    gpu_step_ms: float
    cpu_step_ms: float
    cached: bool = False

    def metrics(self) -> dict[str, float | int | str]:
        return {
            "device_choice": "gpu" if self.device == "cuda" else "cpu",
            "device_gpu_step_ms": round(self.gpu_step_ms, 2),
            "device_cpu_step_ms": round(self.cpu_step_ms, 2),
            "device_benchmark_cached": int(self.cached),
        }


def cache_key(spec: ModelSpec, shape: Sequence[int], batch: int, num_classes: int) -> str:
    payload = json.dumps(
        {
            "model": spec.model_dump(mode="json"),
            "shape": list(shape),
            "batch": batch,
            "classes": num_classes,
            "torch": torch.__version__,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _cache_file(data_dir: Path, key: str) -> Path:
    return data_dir / "cache" / "device_choice" / f"{key}.json"


def cached(data_dir: Path, key: str) -> DeviceChoice | None:
    path = _cache_file(data_dir, key)
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return DeviceChoice(data["device"], data["gpu_step_ms"], data["cpu_step_ms"], cached=True)


def _store(data_dir: Path, key: str, choice: DeviceChoice) -> None:
    path = _cache_file(data_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    record = {**asdict(choice), "cached": False, "measured_at": time.time()}
    partial.write_text(json.dumps(record), encoding="utf-8")
    os.replace(partial, path)


def _time_steps(
    spec: ModelSpec,
    shape: tuple[int, int, int],
    num_classes: int,
    batches: Sequence[tuple[torch.Tensor, torch.Tensor]],
    device: Device,
) -> float:
    """Median milliseconds per Adam training step on ``device``, after a warm-up."""
    torch.manual_seed(0)
    model = build_model(spec, shape, num_classes).to(device)
    optimizer = torch.optim.Adam(model.parameters())
    loss_fn = nn.CrossEntropyLoss()
    times: list[float] = []
    for index, (x, y) in enumerate(batches):
        x, y = x.to(device), y.to(device)
        if device == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        loss_fn(model(x), y).backward()
        optimizer.step()
        if device == "cuda":
            torch.cuda.synchronize()
        if index >= WARMUP:
            times.append(1000 * (time.perf_counter() - started))
    del model, optimizer
    if device == "cuda":
        torch.cuda.empty_cache()
    return statistics.median(times)


def _from_env() -> tuple[float, float] | None:
    spec = os.environ.get(TIMINGS_ENV)
    if not spec:
        return None
    values = dict(part.split("=") for part in spec.split(","))
    return float(values["gpu"]), float(values["cpu"])


def choose(
    *,
    data_dir: Path,
    spec: ModelSpec,
    shape: tuple[int, int, int],
    num_classes: int,
    batch: int,
    batches: Sequence[tuple[torch.Tensor, torch.Tensor]],
    on_start: Callable[[], None] | None = None,
    on_measured: Callable[[Device, float], None] | None = None,
) -> DeviceChoice:
    """The faster device for this model and batch: from the cache, or measured now.

    ``batches`` holds :data:`WARMUP` + :data:`TIMED` batches of the run's own data;
    the caller holds the GPU lease. When it measures, ``on_start`` is called first and
    ``on_measured`` after each device, so the job view can show the benchmark
    in progress (SCR-04 v2, frame E).
    """
    key = cache_key(spec, shape, batch, num_classes)
    found = cached(data_dir, key)
    if found is not None:
        return found
    if on_start is not None:
        on_start()
    planted = _from_env()
    gpu_ms = planted[0] if planted else _time_steps(spec, shape, num_classes, batches, "cuda")
    if on_measured is not None:
        on_measured("cuda", gpu_ms)
    cpu_ms = planted[1] if planted else _time_steps(spec, shape, num_classes, batches, "cpu")
    if on_measured is not None:
        on_measured("cpu", cpu_ms)
    choice = DeviceChoice("cpu" if cpu_ms < gpu_ms else "cuda", gpu_ms, cpu_ms)
    _store(data_dir, key, choice)
    return choice
