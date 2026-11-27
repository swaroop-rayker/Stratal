"""LLM-2.4 (ADR-0022 rule 12): image search over a version's CLIP image vectors, with FAISS
and the real derivation cache; the text tower is replaced by a fake that returns a known
vector, so planted images must come back first (marker ``ml``). The live search ("dogs at
night" on coco10k) is judged by the owner."""
# pyright: basic, reportMissingImports=false

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.errors import NoImageEmbeddings
from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.label.semantic import pack_vector
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.storage.derivations import KIND_CLIP_IMAGE, PgDerivationCache
from stratal.storage.manifest import iter_rows
from stratal.testing.harness import Runner
from stratal.testing.labelled import class_folders

pytestmark = [pytest.mark.integration, pytest.mark.ml]
np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from stratal.inference.image_search import ImageSearch  # noqa: E402

DIM = 512


def basis(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i % DIM] = 1.0
    return vector


class FakeText:
    """Maps a query to a known direction: "image 3" points at the third planted image."""

    def __init__(self, _models: Path) -> None:
        pass

    def embed(self, text: str) -> Any:
        number = int(text.split()[-1])
        mixed = np.asarray(basis(number), dtype=np.float32) * 0.9
        mixed += np.asarray(basis(number + 1), dtype=np.float32) * 0.1
        return mixed / np.linalg.norm(mixed)


def _version(runner: Runner, tmp_path: Path) -> str:
    body = {
        "kind": "pipeline",
        "dataset": "imgs",
        "source": {"path": str(class_folders(tmp_path / "src", per_class=6, size=32))},
        "dataset_type": "labelled_images",
        "stages": {"ingest": {"min_size": 32}},
        "until_stage": "preprocess",
    }
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_pipeline_job(session, PipelineRequest.model_validate(body))
        job_id = job.id
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    done = runner.job(job_id)
    assert done["state"] == "completed", done
    return str(done["stages"][-1]["output_version_id"])


def test_LLM_2_4_planted_images_are_found_from_their_text(runner: Runner, tmp_path: Path) -> None:
    version = _version(runner, tmp_path)
    with Session(get_engine()) as session:
        _, manifest = registry.manifest_for(session, get_settings().data_dir, version)
    rows = sorted(
        (r for r in iter_rows(manifest) if r["status"] == "accepted" and r["derived_sha256"]),
        key=lambda r: str(r["row_key"]),
    )
    search = ImageSearch(
        engine=get_engine(), data_dir=get_settings().data_dir, text_factory=FakeText
    )
    with pytest.raises(NoImageEmbeddings, match="no CLIP image vectors"):
        list(search.search("image 3", version))
    PgDerivationCache(get_engine()).put_many(
        KIND_CLIP_IMAGE,
        "planted",
        {str(r["derived_sha256"]): pack_vector(basis(i)) for i, r in enumerate(rows)},
    )
    search = ImageSearch(
        engine=get_engine(), data_dir=get_settings().data_dir, text_factory=FakeText
    )
    events = list(search.search("image 3", version, 3))
    assert [e["event"] for e in events] == ["meta", "images", "done"]
    assert events[0]["route"] == "images" and events[0]["device"] == "cpu"
    found = events[1]["images"]
    assert [f["row_key"] for f in found[:2]] == [rows[3]["row_key"], rows[4]["row_key"]]
    assert found[0]["similarity"] > found[1]["similarity"] > found[2]["similarity"]
    assert events[1]["searched"] == len(rows)
