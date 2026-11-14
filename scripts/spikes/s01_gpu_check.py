"""Spike S-01: can PyTorch use the GPU from inside a container?

Run:  docker compose run --rm worker-gpu python scripts/spikes/s01_gpu_check.py
"""

import json
import time

import torch


def main() -> None:
    started = time.perf_counter()
    available = torch.cuda.is_available()
    report: dict[str, object] = {
        "torch": torch.__version__,
        "cuda_build": torch.version.cuda,
        "cuda_available": available,
    }
    if available:
        device = torch.device("cuda:0")
        a = torch.randn(4096, 4096, device=device)
        b = torch.randn(4096, 4096, device=device)
        torch.cuda.synchronize()
        report["seconds_to_first_cuda_op"] = round(time.perf_counter() - started, 2)
        t0 = time.perf_counter()
        c = a @ b
        torch.cuda.synchronize()
        total_mb = torch.cuda.get_device_properties(0).total_memory / 2**20
        report.update(
            device=torch.cuda.get_device_name(0),
            total_vram_mb=round(total_mb),
            matmul_4096_ms=round((time.perf_counter() - t0) * 1000, 1),
            checksum_finite=bool(torch.isfinite(c).all().item()),
            allocated_mb=round(torch.cuda.memory_allocated() / 2**20),
            reserved_mb=round(torch.cuda.memory_reserved() / 2**20),
        )
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if available else 1)


if __name__ == "__main__":
    main()
