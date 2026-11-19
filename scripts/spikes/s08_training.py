# pyright: basic, reportMissingImports=false
"""Spike S-08: the reference models on CIFAR-10, GPU against CPU (M8, ADR-0014).

Measures what ADR-0014 assumes, on the balanced split ds_f8e67211dd94:

1. Input: images per second from the blob store with 0, 2 and 4 loader
   workers, and the cost of decoding the whole train split into memory once.
2. Steps: milliseconds per training step for each reference model, on each
   device, at batch sizes 128 and 256, from tensors already in memory (so only
   the model is timed); peak VRAM.
3. A short real run: the small CNN, three epochs on the GPU through the loader,
   with validation accuracy after each; peak VRAM and RAM.
4. Checkpoints: size, save time (temporary file, fsync, rename) and load time of
   model plus Adam state, as safetensors and JSON (NFR-8).

Run in the GPU image, with the new source mounted, writing to docs/spikes:

    docker compose --profile gpu run --rm -v ./src:/app/src:ro -v ./docs/spikes:/out \
        worker-gpu python scripts/spikes/s08_training.py
"""

import json
import os
import resource
import tempfile
import time
from pathlib import Path

import psutil
import torch
from safetensors.torch import load_file, save_file
from sqlalchemy.orm import Session
from torch import nn
from torch.utils.data import DataLoader

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.label.hf import set_vram_cap
from stratal.storage.blobs import BlobStore
from stratal.train.config import ModelSpec
from stratal.train.data import EpochOrder, LabelledImages, split_items
from stratal.train.reference import build_model, parameter_count

VERSION = "ds_f8e67211dd94"
OUT = Path("/out")
SEED = 8
SHAPE = (3, 32, 32)
WARMUP, TIMED = 5, 20
MB = 1024 * 1024


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / MB


