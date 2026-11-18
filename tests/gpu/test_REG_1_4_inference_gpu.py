# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the GPU image, so the type checker cannot see it here.
"""REG-1.4 on the real card: an inference model, held without an optimizer, unloads back
to the baseline through the owner's ``TrainingState.close`` (ADR-0016 rule 3).

A strict expected failure until the owner writes ``TrainingState.close`` (RUN-1.4,
its contract's rule 6). Until then the service serves on the CPU only. Run in the
GPU image:
    docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu
"""

import pytest

from stratal.label.models import ModelInfo
from stratal.label.vram import VramManager
from stratal.testing.fakes import ListSink
from stratal.train.memory import TrainingState

pytestmark = pytest.mark.gpu


@pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="owner unit")
def test_REG_1_4_an_inference_model_unloads_to_baseline_on_the_card() -> None:
    import torch

    from stratal.inference.holder import TorchGpuSide, close_written, prepare_input
    from stratal.train.config import ModelSpec
    from stratal.train.reference import build_model

    side = TorchGpuSide()
    side.prepare()
    manager = VramManager(side.backend(), sink=ListSink(), stage="inference")
    baseline = torch.cuda.memory_allocated()
    holder = [build_model(ModelSpec(name="small_cnn"), (3, 32, 32), 10).eval()]
    info = ModelInfo("small_cnn", "mv_test", "small_cnn", "fp32")

    state = manager.load(
        lambda: TrainingState(side.to_device(holder.pop()), None, info=info),
        name="small_cnn",
        estimate_mb=300,
    )
    from PIL import Image

    with torch.no_grad():
        state.model(prepare_input(Image.new("RGB", (32, 32)), (3, 32, 32)).cuda())
    del state
    report = manager.unload()

    assert report.returned_to_baseline, report
    assert torch.cuda.memory_allocated() == baseline
    assert close_written()
