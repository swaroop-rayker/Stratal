# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""The model adapters on the real card (LABEL-1.1, LABEL-1.3, BUG2, BUG5).

Run in the GPU image, after ``models pull --all``:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from stratal.core.settings import get_settings
from stratal.label.models import CudaOutOfMemory

pytestmark = pytest.mark.gpu

MODELS = get_settings().data_dir / "models"
COCO = Path("/sources/coco10k")


def _coco(count: int) -> list[Image.Image]:
    files = sorted(COCO.glob("*.jpg"))[:count]
    if len(files) < count:
        pytest.skip("the COCO 10k source folder is not there")
    return [Image.open(f).convert("RGB") for f in files]


def test_LABEL_1_1_florence_captions_a_batch_offline() -> None:
    from stratal.label.hf import load_captioner, set_vram_cap

    set_vram_cap(0.85)
    captioner = load_captioner("florence-2-base", MODELS, dtype="fp16")
    try:
        captions = captioner.caption(_coco(4))
    finally:
        captioner.close()

    assert len(captions) == 4
    assert all(isinstance(c, str) and len(c.split()) >= 3 for c in captions), captions


def test_LABEL_1_3_clip_gives_one_embedding_per_input() -> None:
    from stratal.label.hf import load_clip

    clip = load_clip("clip-vit-b-32-laion2b", MODELS, dtype="fp16")
    try:
        images = clip.encode_images(_coco(3))
        texts = clip.encode_texts(["a dog", "a plate of food", "word " * 200])
    finally:
        clip.close()

    assert [len(v) for v in images] == [512] * 3
    assert [len(v) for v in texts] == [512] * 3, "a long text is truncated, not refused"


def test_BUG2_an_oom_becomes_cuda_out_of_memory_and_frees_what_it_took() -> None:
    import torch

    from stratal.label.hf import load_captioner, set_vram_cap

    captioner = load_captioner("florence-2-base", MODELS, dtype="fp16")
    try:
        torch.cuda.synchronize()
        loaded = torch.cuda.memory_allocated()
        set_vram_cap((loaded + 300 * 2**20) / torch.cuda.mem_get_info()[1])
        images = _coco(4) * 16  # 64 images cannot fit in 300 MB of headroom

        with pytest.raises(CudaOutOfMemory) as raised:
            captioner.caption(images)

        assert raised.value.batch_size == 64
        torch.cuda.synchronize()
        assert torch.cuda.memory_allocated() <= loaded + 2**20, "the failed batch left nothing"
        assert len(captioner.caption(images[:1])) == 1, "and the model still works"
    finally:
        set_vram_cap(0.85)
        captioner.close()


_LOAD_AND_EXIT = """
from stratal.core.settings import get_settings
from stratal.label.hf import load_captioner
c = load_captioner("florence-2-base", get_settings().data_dir / "models", dtype="fp16")
print(len(c.caption([__import__("PIL.Image").Image.new("RGB", (64, 64))])))
"""


def test_BUG5_vram_returns_when_the_stage_process_exits() -> None:
    from stratal.mon.probes import GpuProbe

    probe = GpuProbe()
    first = probe.read()
    assert first is not None and first.vram_used_mb is not None
    before = first.vram_used_mb
    child = subprocess.run(
        [sys.executable, "-c", _LOAD_AND_EXIT], capture_output=True, text=True, timeout=300
    )
    last = probe.read()
    assert last is not None and last.vram_used_mb is not None
    after = last.vram_used_mb

    assert child.returncode == 0, child.stderr[-2000:]
    assert after <= before + 64, f"NVML used {before} MB before, {after} MB after exit"
