# pyright: basic
"""Reading the machine: psutil, NVML and the PyTorch allocator (MON-2.1, MON-2.4).

psutil and nvidia-ml-py ship without type hints, so this module is checked in
pyright's basic mode and returns plain typed dataclasses to the strict code.
A metric that cannot be read is ``None``, never zero.
"""

import contextlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

MB = 1024 * 1024


@dataclass(frozen=True)
class SystemReading:
    cpu_percent: float
    cpu_per_core: list[float]
    ram_used_mb: float
    ram_available_mb: float
    ram_total_mb: float
    swap_used_mb: float
    disk_read_bytes: int
    disk_write_bytes: int


@dataclass(frozen=True)
class GpuReading:
    util_percent: float | None
    mem_util_percent: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    temp_c: float | None
    power_w: float | None


def read_system() -> SystemReading:
    """CPU since the previous call, memory now, and cumulative disk counters."""
    per_core = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
    memory = psutil.virtual_memory()
    disk = psutil.disk_io_counters()
    return SystemReading(
        cpu_percent=round(sum(per_core) / max(len(per_core), 1), 1),
        cpu_per_core=per_core,
        ram_used_mb=(memory.total - memory.available) / MB,
        ram_available_mb=memory.available / MB,
        ram_total_mb=memory.total / MB,
        swap_used_mb=psutil.swap_memory().used / MB,
        disk_read_bytes=int(disk.read_bytes) if disk else 0,
        disk_write_bytes=int(disk.write_bytes) if disk else 0,
    )


class GpuProbe:
    """NVML readings for the first NVIDIA GPU. The integrated GPU is not visible to NVML."""

    def __init__(self) -> None:
        self._nvml = None
        self._handle = None
        self.error: str | None = None
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._handle is not None

    def name(self) -> str | None:
        """The GPU's product name, e.g. 'NVIDIA GeForce RTX 4050 Laptop GPU'."""
        nvml, handle = self._nvml, self._handle
        if nvml is None or handle is None:
            return None
        value: Any = self._try(lambda: nvml.nvmlDeviceGetName(handle))
        return value.decode() if isinstance(value, bytes) else value

    def slowdown_c(self) -> float | None:
        """The temperature at which the card slows itself down (NVML's slowdown threshold);
        the most ``thermal_pause_c`` may be (SCR-14)."""
        nvml, handle = self._nvml, self._handle
        if nvml is None or handle is None:
            return None
        value: Any = self._try(
            lambda: nvml.nvmlDeviceGetTemperatureThreshold(
                handle, nvml.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN
            )
        )
        return float(value) if value else None

    def _try(self, read: Callable[[], Any]) -> Any:
        try:
            return read()
        except Exception:
            return None

    def read(self) -> GpuReading | None:
        nvml, handle = self._nvml, self._handle
        if nvml is None or handle is None:
            return None
        rates: Any = self._try(lambda: nvml.nvmlDeviceGetUtilizationRates(handle))
        memory: Any = self._try(lambda: nvml.nvmlDeviceGetMemoryInfo(handle))
        temp = self._try(lambda: nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU))
        power = self._try(lambda: nvml.nvmlDeviceGetPowerUsage(handle))
        return GpuReading(
            util_percent=float(rates.gpu) if rates is not None else None,
            mem_util_percent=float(rates.memory) if rates is not None else None,
            vram_used_mb=memory.used / MB if memory is not None else None,
            vram_total_mb=memory.total / MB if memory is not None else None,
            temp_c=float(temp) if temp is not None else None,
            power_w=power / 1000.0 if power is not None else None,
        )


def process_rss_mb(pid: int) -> float:
    """Resident memory of a process and all of its children."""
    total = 0
    with contextlib.suppress(psutil.Error):
        process = psutil.Process(pid)
        total += process.memory_info().rss
        for child in process.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                total += child.memory_info().rss
    return total / MB


class ProcessCpu:
    """CPU of a process and all of its children, in cores, between two calls (MON-3.2).

    Each process's user and system time is remembered by PID, so a DataLoader worker
    that started since the last call counts from zero and one that ended simply drops
    out; the total is never negative. The first call only sets the baseline and returns
    ``None``.
    """

    def __init__(self, pid: int, clock: Callable[[], float] | None = None) -> None:
        import time

        self._pid = pid
        self._clock = clock or time.monotonic
        self._times: dict[int, float] = {}
        self._at: float | None = None

    def _read(self) -> dict[int, float]:
        times: dict[int, float] = {}
        with contextlib.suppress(psutil.Error):
            process = psutil.Process(self._pid)
            for each in [process, *process.children(recursive=True)]:
                with contextlib.suppress(psutil.Error):
                    t = each.cpu_times()
                    times[each.pid] = t.user + t.system
        return times

    def __call__(self) -> float | None:
        now, times = self._clock(), self._read()
        previous, at = self._times, self._at
        self._times, self._at = times, now
        if at is None or now <= at:
            return None
        used = sum(max(t - previous.get(pid, 0.0), 0.0) for pid, t in times.items())
        return used / (now - at)


def torch_stats() -> tuple[float, float, float] | None:
    """Allocated, reserved and peak-allocated VRAM in MB, as PyTorch sees them.

    ``None`` unless this process has already imported PyTorch and initialized
    CUDA: looking must never be what loads either (SPEC CL-11).
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return None
    try:
        if not torch.cuda.is_initialized():
            return None
        return (
            torch.cuda.memory_allocated() / MB,
            torch.cuda.memory_reserved() / MB,
            torch.cuda.max_memory_allocated() / MB,
        )
    except Exception:
        return None


def oom_kill_count() -> int | None:
    """How many processes the kernel has killed in this container for exceeding its
    memory limit (cgroup v2). ``None`` if the counter cannot be read."""
    try:
        for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def memory_limit_mb() -> float | None:
    """This container's memory limit, if it has one."""
    try:
        raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        return None if raw == "max" else int(raw) / MB
    except (OSError, ValueError):
        return None
