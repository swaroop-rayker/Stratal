"""MON-3.3 (ADR-0020 rule 9, K6): a model unload shows VRAM before and after, PyTorch's
allocated memory and the card's figure, as typed events."""

import gc

import pytest

from stratal.label.vram import VramManager
from stratal.testing.fake_models import FakeCaptioner, FakeGpu
from stratal.testing.fakes import ListSink


def test_MON_3_3_an_unload_event_shows_vram_before_and_after() -> None:
    gpu = FakeGpu()
    sink = ListSink()
    manager = VramManager(gpu, sink=sink)
    model = manager.load(
        lambda: FakeCaptioner(gpu=gpu, weights_mb=1024), name="Florence-2-base", estimate_mb=1500
    )
    del model
    gc.collect()
    manager.unload()

    loaded = [e for e in sink.events if e["type"] == "model_loaded"]
    unloaded = [e for e in sink.events if e["type"] == "model_unloaded"]
    assert len(loaded) == 1 and len(unloaded) == 1
    m = unloaded[0]["metrics"]
    assert m["allocated_mb_before"] == pytest.approx(1024)
    assert m["allocated_mb_after"] == pytest.approx(0)
    assert m["card_used_mb_before"] > m["card_used_mb_after"]
    assert m["card_used_mb_after"] == pytest.approx(gpu.context), "the context stays"
    assert "VRAM 1.00 → 0.00 GB (PyTorch), card" in unloaded[0]["message"], "MON-3.3.AC1"
    assert "back to its baseline" in unloaded[0]["message"], "RUN-1.4.AC1 still said"
    announced = [e for e in sink.events if e["message"].startswith("Unloading")]
    assert announced[0]["type"] == "activity"
