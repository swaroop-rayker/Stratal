# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""BUG5, RUN-1.4, BUG2 on the real card: the owner's VRAM manager and caption loop.

Strict expected failures until ``VramManager`` and ``caption_items`` are written
(ADR-0012); then remove the marker. Run in the GPU image:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

from pathlib import Path

import pytest
from PIL import Image

from stratal.core.settings import get_settings
from stratal.label.captioning import CaptionItem, caption_items
from stratal.label.vram import VramManager
from stratal.testing.fakes import ListSink

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.xfail(
        strict=True, raises=NotImplementedError, reason="owner units RUN-1.4 and LABEL-1.1"
    ),
]

MODELS = get_settings().data_dir / "models"
COCO = Path("/sources/coco10k")


def test_BUG5_memory_allocated_returns_to_baseline_after_unload() -> None:
    import torch

    from stratal.label.hf import TorchGpu, load_captioner, set_vram_cap

    set_vram_cap(0.85)
    manager = VramManager(TorchGpu(), sink=ListSink())
    baseline = torch.cuda.memory_allocated()

    manager.load(
        lambda: load_captioner("florence-2-base", MODELS, dtype="fp16"),
        name="Florence-2-base",
        estimate_mb=900,
    )
    assert torch.cuda.memory_allocated() > baseline + 100 * 2**20
    report = manager.unload()

    assert report.returned_to_baseline
    assert torch.cuda.memory_allocated() == baseline  # RUN-1.4.AC1


def test_BUG2_an_oversized_batch_on_the_card_warns_smaller_and_completes() -> None:
    import torch

    from stratal.label.hf import TorchGpu, load_captioner, set_vram_cap

    files = sorted(COCO.glob("*.jpg"))[:64]
    if len(files) < 64:
        pytest.skip("the COCO 10k source folder is not there")
    sink = ListSink()
    manager = VramManager(TorchGpu(), sink=sink)
    captioner = manager.load(
        lambda: load_captioner("florence-2-base", MODELS, dtype="fp16"),
        name="Florence-2-base",
        estimate_mb=900,
    )
    torch.cuda.synchronize()
    set_vram_cap((torch.cuda.memory_allocated() + 600 * 2**20) / torch.cuda.mem_get_info()[1])
    items = [CaptionItem(key=f"{i:064x}", row_key=f.name) for i, f in enumerate(files)]
    by_key = dict(zip((item.key for item in items), files, strict=True))
    done: dict[str, str] = {}
    try:
        caption_items(
            items,
            captioner=captioner,
            batch_size=64,
            open_image=lambda item: Image.open(by_key[item.key]).convert("RGB"),
            commit=done.update,
            sink=sink,
            checkpoint=lambda: None,
        )
    finally:
        set_vram_cap(0.85)
        del captioner
        manager.unload()

    assert len(done) == 64
    assert any(e["level"] == "WARN" for e in sink.events)
