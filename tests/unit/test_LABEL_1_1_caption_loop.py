"""LABEL-1.1, LABEL-1.2, BUG2: the caption batching loop. An owner unit.

Strict expected failures until ``stratal.label.captioning.caption_items`` is
written (ADR-0012); then remove the marker.
"""

import re
from collections.abc import Callable, Mapping

import pytest
from PIL import Image

from stratal.core.errors import GpuOom
from stratal.label.captioning import CaptionItem, caption_items
from stratal.testing.fake_models import FakeCaptioner, fake_caption
from stratal.testing.fakes import ListSink

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="owner unit LABEL-1.1 not written yet"
)

COLOURS = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (128, 128, 128)]


class Killed(Exception):
    pass


def _items(count: int) -> list[CaptionItem]:
    return [CaptionItem(key=f"{i:064x}", row_key=f"img_{i:05d}.jpg") for i in range(count)]


def _image(item: CaptionItem) -> Image.Image:
    index = int(item.key, 16)
    return Image.new("RGB", (64 + index, 48), COLOURS[index % len(COLOURS)])


class _Store:
    def __init__(self) -> None:
        self.batches: list[dict[str, str]] = []

    def __call__(self, captions: Mapping[str, str]) -> None:
        self.batches.append(dict(captions))

    @property
    def all(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for batch in self.batches:
            merged.update(batch)
        return merged


def _run(
    items: list[CaptionItem],
    captioner: FakeCaptioner,
    *,
    batch_size: int = 4,
    store: _Store | None = None,
    sink: ListSink | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[_Store, ListSink]:
    store = store or _Store()
    sink = sink or ListSink()

    def no_stop() -> None:
        return None

    caption_items(
        items,
        captioner=captioner,
        batch_size=batch_size,
        open_image=_image,
        commit=store,
        sink=sink,
        checkpoint=checkpoint or no_stop,
    )
    return store, sink


def test_LABEL_1_1_every_item_gets_its_caption_committed_per_batch() -> None:
    items = _items(10)
    captioner = FakeCaptioner()

    store, _ = _run(items, captioner, batch_size=4)

    assert store.all == {item.key: fake_caption(_image(item)) for item in items}
    assert [len(batch) for batch in store.batches] == [4, 4, 2]
    assert captioner.batch_sizes == [4, 4, 2]


def test_LABEL_1_1_an_empty_list_calls_nothing() -> None:
    captioner = FakeCaptioner()

    store, _ = _run([], captioner)

    assert (store.batches, captioner.batch_sizes) == ([], [])


def test_LABEL_1_1_progress_names_batch_model_size_and_precision() -> None:
    _, sink = _run(_items(8), FakeCaptioner(), batch_size=4)

    progress = [e for e in sink.events if e["step"] is not None]
    assert [(e["step"], e["total"]) for e in progress] == [(1, 2), (2, 2)]
    assert re.fullmatch(
        r"Captioning batch 2/2 · Fake captioner · bs=4 · fp16", progress[-1]["message"]
    )
    assert {"images_per_s", "batch_size"} <= set(progress[-1]["metrics"])


def test_LABEL_1_2_a_kill_at_60_percent_loses_nothing_and_repeats_nothing() -> None:
    items = _items(50)
    captioner = FakeCaptioner()
    store = _Store()
    calls = 0

    def kill_after_three_batches() -> None:
        nonlocal calls
        calls += 1
        if calls == 4:  # before the fourth batch of ten: 60 % done
            raise Killed

    with pytest.raises(Killed):
        _run(items, captioner, batch_size=10, store=store, checkpoint=kill_after_three_batches)
    assert len(store.all) == 30

    remaining = [item for item in items if item.key not in store.all]  # what the stage passes next
    _run(remaining, captioner, batch_size=10, store=store)

    assert len(store.all) == 50
    assert len(captioner.seen) == 50, "no image was captioned twice"


def test_BUG2_an_oversized_batch_warns_with_a_smaller_batch_and_completes() -> None:
    items = _items(20)
    captioner = FakeCaptioner(oom_above=4)

    store, sink = _run(items, captioner, batch_size=16)

    assert len(store.all) == 20
    warnings = [e for e in sink.events if e["level"] == "WARN"]
    assert warnings, "the reduction is announced"
    assert all(e["reason"] and e["suggested_action"] for e in warnings)
    assert any(re.search(r"\b(8|4)\b", e["suggested_action"] or "") for e in warnings)
    assert max(size for size in captioner.batch_sizes[-3:]) <= 4


def test_BUG2_when_one_image_does_not_fit_the_stage_fails_with_gpu_oom() -> None:
    with pytest.raises(GpuOom) as raised:
        _run(_items(3), FakeCaptioner(oom_above=0), batch_size=2)

    assert raised.value.suggested_action


def test_LABEL_1_1_a_batch_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        _run(_items(3), FakeCaptioner(), batch_size=0)
