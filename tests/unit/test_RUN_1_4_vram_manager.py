"""RUN-1.4, RUN-1.5, BUG5 on the CPU: the VRAM manager against a fake GPU. An owner unit.

Written as strict expected failures before the unit existed; the markers were
removed on 2026-11-15 when the agent wrote it at the owner's request
(ADR-0012). ``FakeGpu`` frees memory only when the fake weights are
garbage-collected, so a leaked reference behaves as on the card.
The same checks on the real card are in ``tests/gpu/test_BUG5_vram.py``.
"""

import pytest

from stratal.core.errors import VramInsufficient
from stratal.label.vram import VramManager
from stratal.testing.fake_models import FakeCaptioner, FakeClipEncoder, FakeGpu
from stratal.testing.fakes import ListSink


def test_RUN_1_4_unload_returns_allocated_memory_to_baseline() -> None:
    gpu = FakeGpu()
    manager = VramManager(gpu, sink=ListSink())

    model = manager.load(
        lambda: FakeCaptioner(gpu=gpu, weights_mb=900), name="cap", estimate_mb=1200
    )
    assert manager.loaded == "cap"
    assert gpu.allocated_mb() == pytest.approx(900)
    del model
    report = manager.unload()

    assert report.returned_to_baseline
    assert report.after.allocated_mb == pytest.approx(report.baseline_allocated_mb)
    assert gpu.reserved_mb() == pytest.approx(0), "the cache was emptied"
    assert manager.loaded is None


def test_RUN_1_4_only_one_model_at_a_time() -> None:
    gpu = FakeGpu()
    manager = VramManager(gpu, sink=ListSink())
    manager.load(lambda: FakeCaptioner(gpu=gpu), name="cap", estimate_mb=500)
    loaded: list[str] = []

    def second() -> FakeClipEncoder:
        loaded.append("clip")
        return FakeClipEncoder(gpu=gpu)

    with pytest.raises(RuntimeError):
        manager.load(second, name="clip", estimate_mb=500)
    assert loaded == [], "the second loader was never called"


def test_RUN_1_4_a_load_that_does_not_fit_is_refused_before_loading() -> None:
    gpu = FakeGpu(total=2000)
    manager = VramManager(gpu, sink=ListSink())

    with pytest.raises(VramInsufficient) as raised:
        manager.load(lambda: FakeCaptioner(gpu=gpu), name="cap", estimate_mb=5000)

    assert raised.value.suggested_action
    assert gpu.allocated_mb() == 0
    assert manager.loaded is None


def test_BUG5_a_leaked_reference_is_reported_not_hidden() -> None:
    gpu = FakeGpu()
    sink = ListSink()
    manager = VramManager(gpu, sink=sink)
    kept = [
        manager.load(lambda: FakeCaptioner(gpu=gpu, weights_mb=400), name="cap", estimate_mb=600)
    ]
    kept[0].close()  # the weights go, but suppose a list somewhere still holds the adapter...
    extra = [FakeCaptioner(gpu=gpu, weights_mb=120)]  # ...and something else kept tensors alive

    report = manager.unload()

    assert not report.returned_to_baseline
    warnings = [e for e in sink.events if e["level"] == "WARN"]
    assert warnings and warnings[-1]["reason"] and warnings[-1]["suggested_action"]
    assert extra  # held to the end on purpose


def test_RUN_1_4_load_and_unload_report_vram_before_and_after() -> None:
    gpu = FakeGpu()
    sink = ListSink()
    manager = VramManager(gpu, sink=sink)

    manager.load(
        lambda: FakeCaptioner(gpu=gpu, weights_mb=900), name="Florence-2-base", estimate_mb=921.6
    )
    manager.unload()

    messages = sink.messages()
    assert any(m.startswith("Loading Florence-2-base to GPU (0.9 GB)") for m in messages)
    assert any(m.startswith("Unloading Florence-2-base, freeing VRAM") for m in messages)
    loaded = [e for e in sink.events if e["metrics"] and "allocated_mb_after" in e["metrics"]]
    assert loaded, "an event carries allocated memory after loading (MON-3.3)"


def test_RUN_1_4_unload_with_nothing_loaded_is_refused() -> None:
    # Built outside the block, so only unload() can satisfy it.
    manager = VramManager(FakeGpu(), sink=ListSink())

    with pytest.raises(RuntimeError):
        manager.unload()
