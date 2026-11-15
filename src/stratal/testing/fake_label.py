"""Replaces the label and filter stages' models and owner units with fakes.

Name this module in ``STRATAL_EXTRA_STAGES`` to run whole pipelines through
labeling on a machine without a GPU and before the owner's M4 units exist. The
label stage then captions with :class:`FakeCaptioner`, scores every pair 0.5,
and the filter marks nothing.
"""

from pathlib import Path

from stratal.label.filter_stage import FilterConfig
from stratal.label.stage import LabelConfig, LabelModels, LabelUnits
from stratal.runtime.stages import StageDef, make_filter_runner, make_label_runner, register
from stratal.testing.fake_models import (
    ConstantSimilarity,
    FakeCaptioner,
    FakeClipEncoder,
    FakeGpu,
    PassthroughVram,
    caption_all_at_once,
    filter_nothing,
)

FAKE_UNITS = LabelUnits(
    caption=caption_all_at_once,
    similarity=ConstantSimilarity(0.5),
    vram=lambda gpu, sink: PassthroughVram(gpu, sink),
)


def fake_models(_data_dir: Path) -> LabelModels:
    return LabelModels(
        gpu=FakeGpu,
        load_captioner=lambda _config: FakeCaptioner(),
        load_clip=lambda _config: FakeClipEncoder(),
        library_versions={"fake": "1"},
    )


register(StageDef("label", "cpu", LabelConfig, make_label_runner(fake_models, FAKE_UNITS)))
register(StageDef("filter", "cpu", FilterConfig, make_filter_runner(filter_nothing)))
