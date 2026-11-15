"""The label and filter stages around the owner's units, with fakes (LABEL-1.1 to 1.4, MON-3.1).

These test the agent's shell: what it reads, caches, writes and refuses. The
owner's units themselves are tested in ``test_LABEL_1_*`` and ``test_RUN_1_4_*``.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord
from stratal.label.captioning import CaptionItem, CaptionReport
from stratal.label.filter_stage import FilterConfig
from stratal.label.filtering import REASON_BELOW_THRESHOLD
from stratal.label.stage import LabelConfig, LabelModels, LabelUnits
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import MemoryDerivationCache
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_FILTERED
from stratal.testing import stagekit
from stratal.testing.fake_clean import FAKE_UNITS as FAKE_DEDUPE
from stratal.testing.fake_models import (
    ConstantSimilarity,
    FakeCaptioner,
    FakeClipEncoder,
    FakeGpu,
    PassthroughVram,
    ScriptedFilter,
    caption_all_at_once,
    fake_caption,
)
from stratal.testing.fakes import ListSink
from stratal.testing.synthetic import make_image_folder


class Killed(Exception):
    pass


class _Models:
    """Fake models that count loads, and a lease that counts takes."""

    def __init__(self) -> None:
        self.captioner_loads = 0
        self.clip_loads = 0
        self.leases = 0

    def models(self) -> LabelModels:
        def captioner(_config: LabelConfig) -> FakeCaptioner:
            self.captioner_loads += 1
            return FakeCaptioner()

        def clip(_config: LabelConfig) -> FakeClipEncoder:
            self.clip_loads += 1
            return FakeClipEncoder()

        return LabelModels(gpu=FakeGpu, load_captioner=captioner, load_clip=clip)

    @contextmanager
    def lease(self) -> Generator[object]:
        self.leases += 1
        yield None


UNITS = LabelUnits(
    caption=caption_all_at_once,
    similarity=ConstantSimilarity(0.25),
    vram=lambda gpu, sink: PassthroughVram(gpu, sink),
)


@pytest.fixture
def preprocessed(tmp_path: Path) -> tuple[Path, VersionRecord]:
    folder = make_image_folder(tmp_path / "src", valid=7, corrupt=1)
    data = tmp_path / "data"
    raw = stagekit.ingest(folder.root, data)
    cleaned = stagekit.clean(data, raw, FAKE_DEDUPE)
    return data, stagekit.preprocess(data, cleaned.record).record


def test_LABEL_1_1_every_accepted_row_gets_a_caption_and_its_model(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    fakes = _Models()

    result = stagekit.label(data, parent, models=fakes.models(), units=UNITS, lease=fakes.lease)
    rows = stagekit.rows_of(data, result.record)

    accepted = [r for r in rows.values() if r["status"] == STATUS_ACCEPTED]
    assert len(accepted) == 7
    blobs = BlobStore(data)
    for row in accepted:
        with Image.open(blobs.path_for(row["derived_sha256"])) as image:
            assert row["caption"] == fake_caption(image.convert("RGB"))
        assert (row["caption_model"], len(row["caption_model_revision"])) == ("florence-2-base", 40)
        assert (row["clip_model"], row["clip_score"]) == ("clip-vit-b-32-laion2b", 0.25)
    rejected = [r for r in rows.values() if r["status"] != STATUS_ACCEPTED]
    assert rejected and all(r["caption"] is None for r in rejected), "other rows are untouched"
    assert result.metrics["rows_labelled"] == 7


def test_LABEL_1_2_a_rerun_takes_everything_from_the_cache_and_never_touches_the_gpu(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    cache = MemoryDerivationCache()
    first = _Models()
    stagekit.label(data, parent, models=first.models(), units=UNITS, cache=cache, lease=first.lease)
    again = _Models()

    result = stagekit.label(
        data, parent, models=again.models(), units=UNITS, cache=cache, lease=again.lease
    )

    assert (first.leases, first.captioner_loads, first.clip_loads) == (2, 1, 1)
    assert (again.leases, again.captioner_loads, again.clip_loads) == (0, 0, 0)
    assert result.metrics["used_gpu"] == 0
    assert result.metrics["caption_cache_hits"] == 7


def test_LABEL_1_2_a_kill_during_scoring_resumes_with_only_the_missing_scores(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    cache = MemoryDerivationCache()
    config = LabelConfig(clip_batch_size=2)
    calls = 0

    def kill_in_the_third_clip_batch() -> None:
        nonlocal calls
        calls += 1
        if calls == 4:  # 1 caption batch, then CLIP batches 1 and 2, then stop
            raise Killed

    with pytest.raises(Killed):
        stagekit.label(
            data,
            parent,
            models=_Models().models(),
            units=UNITS,
            cache=cache,
            config=config,
            checkpoint=kill_in_the_third_clip_batch,
        )
    clip = FakeClipEncoder()
    resumed = _Models()
    models = LabelModels(
        gpu=FakeGpu, load_captioner=lambda _c: FakeCaptioner(), load_clip=lambda _c: clip
    )

    result = stagekit.label(
        data, parent, models=models, units=UNITS, cache=cache, config=config, lease=resumed.lease
    )

    assert result.metrics["captions_computed"] == 0, "captions came from the cache"
    assert result.metrics["clip_computed"] == 3, "only the pairs not scored before the kill"
    assert clip.image_batches == [2, 1]


def test_MON_3_1_labeling_emits_its_messages_and_metrics(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    sink = ListSink()

    result = stagekit.label(data, parent, models=_Models().models(), units=UNITS, sink=sink)

    messages = sink.messages()
    assert "7 images to label: 7 need captions, 0 already captioned" in messages
    assert "CLIP scoring batch 1/1 · CLIP ViT-B/32 · bs=64" in messages
    progress = [e for e in sink.events if e["substage"] == "clip"]
    assert {"images_per_s", "clip_score_running_mean"} <= set(progress[-1]["metrics"])
    assert result.metrics["clip_score_mean"] == 0.25
    assert {"images", "captions_computed", "clip_computed", "images_per_s"} <= set(result.metrics)


def test_LABEL_1_1_a_caption_loop_that_drops_images_fails_the_stage(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed

    def forgetful(items: Sequence[CaptionItem], **kwargs: Any) -> CaptionReport:
        return caption_all_at_once(items[:-1], **kwargs)

    units = LabelUnits(caption=forgetful, similarity=UNITS.similarity, vram=UNITS.vram)
    with pytest.raises(StratalError, match="no caption after captioning"):
        stagekit.label(data, parent, models=_Models().models(), units=units)


def test_LABEL_1_3_a_similarity_that_returns_the_wrong_count_fails_the_stage(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    units = LabelUnits(
        caption=caption_all_at_once,
        similarity=lambda image_vectors, text_vectors: [0.1],
        vram=UNITS.vram,
    )

    with pytest.raises(StratalError, match="clip_similarity returned 1 scores"):
        stagekit.label(data, parent, models=_Models().models(), units=units)


def test_LABEL_1_1_labeling_a_version_without_copies_is_refused(tmp_path: Path) -> None:
    folder = make_image_folder(tmp_path / "src", valid=3)
    data = tmp_path / "data"
    cleaned = stagekit.clean(data, stagekit.ingest(folder.root, data), FAKE_DEDUPE)

    with pytest.raises(StratalError, match="no preprocessed copy"):
        stagekit.label(data, cleaned.record, models=_Models().models(), units=UNITS)


def test_LABEL_1_4_filtered_rows_are_marked_with_a_reason_and_kept(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    labelled = stagekit.label(data, parent, models=_Models().models(), units=UNITS)
    rows = stagekit.rows_of(data, labelled.record)
    victim = sorted(k for k, r in rows.items() if r["status"] == STATUS_ACCEPTED)[0]

    result = stagekit.filter_stage(
        data, labelled.record, rule=ScriptedFilter({victim: REASON_BELOW_THRESHOLD})
    )
    after = stagekit.rows_of(data, result.record)

    assert len(after) == len(rows), "no row was deleted"
    assert after[victim]["status"] == STATUS_FILTERED
    assert after[victim]["reason_code"] == REASON_BELOW_THRESHOLD
    assert after[victim]["decided_at_stage"] == "filter"
    assert "0.2500 is below the threshold 0.2" in after[victim]["reason_detail"]
    assert after[victim]["caption"] == rows[victim]["caption"], "the caption stays"
    assert result.metrics["filtered"] == 1
    assert result.metrics["filter_rate"] == round(1 / 7, 4)


def test_LABEL_1_4_a_rule_that_marks_unknown_rows_or_reasons_fails_the_stage(
    preprocessed: tuple[Path, VersionRecord],
) -> None:
    data, parent = preprocessed
    labelled = stagekit.label(data, parent, models=_Models().models(), units=UNITS)

    with pytest.raises(StratalError, match="not given"):
        stagekit.filter_stage(data, labelled.record, rule=ScriptedFilter({"nope.png": "x"}))
    some_row = next(
        k
        for k, r in stagekit.rows_of(data, labelled.record).items()
        if r["status"] == STATUS_ACCEPTED
    )
    with pytest.raises(StratalError, match="unknown reason codes"):
        stagekit.filter_stage(data, labelled.record, rule=ScriptedFilter({some_row: "blurry"}))


def test_LABEL_1_4_the_threshold_must_be_a_finite_number() -> None:
    with pytest.raises(ValueError):
        FilterConfig(clip_threshold=float("nan"))
