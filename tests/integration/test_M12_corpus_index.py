"""DATA-5.3 and LLM-2.1 (ADR-0022 rules 1 to 4): a text corpus through ingest, clean_text,
chunk and index, with the real tokenizer, embedder and FAISS (marker ``ml``).

Runs in the worker-cpu image, which has sentence-transformers and faiss-cpu; the
allow-listed bge files come from the real data volume's ``models`` folder. check.ps1,
step "integration (ml)".
"""
# pyright: basic, reportMissingImports=false

import json
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.db.engine import get_engine
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import iter_rows, read_metadata
from stratal.testing.harness import Runner

pytestmark = [pytest.mark.integration, pytest.mark.ml]
pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")

from stratal.data.rag_index import BgeEmbedder, index_of, load_index  # noqa: E402

REAL_MODELS = Path("/data/models")

DOCS = {
    "DATA_MODEL.md": """# Stratal — Data Model

How data is kept.

## 3. Identifiers

A dataset version ID is ds_ followed by twelve hex characters of a SHA-256 over the content
hash of its rows and the hash of its config. The same rows and config always give the same ID.

## 6. Blob store

Every file is stored once under the SHA-256 of its bytes. Blobs are read-only after writing.
""",
    "decisions/ADR-0008-sleep.md": """# ADR-0008 — Sleep and suspend handling

## Decision

When the laptop sleeps, the whole Linux VM is suspended. A running job notices the gap in
its heartbeat when it wakes, and the watchdog marks it stalled if it does not continue.
""",
    "spikes/S-11-results.json": "{}",
}


@pytest.fixture(autouse=True)
def models() -> None:
    if not (REAL_MODELS / "bge-small-en-v1.5").is_dir():
        pytest.skip("bge-small-en-v1.5 is not pulled (models pull bge-small-en-v1.5)")
    link = get_settings().data_dir / "models"
    if not link.exists():
        os.symlink(REAL_MODELS, link)


def _run(runner: Runner, body: dict[str, Any]) -> dict[str, Any]:
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_pipeline_job(session, PipelineRequest.model_validate(body))
        job_id = job.id
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    return runner.job(job_id)


def _corpus(root: Path) -> Path:
    for name, text in DOCS.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def _manifest(version_id: str) -> Path:
    with Session(get_engine()) as session:
        return registry.manifest_for(session, get_settings().data_dir, version_id)[1]


def _versions(job: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["output_version_id"] for s in job["stages"] if s["output_version_id"]}


def test_LLM_2_1_corpus_is_chunked_and_indexed(runner: Runner, tmp_path: Path) -> None:
    job = _run(
        runner,
        {
            "kind": "pipeline",
            "dataset": "docs",
            "source": {"path": str(_corpus(tmp_path / "docs"))},
            "dataset_type": "text_corpus",
            "stages": {"chunk": {"chunk_tokens": 64, "overlap_tokens": 8, "min_tokens": 4}},
        },
    )
    assert job["state"] == "completed", job
    versions = _versions(job)
    assert list(versions) == ["ingest", "clean_text", "chunk", "index"]

    documents = {r["row_key"]: r for r in iter_rows(_manifest(versions["clean_text"]))}
    assert documents["spikes/S-11-results.json"]["reason_code"] == "unsupported_format"
    assert "\n## 3. Identifiers" in documents["DATA_MODEL.md"]["text"]  # lines kept (ADR-0022)

    chunks = [r for r in iter_rows(_manifest(versions["index"])) if r["status"] == "accepted"]
    sections = {r["section"] for r in chunks}
    assert "Stratal — Data Model › 3. Identifiers" in sections
    assert "ADR-0008 — Sleep and suspend handling › Decision" in sections
    for row in chunks:
        source = documents[row["document"]]["text"]
        assert source[row["char_start"] : row["char_end"]] == row["text"]
        assert 0 < row["token_count"] <= 64

    described = index_of(_manifest(versions["index"]))
    assert described is not None
    assert described["count"] == len(chunks) and described["dimension"] == 384
    assert read_metadata(_manifest(versions["index"]))["stratal.stage"] == "index"
    index = load_index(
        BlobStore(get_settings().data_dir).path_for(described["sha256"]).read_bytes()
    )
    embedder = BgeEmbedder(get_settings().data_dir / "models")
    query: Any = embedder.embed(
        [described["query_prefix"] + "How is a dataset version ID computed?"]
    )
    scores, positions = index.search(query, 3)
    ordered = sorted(chunks, key=lambda r: r["row_key"])
    best = ordered[int(positions[0][0])]
    assert best["section"] == "Stratal — Data Model › 3. Identifiers"
    assert 0.5 < float(scores[0][0]) <= 1.0001


def test_LLM_2_1_another_chunk_size_is_another_version_and_reuses_vectors(
    runner: Runner, tmp_path: Path
) -> None:
    source = str(_corpus(tmp_path / "docs"))
    base = {
        "kind": "pipeline",
        "dataset": "docs2",
        "source": {"path": source},
        "dataset_type": "text_corpus",
    }
    first = _run(runner, {**base, "stages": {"chunk": {"chunk_tokens": 64}}})
    again = _run(runner, {**base, "stages": {"chunk": {"chunk_tokens": 64}}})
    other = _run(runner, {**base, "stages": {"chunk": {"chunk_tokens": 128}}})
    assert _versions(first)["index"] == _versions(again)["index"]  # memoized
    assert _versions(other)["chunk"] != _versions(first)["chunk"]
    assert _versions(other)["index"] != _versions(first)["index"]
    counts = json.dumps(runner.job(other["id"])["stages"][-1].get("metrics") or {})
    assert counts  # the index stage reports how many vectors came from the cache
