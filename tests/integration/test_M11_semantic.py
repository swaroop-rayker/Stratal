"""DATA-2.3 and DATA-4.8 (M11, ADR-0013) through a pipeline job, against the real
PostgreSQL and Redis, without a GPU.

The label stage is ``stratal.testing.semantic_label``: its CLIP vectors follow each
image's mean colour, and the clustering and the split are the real units. One red scene
is drawn three ways: stripes, a crop of the stripes and a checkerboard. The checkerboard
is far from the stripes by pHash, as two photos of one scene from different angles are,
and close by "CLIP". Five other scenes point along the other colour axes. Real CLIP
vectors of crops and of a warped view were measured in spike S-10 (0.90: 78 % of 80 %
crops found; the warped opposite-corner pair at a median of 0.924).
"""

import random
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.dedupe import perceptual_hash
from stratal.db.engine import get_engine
from stratal.runtime import stages as stage_registry
from stratal.testing.harness import Runner
from stratal.testing.pipeline import FAKE_MODULES, PipelineApi, restore_real_units

pytestmark = pytest.mark.integration

SIZE = 96


def _scene(pattern: str) -> Image.Image:
    """Red and white, half each, so the mean is (255, 128, 128) whatever the pattern."""
    image = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    pixels = image.load()
    assert pixels is not None
    for x in range(SIZE):
        for y in range(SIZE):
            red = (x // 8) % 2 == 0 if pattern == "stripes" else ((x // 12) + (y // 12)) % 2 == 0
            if red:
                pixels[x, y] = (255, 0, 0)
    return image


def _other(colour: tuple[int, int, int], seed: int) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = image.load()
    assert pixels is not None
    for x in range(SIZE):
        for y in range(SIZE):
            pixels[x, y] = tuple(max(0, min(255, c + rng.randint(-40, 40))) for c in colour)
    return image


@pytest.fixture
def semantic_stages(monkeypatch: pytest.MonkeyPatch, bus: Any) -> Any:
    modules = [m for m in FAKE_MODULES if not m.endswith(("fake_split", "fake_label"))]
    monkeypatch.setenv(
        "STRATAL_EXTRA_STAGES", ",".join([*modules, "stratal.testing.semantic_label"])
    )
    stage_registry.load_extra_stages()
    yield
    restore_real_units()


def test_DATA_2_3_cropped_variants_are_grouped_and_reported_and_DATA_4_8_one_split(
    semantic_stages: Any, runner: Runner, tmp_path: Path
) -> None:
    source = tmp_path / "scenes"
    source.mkdir()
    stripes = _scene("stripes")
    stripes.save(source / "scene_stripes.png")
    crop = stripes.crop((0, 0, int(SIZE * 0.8), int(SIZE * 0.8)))
    crop.resize((SIZE, SIZE)).save(  # pyright: ignore[reportUnknownMemberType]
        source / "scene_stripes_crop80.png"
    )
    _scene("checker").save(source / "scene_checker.png")
    for i, colour in enumerate(
        [(0, 128, 128), (128, 255, 128), (128, 0, 128), (128, 128, 255), (128, 128, 0)]
    ):
        _other(colour, seed=i).save(source / f"other_{i}.png")
    far = (
        int(perceptual_hash(stripes, hash_size=8), 16)
        ^ int(perceptual_hash(_scene("checker"), hash_size=8), 16)
    ).bit_count()
    assert far > 12, f"the scene's two views must be far apart by pHash, got {far} bits"

    body = {
        "dataset": "semantic",
        "source": {"path": str(source)},
        "stages": {
            "ingest": {"min_size": 32},
            "split": {"train": 0.6, "val": 0.2, "test": 0.2},
        },
    }
    job = PipelineApi(runner).run(body)
    assert job["state"] == "completed", job

    label_id = next(s for s in job["stages"] if s["name"] == "label")["output_version_id"]
    with Session(get_engine()) as session:
        label = registry.get_version(session, label_id)
        label_rows = {
            r["row_key"]: r
            for r in registry.load_version(session, get_settings().data_dir, label_id)
        }
        split_rows = {
            r["row_key"]: r
            for r in registry.load_version(
                session, get_settings().data_dir, job["output_version_id"]
            )
        }
    report = label.counts["semantic"]
    assert (report["clusters"], report["rows_in_clusters"], report["largest"]) == (1, 3, 3), (
        "AC1: the report"
    )
    scene = ["scene_checker.png", "scene_stripes.png", "scene_stripes_crop80.png"]
    clusters = {label_rows[k]["semantic_cluster_id"] for k in scene}
    assert len(clusters) == 1 and None not in clusters, "AC1: cropped variants grouped together"
    assert all(label_rows[f"other_{i}.png"]["semantic_cluster_id"] is None for i in range(5))
    assert all(label_rows[k]["status"] == "accepted" for k in scene), "mark keeps every member"
    assert report["examples"] == [{"cluster_id": clusters.pop(), "rows": 3}]

    assert len({split_rows[k]["split"] for k in scene}) == 1, "DATA-4.8.AC1: one split"
    assert len({split_rows[k]["split_group"] for k in scene}) == 1

    # SCR-03 v2 frame C: the filter and the cluster's drawer
    client = PipelineApi(runner).client
    in_cluster = client.get(f"/api/v1/versions/{label_id}/rows", params={"semantic": "true"}).json()
    assert sorted(r["row_key"] for r in in_cluster["items"]) == scene
    cluster_id = in_cluster["items"][0]["semantic_cluster_id"]
    view = client.get(f"/api/v1/versions/{job['output_version_id']}/clusters/{cluster_id}").json()
    assert sorted(r["row_key"] for r in view["rows"]) == scene
    assert len(view["pairs"]) == 3 and all(p["similarity"] > 0.99 for p in view["pairs"])
    checker = next(p for p in view["pairs"] if "scene_checker.png" in (p["a"], p["b"]))
    assert checker["phash_distance"] > 12
    assert len(view["split_groups"]) == 1
