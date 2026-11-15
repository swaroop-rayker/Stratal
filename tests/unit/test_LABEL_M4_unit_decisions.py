"""LABEL-1.1 to 1.4, RUN-1.4: the design choices made when the M4 units were written.

The contract tests are in ``test_LABEL_1_1_caption_loop.py``,
``test_LABEL_1_3_clip_similarity.py``, ``test_LABEL_1_4_filter_rule.py`` and
``test_RUN_1_4_vram_manager.py``. These pin what the contracts left open
(recorded in DECISIONS.md): plain cosine, the extra refusals, halving without
growing back, an honest progress total, and how the VRAM manager measures.
"""

import math
from collections.abc import Mapping

import pytest
from PIL import Image

from stratal.label.captioning import CaptionItem, caption_items
from stratal.label.filter_stage import FilterConfig
from stratal.label.filtering import (
    REASON_BELOW_THRESHOLD,
    REASON_MISSING_CAPTION,
    ScoredItem,
    filter_decisions,
)
from stratal.label.scoring import clip_similarity
from stratal.label.vram import TOLERANCE_MB, VramManager
from stratal.testing.fake_models import FakeCaptioner, FakeGpu
from stratal.testing.fakes import ListSink

# -- LABEL-1.3: plain cosine ---------------------------------------------------


def test_LABEL_1_3_the_score_is_plain_cosine_not_rescaled_or_clipped() -> None:
    half_way, opposite = clip_similarity([[1.0, 0.0], [1.0, 0.0]], [[1.0, 1.0], [-3.0, 0.0]])

    assert half_way == pytest.approx(1 / math.sqrt(2))
    assert opposite == -1.0, "negative scores are kept, unlike CLIPScore's max(cos, 0)"


def test_LABEL_1_3_identical_vectors_score_exactly_one_despite_rounding() -> None:
    vectors = [[0.1] * 512, [0.3, -1.7, 2.9e-3, 41.0], [1e-3] * 512]

    for vector in vectors:
        assert clip_similarity([vector], [list(vector)]) == [1.0]
    huge = [1e100, 1e100]  # |v|² is finite, but |a|²·|b|² overflows
    assert clip_similarity([huge], [huge]) == [pytest.approx(1.0)], "the fallback path"


def test_LABEL_1_3_values_that_are_not_finite_are_refused() -> None:
    with pytest.raises(ValueError, match="not finite"):
        clip_similarity([[math.nan, 1.0]], [[1.0, 0.0]])
    with pytest.raises(ValueError, match="not finite"):
        clip_similarity([[1.0, 0.0]], [[math.inf, 0.0]])


def test_LABEL_1_3_every_pair_must_have_the_first_pairs_dimension() -> None:
    with pytest.raises(ValueError, match="pair 1"):
        clip_similarity([[1.0, 0.0], [1.0, 0.0, 0.0]], [[1.0, 0.0], [1.0, 0.0, 0.0]])


# -- LABEL-1.4: the extra refusals --------------------------------------------


def test_LABEL_1_4_a_row_key_given_twice_is_refused() -> None:
    items = [ScoredItem("a.jpg", "a cat", 0.5), ScoredItem("a.jpg", "a cat", 0.1)]

    with pytest.raises(ValueError, match="given twice"):
        filter_decisions(items, threshold=0.2)


def test_LABEL_1_4_a_score_that_is_not_finite_is_refused_not_kept() -> None:
    with pytest.raises(ValueError, match=r"b\.jpg"):
        filter_decisions([ScoredItem("b.jpg", "a cat", math.nan)], threshold=0.2)


def test_LABEL_1_4_a_missing_caption_wins_even_without_a_score() -> None:
    items = [ScoredItem("a.jpg", "", None), ScoredItem("b.jpg", "a dog", 0.1)]

    assert filter_decisions(items, threshold=0.2) == {
        "a.jpg": REASON_MISSING_CAPTION,
        "b.jpg": REASON_BELOW_THRESHOLD,
    }


def test_LABEL_1_4_a_negative_threshold_is_allowed_on_the_cosine_scale() -> None:
    items = [ScoredItem("a.jpg", "x", -0.2), ScoredItem("b.jpg", "x", -0.05)]

    assert filter_decisions(items, threshold=-0.1) == {"a.jpg": REASON_BELOW_THRESHOLD}


def test_LABEL_1_4_default_threshold_is_the_owners_choice() -> None:
    assert FilterConfig().clip_threshold == 0.2, "OC-5, kept by the owner 2026-11-15"


# -- LABEL-1.1, BUG2: halving, no regrowth, an honest total -------------------


