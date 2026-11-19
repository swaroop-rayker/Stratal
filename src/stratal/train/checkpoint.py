# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""Training checkpoints: everything an exact resume needs, never pickled (ADR-0014 rule 5).

A checkpoint is a pair of files named after the global step,
``ckpt-000001234.safetensors`` with the tensors (model weights, the optimizer's
per-parameter state, PyTorch's random states) and ``ckpt-000001234.json`` with
the rest (position, optimizer settings, scheduler, Python's random state,
metrics so far). Loading either runs no code (NFR-8).

Each file is written under a temporary name, flushed to disk and renamed; the
JSON file goes last and is what marks the pair complete. Only then are older
pairs removed, so a crash at any moment leaves at least one complete checkpoint
(REG-2.6: only the latest is kept).
"""

import json
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import save_file

from stratal.train.weights import read_tensors

FORMAT = 1
_PREFIX = "ckpt-"


@dataclass(frozen=True)
class Position:
    """Where training stands: ``batches_done`` batches of ``epoch`` (0-based) are trained."""

    epoch: int
    batches_done: int
    global_step: int


@dataclass
class Checkpoint:
    position: Position
    config_hash: str
    history: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    """One entry per finished epoch: losses and accuracies."""


def checkpoint_dir(data_dir: Path, job_id: str) -> Path:
    return data_dir / "jobs" / job_id / "checkpoints"


def _write(path: Path, write: Any) -> None:
    partial = path.with_name(path.name + ".part")
    write(partial)
    with partial.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _scalar(value: Any) -> Any:
    """An optimizer state value that is not a tensor with dimensions, as JSON."""
    if torch.is_tensor(value):
        return {"tensor": str(value.dtype).removeprefix("torch."), "value": value.item()}
    return value


def _unscalar(value: Any) -> Any:
    if isinstance(value, dict) and "tensor" in value:
        return torch.tensor(value["value"], dtype=getattr(torch, value["tensor"]))
    return value


def save(
    folder: Path,
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Any,
    checkpoint: Checkpoint,
) -> Path:
    """Write a checkpoint, then remove older ones. Returns the JSON file's path."""
    folder.mkdir(parents=True, exist_ok=True)
    tensors: dict[str, torch.Tensor] = {
        f"model.{name}": value.detach().cpu().contiguous()
        for name, value in model.state_dict().items()
    }
    state = optimizer.state_dict()
    scalars: dict[str, Any] = {}
    for index, values in state["state"].items():
        for key, value in values.items():
            name = f"optim.{index}.{key}"
            if torch.is_tensor(value) and value.ndim > 0:
                tensors[name] = value.detach().cpu().contiguous()
            else:
                scalars[name] = _scalar(value)
    tensors["rng.torch"] = torch.get_rng_state()
    if torch.cuda.is_available() and torch.cuda.is_initialized():
        for index, rng in enumerate(torch.cuda.get_rng_state_all()):
            tensors[f"rng.cuda.{index}"] = rng
    version, internal, gauss = random.getstate()
    meta = {
        "format": FORMAT,
        "config_hash": checkpoint.config_hash,
        "position": asdict(checkpoint.position),
        "history": checkpoint.history,
        "param_groups": state["param_groups"],
        "optimizer_scalars": scalars,
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "python_random": [version, list(internal), gauss],
    }
    stem = f"{_PREFIX}{checkpoint.position.global_step:09d}"
    _write(folder / f"{stem}.safetensors", lambda p: save_file(tensors, str(p)))
    _write(folder / f"{stem}.json", lambda p: p.write_text(json.dumps(meta), encoding="utf-8"))
    for old in folder.glob(f"{_PREFIX}*"):
        if not old.name.startswith(stem):
            old.unlink(missing_ok=True)
    return folder / f"{stem}.json"


def latest(folder: Path) -> Path | None:
    """The newest complete checkpoint's JSON file, or ``None``."""
    complete = [
        meta
        for meta in folder.glob(f"{_PREFIX}*.json")
        if meta.with_suffix(".safetensors").is_file()
    ]
    return max(complete, key=lambda p: p.name) if complete else None


@dataclass
class Loaded:
    checkpoint: Checkpoint
    meta: dict[str, Any]
    tensors: dict[str, torch.Tensor]


def load(meta_path: Path) -> Loaded:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != FORMAT:
        raise ValueError(f"checkpoint {meta_path.name} has format {meta.get('format')!r}")
    tensors = read_tensors(
        meta_path.with_suffix(".safetensors"),
        suggested_action="Cancel the run, which removes its checkpoint, and start it again.",
    )
    checkpoint = Checkpoint(
        Position(**meta["position"]), str(meta["config_hash"]), list(meta["history"])
    )
    return Loaded(checkpoint, meta, tensors)


def restore(
    loaded: Loaded, *, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: Any
) -> None:
    """Put model, optimizer, scheduler and random states back as they were saved."""
    tensors, meta = loaded.tensors, loaded.meta
    model.load_state_dict(
        {name.removeprefix("model."): t for name, t in tensors.items() if name.startswith("model.")}
    )
    state: dict[int, dict[str, Any]] = {}
    for name, tensor in tensors.items():
        if name.startswith("optim."):
            _, index, key = name.split(".", 2)
            state.setdefault(int(index), {})[key] = tensor
    for name, value in meta["optimizer_scalars"].items():
        _, index, key = name.split(".", 2)
        state.setdefault(int(index), {})[key] = _unscalar(value)
    optimizer.load_state_dict({"state": state, "param_groups": meta["param_groups"]})
    if scheduler is not None and meta["scheduler"] is not None:
        scheduler.load_state_dict(meta["scheduler"])
    torch.set_rng_state(tensors["rng.torch"])
    cuda_states = [tensors[k] for k in sorted(tensors) if k.startswith("rng.cuda.")]
    if cuda_states and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(cuda_states)
    version, internal, gauss = meta["python_random"]
    random.setstate((version, tuple(internal), gauss))


def remove_all(folder: Path) -> int:
    """Delete every checkpoint in ``folder``; returns the bytes freed (REG-2.6)."""
    freed = 0
    for path in folder.glob(f"{_PREFIX}*"):
        freed += path.stat().st_size
        path.unlink(missing_ok=True)
    return freed
