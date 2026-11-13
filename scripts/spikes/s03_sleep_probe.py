"""Spike S-03: what does sleep do to a running GPU job? Needs the owner at the laptop.

Start it, put the laptop to sleep, wake it, and watch what the loop reports:

  docker compose run --rm worker-gpu python scripts/spikes/s03_sleep_probe.py

Every second it does a small GPU operation and prints wall-clock and monotonic
time. A jump in "gap_s" marks the sleep. An error line shows what a real job
would have hit. Stop it with Ctrl+C.
"""

import json
import time
from datetime import UTC, datetime

import torch


def main() -> None:
    device = torch.device("cuda:0")
    x = torch.randn(1024, 1024, device=device)
    last_wall = time.time()
    last_mono = time.monotonic()
    tick = 0
    while True:
        time.sleep(1.0)
        tick += 1
        wall, mono = time.time(), time.monotonic()
        line: dict[str, object] = {
            "tick": tick,
            "utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "wall_gap_s": round(wall - last_wall, 2),
            "mono_gap_s": round(mono - last_mono, 2),
        }
        last_wall, last_mono = wall, mono
        try:
            value = float((x @ x).sum().item())
            line["gpu_ok"] = value == value  # False only if NaN
        except Exception as exc:
            line["gpu_ok"] = False
            line["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        if tick % 10 == 0 or line.get("gpu_ok") is not True or line["wall_gap_s"] > 3:  # type: ignore[operator]
            print(json.dumps(line), flush=True)


if __name__ == "__main__":
    main()
