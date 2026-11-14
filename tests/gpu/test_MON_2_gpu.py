# pyright: basic
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""GPU telemetry on the real card (MON-2.1's GPU fields, MON-2.4).

Run in the GPU image, one process at a time:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

import json
import sys

import pytest

from stratal.mon import probes
from stratal.mon.emitter import KIND_PROCESS_SAMPLE, ListEmitter
from stratal.mon.sampler import MachineProbe
from stratal.runtime.child import ProcessReporter

pytestmark = pytest.mark.gpu

ALLOCATE_MB = 512


def test_MON_2_1_gpu_metrics_are_recorded() -> None:
    probe = MachineProbe()
    assert probe.gpu.available, probe.gpu.error

    reading = probe()

    assert reading.gpu_util_percent is not None and 0 <= reading.gpu_util_percent <= 100
    assert reading.gpu_mem_util_percent is not None
    assert reading.vram_total_mb is not None and reading.vram_total_mb > 5000, "the RTX 4050"
    assert reading.vram_used_mb is not None and 0 < reading.vram_used_mb < reading.vram_total_mb
    assert reading.gpu_temp_c is not None and 10 < reading.gpu_temp_c < 100
    assert reading.gpu_power_w is not None and reading.gpu_power_w > 0


def test_MON_2_4_nvml_and_pytorch_figures_are_recorded_side_by_side() -> None:
    if "torch" not in sys.modules:
        assert probes.torch_stats() is None, "looking must not be what loads PyTorch"
    import torch  # pyright: ignore[reportMissingImports]

    gpu = probes.GpuProbe()
    emitter = ListEmitter()
    reporter = ProcessReporter(emitter, job_id="job_gpu", stage="label", interval_s=1.0)
    before = gpu.read()
    assert before is not None and before.vram_used_mb is not None

    block = torch.empty((ALLOCATE_MB, 1024, 1024), dtype=torch.uint8, device="cuda")
    reporter.report()
    held = gpu.read()
    del block
    reporter.report()
    released = gpu.read()

    with_block, after_del = (json.loads(data) for _kind, data in emitter.sent)
    assert emitter.sent[0][0] == KIND_PROCESS_SAMPLE
    # PyTorch's view, from inside the process that owns the allocator (SPEC CL-11).
    assert with_block["torch_allocated_mb"] >= ALLOCATE_MB
    assert with_block["torch_reserved_mb"] >= with_block["torch_allocated_mb"]
    assert with_block["torch_max_allocated_mb"] >= ALLOCATE_MB
    assert with_block["rss_mb"] > 0
    # The driver's view, from NVML, taken at the same moment.
    assert held is not None and held.vram_used_mb is not None
    assert held.vram_used_mb - before.vram_used_mb >= ALLOCATE_MB * 0.9
    # The gap that explains the caching allocator (MON-2.4.AC1): after the tensor is
    # deleted PyTorch has allocated almost nothing but still reserves the block, so
    # the driver still reports it as used.
    assert after_del["torch_allocated_mb"] < 1
    assert after_del["torch_reserved_mb"] >= ALLOCATE_MB
    assert released is not None and released.vram_used_mb is not None
    assert released.vram_used_mb - before.vram_used_mb >= ALLOCATE_MB * 0.9
    assert after_del["torch_max_allocated_mb"] >= ALLOCATE_MB, "the peak is kept"
