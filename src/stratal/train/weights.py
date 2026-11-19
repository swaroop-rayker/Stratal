# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""A registry version's files: ``model.safetensors`` and ``model.json`` (ADR-0015 rule 3).

The folder is written under a temporary name and renamed into place, so a
version's folder either holds both files or does not exist. Nothing here
unpickles anything (NFR-8).
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import torch
from safetensors import SafetensorError
from safetensors.torch import load_file, save_file

from stratal.core.errors import StratalError, WeightsUnreadable
from stratal.registry.store import META_FILE, WEIGHTS_FILE


def write(folder: Path, model: torch.nn.Module, meta: dict[str, Any]) -> int:
    """Write a version's files into ``folder``, replacing any; returns their bytes."""
    partial = folder.with_name(folder.name + ".part")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    tensors = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    save_file(tensors, str(partial / WEIGHTS_FILE))
    (partial / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for path in partial.iterdir():
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    if folder.exists():
        shutil.rmtree(folder)
    os.replace(partial, folder)
    return size(folder)


def size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


def load_into(model: torch.nn.Module, folder: Path) -> None:
    """Load a version's weights into ``model``, which must have the same layers."""
    path = folder / WEIGHTS_FILE
    if not path.is_file():
        raise StratalError(
            f"The weights file {path} is missing.",
            suggested_action="Check the data folder; base weights are never deleted by "
            "retention (REG-2.1).",
        )
    model.load_state_dict(
        read_tensors(path, suggested_action="Re-train the version (REG-2.7)."), strict=True
    )


def read_tensors(path: Path, *, suggested_action: str) -> dict[str, torch.Tensor]:
    """Read a safetensors file, refusing anything else (NFR-8).

    safetensors parses a JSON header and raw tensor bytes and never runs code, so a
    pickle saved under a ``.safetensors`` name fails here before any of it is used.
    """
    try:
        return load_file(str(path))
    except SafetensorError as exc:
        raise WeightsUnreadable(
            f"{path.name} is not a valid safetensors file ({exc}); nothing in it was loaded.",
            suggested_action=suggested_action,
        ) from exc