def _items(count: int) -> list[CaptionItem]:
    return [CaptionItem(key=f"{i:064x}", row_key=f"img_{i}.jpg") for i in range(count)]


class _Opener:
    def __init__(self) -> None:
        self.opened = 0

    def __call__(self, item: CaptionItem) -> Image.Image:
        self.opened += 1
        return Image.new("RGB", (32, 32), (200, 30, 30))


def _caption(
    count: int, *, batch_size: int, oom_above: int | None
) -> tuple[FakeCaptioner, ListSink, _Opener, list[Mapping[str, str]]]:
    captioner = FakeCaptioner(oom_above=oom_above)
    sink = ListSink()
    opener = _Opener()
    commits: list[Mapping[str, str]] = []
    caption_items(
        _items(count),
        captioner=captioner,
        batch_size=batch_size,
        open_image=opener,
        commit=commits.append,
        sink=sink,
        checkpoint=lambda: None,
    )
    return captioner, sink, opener, commits


def test_BUG2_the_batch_is_halved_and_never_grows_back() -> None:
    captioner, _, _, commits = _caption(30, batch_size=16, oom_above=5)

    assert captioner.batch_sizes[:3] == [16, 8, 4], "16 fails, 8 fails, 4 fits"
    successful = [len(batch) for batch in commits]
    assert successful == [4, 4, 4, 4, 4, 4, 4, 2], "it stays at 4, even where 5 would fit"


def test_BUG2_only_the_failed_batch_is_reopened() -> None:
    _, _, opener, _ = _caption(20, batch_size=16, oom_above=4)

    assert opener.opened == 20 + 16 + 8, "each failed attempt reopens its own images only"


def test_BUG2_the_progress_total_follows_the_new_batch_size() -> None:
    _, sink, _, _ = _caption(20, batch_size=8, oom_above=4)

    totals = [e["total"] for e in sink.events if e["step"] is not None]
    assert totals == [5, 5, 5, 5, 5], "8 failed at once, so 20 images at 4 a batch is 5 batches"
    warning = next(e for e in sink.events if e["level"] == "WARN")
    assert "caption_batch_size to 4" in warning["suggested_action"]


def test_LABEL_1_1_the_report_says_what_happened() -> None:
    _, sink, _, _ = _caption(10, batch_size=8, oom_above=4)

    first = sink.events[0]
    assert first["message"] == "Captioning 10 images · Fake captioner · bs=8 · fp16"
    last = [e for e in sink.events if e["step"] is not None][-1]
    assert last["metrics"]["captioned"] == 10


# -- RUN-1.4: how the VRAM manager measures -----------------------------------


def test_RUN_1_4_cached_blocks_count_as_free_when_checking_a_load() -> None:
    gpu = FakeGpu(total=2000, cached=1200)  # free = 2000 - 90 - 1200 = 710 before emptying
    manager = VramManager(gpu, sink=ListSink())

    manager.load(lambda: FakeCaptioner(gpu=gpu, weights_mb=500), name="cap", estimate_mb=1500)

    assert gpu.calls[:2] == ["synchronize", "empty_cache"], "the cache is emptied before measuring"
    assert manager.loaded == "cap"


def test_RUN_1_4_a_loader_that_fails_leaves_nothing_loaded() -> None:
    gpu = FakeGpu()
    manager = VramManager(gpu, sink=ListSink())

    def broken() -> FakeCaptioner:
        FakeCaptioner(gpu=gpu, weights_mb=300)  # half-built, then dropped
        raise OSError("weights file truncated")

    with pytest.raises(OSError):
        manager.load(broken, name="cap", estimate_mb=100)

    assert manager.loaded is None
    assert gpu.allocated_mb() == 0 and gpu.reserved_mb() == 0
    manager.load(lambda: FakeCaptioner(gpu=gpu), name="next", estimate_mb=100)


def test_RUN_1_4_less_than_the_tolerance_left_over_still_counts_as_freed() -> None:
    gpu = FakeGpu()
    manager = VramManager(gpu, sink=ListSink())
    manager.load(lambda: FakeCaptioner(gpu=gpu, weights_mb=400), name="cap", estimate_mb=500)
    gpu.allocated += TOLERANCE_MB / 2  # e.g. a rounding remainder

    assert manager.unload().returned_to_baseline


def test_RUN_1_4_an_estimate_that_is_not_a_number_of_mb_is_refused() -> None:
    manager = VramManager(FakeGpu(), sink=ListSink())

    for estimate in (-1.0, math.nan):
        with pytest.raises(ValueError):
            manager.load(lambda: FakeCaptioner(), name="cap", estimate_mb=estimate)
