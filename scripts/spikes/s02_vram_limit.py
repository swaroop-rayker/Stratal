"""Spike S-02: what happens at the VRAM limit?

Allocates 256 MB tensors until something gives, timing every step and reading
the driver's view (nvidia-smi) and system RAM alongside PyTorch's own numbers.

Run uncapped, then capped:
  docker compose run --rm worker-gpu python scripts/spikes/s02_vram_limit.py
  docker compose run --rm worker-gpu python scripts/spikes/s02_vram_limit.py --fraction 0.85

Stops at an out-of-memory error, at --max-gb, or when one step takes longer
than --slow-s seconds (the sign of spilling into system RAM).
"""

import argparse
import json
import subprocess
import time

import torch

STEP_MB = 256


def nvml_used_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip().splitlines()[0])


def mem_available_mb() -> int:
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return -1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fraction", type=float, default=None)
    parser.add_argument("--max-gb", type=float, default=8.0)
    parser.add_argument("--slow-s", type=float, default=3.0)
    args = parser.parse_args()

    device = torch.device("cuda:0")
    total_mb = torch.cuda.get_device_properties(0).total_memory / 2**20
    baseline_nvml = nvml_used_mb()
    torch.zeros(1, device=device)
    torch.cuda.synchronize()
    context_mb = nvml_used_mb() - baseline_nvml
    if args.fraction is not None:
        torch.cuda.set_per_process_memory_fraction(args.fraction, 0)

    held: list[torch.Tensor] = []
    steps: list[dict[str, float]] = []
    outcome = "reached --max-gb without an error"
    slowest = 0.0
    while len(held) * STEP_MB < args.max_gb * 1024:
        t0 = time.perf_counter()
        try:
            block = torch.empty(STEP_MB * 2**20, dtype=torch.uint8, device=device)
            block.fill_(1)
            torch.cuda.synchronize()
        except torch.OutOfMemoryError as exc:
            outcome = f"OutOfMemoryError: {str(exc).splitlines()[0][:160]}"
            break
        seconds = time.perf_counter() - t0
        held.append(block)
        slowest = max(slowest, seconds)
        steps.append(
            {
                "held_mb": len(held) * STEP_MB,
                "step_s": round(seconds, 3),
                "torch_reserved_mb": round(torch.cuda.memory_reserved() / 2**20),
                "nvml_used_mb": nvml_used_mb(),
                "ram_available_mb": mem_available_mb(),
            }
        )
        if seconds > args.slow_s:
            outcome = f"stopped: a 256 MB step took {seconds:.1f} s (spilling into system RAM?)"
            break

    last = steps[-1] if steps else {}
    report = {
        "fraction": args.fraction,
        "total_vram_mb": round(total_mb),
        "nvml_used_before_mb": baseline_nvml,
        "cuda_context_mb": context_mb,
        "outcome": outcome,
        "held_at_stop_mb": len(held) * STEP_MB,
        "peak_torch_reserved_mb": last.get("torch_reserved_mb"),
        "peak_nvml_used_mb": max((s["nvml_used_mb"] for s in steps), default=None),
        "slowest_step_s": round(slowest, 3),
        "ram_available_start_mb": steps[0]["ram_available_mb"] if steps else None,
        "ram_available_end_mb": last.get("ram_available_mb"),
        "last_steps": steps[-4:],
    }
    del held
    torch.cuda.empty_cache()
    report["allocated_after_release_mb"] = round(torch.cuda.memory_allocated() / 2**20)
    report["nvml_used_after_release_mb"] = nvml_used_mb()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
