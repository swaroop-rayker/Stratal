"""Spike S-05: which MON-2.1 metrics can be read from inside a container?

psutil and nvidia-ml-py are not project dependencies yet (they arrive with the
sampler in M2), so this runs them in a throwaway environment:

  docker compose run --rm worker-gpu uv run --no-project \
      --with psutil --with nvidia-ml-py python scripts/spikes/s05_metrics.py
"""

import json
import time
from collections.abc import Callable
from typing import Any

import psutil
import pynvml


def attempt(read: Callable[[], Any]) -> Any:
    try:
        return read()
    except Exception as exc:
        return f"UNAVAILABLE: {type(exc).__name__}: {exc}"


def main() -> None:
    report: dict[str, Any] = {}

    psutil.cpu_percent(interval=None)
    disk_before = psutil.disk_io_counters()
    time.sleep(1.0)
    disk_after = psutil.disk_io_counters()
    memory = psutil.virtual_memory()
    report["psutil"] = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_per_core_count": len(psutil.cpu_percent(interval=0.2, percpu=True)),
        "ram_total_mb": round(memory.total / 2**20),
        "ram_used_mb": round(memory.used / 2**20),
        "ram_available_mb": round(memory.available / 2**20),
        "swap_total_mb": round(psutil.swap_memory().total / 2**20),
        "process_rss_mb": round(psutil.Process().memory_info().rss / 2**20, 1),
        "disk_counters_present": disk_before is not None,
        "disk_read_mb_s": attempt(
            lambda: round((disk_after.read_bytes - disk_before.read_bytes) / 2**20, 2)
        ),
        "disk_write_mb_s": attempt(
            lambda: round((disk_after.write_bytes - disk_before.write_bytes) / 2**20, 2)
        ),
    }

    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    report["nvml"] = {
        "device_count": attempt(pynvml.nvmlDeviceGetCount),
        "name": attempt(lambda: pynvml.nvmlDeviceGetName(handle)),
        "utilization_gpu_percent": attempt(
            lambda: pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        ),
        "utilization_memory_percent": attempt(
            lambda: pynvml.nvmlDeviceGetUtilizationRates(handle).memory
        ),
        "vram_used_mb": attempt(lambda: round(pynvml.nvmlDeviceGetMemoryInfo(handle).used / 2**20)),
        "vram_total_mb": attempt(
            lambda: round(pynvml.nvmlDeviceGetMemoryInfo(handle).total / 2**20)
        ),
        "temperature_c": attempt(
            lambda: pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        ),
        "power_w": attempt(lambda: round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000, 1)),
        "fan_percent": attempt(lambda: pynvml.nvmlDeviceGetFanSpeed(handle)),
        "per_process_memory": attempt(
            lambda: [
                {"pid": p.pid, "used_bytes": p.usedGpuMemory}
                for p in pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            ]
        ),
    }
    pynvml.nvmlShutdown()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
