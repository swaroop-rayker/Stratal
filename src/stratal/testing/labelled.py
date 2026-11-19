"""A small labelled-images split for tests of training (M8), built through the real pipeline.

Two class folders of distinct synthetic images go through a ``labelled_images``
pipeline (with the owner's units faked, see :mod:`stratal.testing.pipeline`), so
the version a test trains on is made exactly as a real one is.
"""

from pathlib import Path

from stratal.testing.harness import Runner
from stratal.testing.pipeline import PipelineApi
from stratal.testing.synthetic import make_image_folder


def class_folders(root: Path, *, per_class: int = 20, size: int = 32) -> Path:
    """``cat/`` and ``dog/`` under ``root``, ``per_class`` images each, all ``size`` px."""
    for seed, label in enumerate(("cat", "dog"), start=1):
        make_image_folder(root / label, valid=per_class, size=size, seed=seed)
    return root


def labelled_split(
    runner: Runner, root: Path, slug: str, *, per_class: int = 20, size: int = 32
) -> str:
    """Run a labelled-images pipeline over fresh class folders; returns the split version."""
    job = PipelineApi(runner).run(
        {
            "dataset": slug,
            "source": {"path": str(class_folders(root, per_class=per_class, size=size))},
            "dataset_type": "labelled_images",
            "stages": {"ingest": {"min_size": 32}},
        }
    )
    assert job["state"] == "completed", job
    return str(job["output_version_id"])