def peak_rss_mb() -> float:
    """Peak RSS of this process (ru_maxrss is in KiB on Linux)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def sync(device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()


def loader(items, *, workers: int, batch: int, epoch: int = 0) -> DataLoader:
    return DataLoader(
        LabelledImages(items),
        batch_size=batch,
        sampler=EpochOrder(len(items), seed=SEED, epoch=epoch),
        num_workers=workers,
        pin_memory=True,
        persistent_workers=False,
    )


def measure_input(train) -> dict:
    results: dict = {"loader": {}}
    for workers in (0, 2, 4):
        images = 0
        started = time.monotonic()
        for batches, (x, _) in enumerate(loader(train, workers=workers, batch=256), start=1):
            images += x.shape[0]
            if batches == 40:
                break
        seconds = time.monotonic() - started
        results["loader"][str(workers)] = {
            "images": images,
            "seconds": round(seconds, 2),
            "images_per_s": round(images / seconds),
        }
        print(f"loader workers={workers}: {images / seconds:,.0f} images/s", flush=True)
    before = rss_mb()
    started = time.monotonic()
    xs = torch.empty((len(train), *SHAPE))
    ys = torch.empty(len(train), dtype=torch.long)
    for i, (x, y) in enumerate(LabelledImages(train)):
        xs[i], ys[i] = x, y
    seconds = time.monotonic() - started
    results["preload"] = {
        "images": len(train),
        "seconds": round(seconds, 1),
        "rss_added_mb": round(rss_mb() - before),
        "tensor_mb": round(xs.numel() * 4 / MB),
    }
    print(f"preload: {len(train):,} images in {seconds:.1f} s", flush=True)
    return {"results": results, "xs": xs, "ys": ys}


def time_steps(name: str, device: str, batch: int, xs, ys) -> dict:
    torch.manual_seed(SEED)
    model = build_model(ModelSpec(name=name), SHAPE, 10).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    times = []
    for step in range(WARMUP + TIMED):
        start = (step * batch) % (len(xs) - batch)
        x = xs[start : start + batch].to(device, non_blocking=True)
        y = ys[start : start + batch].to(device, non_blocking=True)
        sync(device)
        t0 = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        loss = loss_fn(model(x), y)
        loss.backward()
        optimizer.step()
        sync(device)
        if step >= WARMUP:
            times.append(time.perf_counter() - t0)
    ms = sorted(times)[len(times) // 2] * 1000
    result = {
        "model": name,
        "device": device,
        "batch": batch,
        "parameters": parameter_count(model),
        "median_step_ms": round(ms, 2),
        "samples_per_s": round(batch / (ms / 1000)),
    }
    if device == "cuda":
        result["peak_allocated_mb"] = round(torch.cuda.max_memory_allocated() / MB, 1)
        result["peak_reserved_mb"] = round(torch.cuda.max_memory_reserved() / MB, 1)
    del model, optimizer
    print(json.dumps(result), flush=True)
    return result


def evaluate(model, items, workers: int) -> float:
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for x, y in loader(items, workers=workers, batch=512):
            pred = model(x.to("cuda", non_blocking=True)).argmax(1).cpu()
            correct += int((pred == y).sum())
            total += len(y)
    model.train()
    return correct / total


def real_run(train, val, workers: int) -> tuple[dict, nn.Module, torch.optim.Optimizer]:
    torch.manual_seed(SEED)
    model = build_model(ModelSpec(name="small_cnn"), SHAPE, 10).to("cuda")
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    epochs = []
    for epoch in range(3):
        started = time.monotonic()
        seen = 0
        for x, y in loader(train, workers=workers, batch=128, epoch=epoch):
            x, y = x.to("cuda", non_blocking=True), y.to("cuda", non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(x), y)
            loss.backward()
            optimizer.step()
            seen += len(y)
        train_s = time.monotonic() - started
        accuracy = evaluate(model, val, workers)
        epochs.append(
            {
                "epoch": epoch + 1,
                "train_seconds": round(train_s, 1),
                "images_per_s": round(seen / train_s),
                "val_accuracy": round(accuracy, 4),
                "last_loss": round(float(loss), 4),
            }
        )
        print(json.dumps(epochs[-1]), flush=True)
    result = {
        "model": "small_cnn",
        "batch": 128,
        "loader_workers": workers,
        "epochs": epochs,
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / MB, 1),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved() / MB, 1),
        "process_peak_rss_mb": round(peak_rss_mb()),
    }
    return result, model, optimizer


def checkpoint(model: nn.Module, optimizer: torch.optim.Optimizer) -> dict:
    """Model and Adam state as safetensors, everything else as JSON; never pickled."""
    tensors = {f"model.{k}": v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    state = optimizer.state_dict()
    scalars: dict = {"param_groups": state["param_groups"], "steps": {}}
    for index, values in state["state"].items():
        for key, value in values.items():
            if torch.is_tensor(value) and value.ndim > 0:
                tensors[f"optim.{index}.{key}"] = value.detach().cpu().contiguous()
            else:
                scalars["steps"][f"{index}.{key}"] = float(value)
    with tempfile.TemporaryDirectory(dir="/data/tmp") as folder:
        final = Path(folder) / "ckpt.safetensors"
        partial = final.with_suffix(".part")
        started = time.perf_counter()
        save_file(tensors, str(partial))
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(partial, final)
        (Path(folder) / "ckpt.json").write_text(json.dumps(scalars))
        saved = time.perf_counter() - started
        started = time.perf_counter()
        loaded = load_file(str(final))
        load_s = time.perf_counter() - started
        size = final.stat().st_size + (Path(folder) / "ckpt.json").stat().st_size
    assert set(loaded) == set(tensors)
    return {
        "tensors": len(tensors),
        "bytes": size,
        "save_ms": round(saved * 1000, 1),
        "load_ms": round(load_s * 1000, 1),
    }


def main() -> None:
    settings = get_settings()
    (settings.data_dir / "tmp").mkdir(exist_ok=True)
    set_vram_cap(0.85)
    with Session(get_engine()) as session:
        rows = list(registry.load_version(session, settings.data_dir, VERSION))
    items = split_items(rows, BlobStore(settings.data_dir))
    train, val = items.by_split["train"], items.by_split["val"]
    report: dict = {
        "version": VERSION,
        "classes": items.classes,
        "rows": {k: len(v) for k, v in items.by_split.items()},
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "cpu_count": os.cpu_count(),
        "torch_threads": torch.get_num_threads(),
        "container_memory_limit_mb": None,
    }
    limit = Path("/sys/fs/cgroup/memory.max")
    if limit.exists() and limit.read_text().strip().isdigit():
        report["container_memory_limit_mb"] = int(limit.read_text()) // MB
    print(json.dumps(report), flush=True)

    measured = measure_input(train)
    report["input"] = measured["results"]
    xs, ys = measured["xs"], measured["ys"]

    report["steps"] = [
        time_steps(name, device, batch, xs, ys)
        for name in ("mlp", "small_cnn")
        for device in ("cuda", "cpu")
        for batch in (128, 256)
    ]
    del xs, ys

    loader_rates = report["input"]["loader"]
    workers = max(loader_rates, key=lambda k: loader_rates[k]["images_per_s"])
    run, model, optimizer = real_run(train, val, int(workers))
    report["run"] = run
    report["checkpoint_small_cnn"] = checkpoint(model, optimizer)
    print(json.dumps(report["checkpoint_small_cnn"]), flush=True)
    del model, optimizer
    torch.manual_seed(SEED)
    mlp = build_model(ModelSpec(name="mlp"), SHAPE, 10)
    mlp_optimizer = torch.optim.Adam(mlp.parameters())
    nn.CrossEntropyLoss()(mlp(torch.randn(8, *SHAPE)), torch.zeros(8, dtype=torch.long)).backward()
    mlp_optimizer.step()  # one step, so Adam's state exists and is saved
    report["checkpoint_mlp"] = checkpoint(mlp, mlp_optimizer)
    report["process_peak_rss_mb"] = round(peak_rss_mb())
    (OUT / "S-08-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print("written", OUT / "S-08-results.json", flush=True)


if __name__ == "__main__":
    main()
