# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""NFR-1 and NFR-7: the label stage on the real card, with the network blocked.

One run of the real label stage (real models, the real units) over 48 COCO
images, made ready by ingest, clean and preprocess on a temporary data folder.
Every socket connection is refused for the whole run, which proves the stage
needs no network once the models are pulled (NFR-7.AC1). PyTorch's peak
reserved memory plus the CUDA context stays under 5.5 GB (NFR-1, ADR-0006
rule 9).

Run in the GPU image, after ``models pull --all``:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

import pytest

from stratal.core.settings import get_settings
from stratal.data.clean import OWNER_UNITS as DEDUPE
from stratal.label.stage import OWNER_UNITS as LABEL_UNITS
from stratal.label.stage import LabelResult
from stratal.runtime.stages import real_label_models
from stratal.storage.manifest import STATUS_ACCEPTED
from stratal.testing import stagekit

pytestmark = pytest.mark.gpu

COCO = Path("/sources/coco10k")
NFR_1_LIMIT_GB = 5.5
CUDA_CONTEXT_GB = 0.09  # ADR-0006 rule 9, measured in spike S-02


@dataclass
class _Run:
    result: LabelResult
    rows: dict[str, dict]
    peak_reserved_gb: float
    blocked_attempts: list[str]


@pytest.fixture(scope="module")
def labelled(tmp_path_factory: pytest.TempPathFactory) -> _Run:
    import torch

    files = sorted(COCO.glob("*.jpg"))[:48]
    if len(files) < 48:
        pytest.skip("the COCO 10k source folder is not there")
    base = tmp_path_factory.mktemp("nfr")
    source = base / "src"
    source.mkdir()
    for file in files:
        shutil.copy(file, source / file.name)
    data = base / "data"
    data.mkdir()
    (data / "models").symlink_to(get_settings().data_dir / "models")
    raw = stagekit.ingest(source, data)
    cleaned = stagekit.clean(data, raw, DEDUPE)
    ready = stagekit.preprocess(data, cleaned.record).record

    attempts: list[str] = []
    original = socket.socket.connect

    def refuse(self: socket.socket, address: object) -> None:
        attempts.append(repr(address))
        raise OSError("network disabled for NFR-7")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    try:
        torch.cuda.reset_peak_memory_stats()
        result = stagekit.label(data, ready, models=real_label_models(data), units=LABEL_UNITS)
        peak = torch.cuda.max_memory_reserved() / 2**30
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]
    return _Run(result, stagekit.rows_of(data, result.record), peak, attempts)


def test_NFR_7_the_label_stage_runs_with_the_network_blocked(labelled: _Run) -> None:
    accepted = [r for r in labelled.rows.values() if r["status"] == STATUS_ACCEPTED]

    assert labelled.blocked_attempts == [], "nothing even tried to connect"
    assert len(accepted) == 48
    assert all(r["caption"] and r["clip_score"] is not None for r in accepted)
    assert labelled.result.metrics["used_gpu"] == 1


def test_NFR_1_peak_vram_of_the_label_stage_stays_under_5_5_gb(labelled: _Run) -> None:
    total = labelled.peak_reserved_gb + CUDA_CONTEXT_GB

    assert total < NFR_1_LIMIT_GB, f"peak {labelled.peak_reserved_gb:.2f} GB reserved + context"
    print(f"NFR-1: peak reserved {labelled.peak_reserved_gb:.2f} GB + {CUDA_CONTEXT_GB} GB context")
