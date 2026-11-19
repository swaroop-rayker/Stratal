# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""RUN-1.4.AC1 for training, on the real card: after a few Adam steps, unloading the
training state brings ``torch.cuda.memory_allocated`` back to its baseline.

Run in the GPU image:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

import pytest

from stratal.label.models import ModelInfo
from stratal.label.vram import VramManager
from stratal.testing.fakes import ListSink
from stratal.train.memory import TrainingState

pytestmark = pytest.mark.gpu


def test_RUN_1_4_memory_allocated_returns_to_baseline_after_training() -> None:
    import torch
    from torch import nn

    from stratal.label.hf import TorchGpu, set_vram_cap

    set_vram_cap(0.85)
    manager = VramManager(TorchGpu(), sink=ListSink(), stage="train")
    baseline = torch.cuda.memory_allocated()

    def build() -> TrainingState:
        model = nn.Sequential(
            nn.Conv2d(3, 64, 3, padding=1),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(64 * 32 * 32, 256),
            nn.ReLU(),
            nn.Linear(256, 10),
        ).cuda()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        info = ModelInfo(name="test_cnn", revision="test", display="test CNN", dtype="fp32")
        return TrainingState(model, optimizer, info=info)

    state = manager.load(build, name="test CNN", estimate_mb=600)
    for _ in range(3):  # gradients and Adam's two running averages now exist on the card
        images = torch.randn(32, 3, 32, 32, device="cuda")
        labels = torch.randint(0, 10, (32,), device="cuda")
        loss = nn.functional.cross_entropy(state.model(images), labels)
        state.optimizer.zero_grad()
        loss.backward()
        state.optimizer.step()
        del images, labels, loss
    assert torch.cuda.memory_allocated() > baseline + 200 * 1024 * 1024
    del state  # the test keeps no reference; only the manager holds the state

    report = manager.unload()

    assert report.returned_to_baseline, (
        f"{report.after.allocated_mb - report.baseline_allocated_mb:.1f} MB still held"
    )
    assert torch.cuda.memory_allocated() <= baseline + 1024 * 1024
