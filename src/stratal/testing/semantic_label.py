"""A label stage whose CLIP vectors follow each image's mean colour, with the real
semantic clusters (DATA-2.3) — for pipeline tests of DATA-2.3 and DATA-4.8 without a GPU.

Name it in ``STRATAL_EXTRA_STAGES`` after ``fake_label``. An image's vector is its mean
red, green and blue minus 128, so a crop or a re-patterned copy of one scene points the
same way while scenes of other colours point elsewhere. Captions and scores stay fake.
"""

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageStat

from stratal.label.models import Vector
from stratal.label.stage import LabelConfig, LabelModels, LabelUnits
from stratal.runtime.stages import StageDef, make_label_runner, register
from stratal.testing.fake_models import (
    ConstantSimilarity,
    FakeCaptioner,
    FakeClipEncoder,
    FakeGpu,
    PassthroughVram,
    caption_all_at_once,
)


class MeanColourClip(FakeClipEncoder):
    def encode_images(self, images: Sequence[Image.Image]) -> list[Vector]:
        self.image_batches.append(len(images))
        return [[m - 128.0 for m in ImageStat.Stat(image.convert("RGB")).mean] for image in images]


UNITS = LabelUnits(
    caption=caption_all_at_once,
    similarity=ConstantSimilarity(0.5),
    vram=lambda gpu, sink: PassthroughVram(gpu, sink),
)


def models(_data_dir: Path) -> LabelModels:
    return LabelModels(
        gpu=FakeGpu,
        load_captioner=lambda _config: FakeCaptioner(),
        load_clip=lambda _config: MeanColourClip(),
        library_versions={"fake": "mean-colour"},
    )


register(StageDef("label", "cpu", LabelConfig, make_label_runner(models, UNITS)))
