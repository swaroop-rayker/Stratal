"""LLM-2.1 and LLM-2.3 (ADR-0022 rules 5 to 8): RAG with a real index, embedder and the real
retrieval settings, the LLM answered by a fake Ollama (marker ``ml``). The live model is
exercised by the demo and NFR-6's test."""
# pyright: basic, reportMissingImports=false

import os
import socket
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from stratal.core.settings import get_settings
from stratal.data.retrieval_settings import RetrievalValues, write_retrieval
from stratal.db.engine import get_engine
from stratal.inference.llm import LlmAdapter
from stratal.pretrained.catalog import STAMP, get_model, model_dir
from stratal.runtime import jobs as job_service
from stratal.runtime.jobs import PipelineRequest
from stratal.testing.fake_llm import FakeCard, FakeOllama
from stratal.testing.fakes import ListSink
from stratal.testing.harness import Runner

pytestmark = [pytest.mark.integration, pytest.mark.ml]
pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")

from stratal.inference.rag import RagService, Retriever  # noqa: E402

REAL_MODELS = Path("/data/models")
DOCS = {
    "DATA_MODEL.md": "# Stratal — Data Model\n\n## 3. Identifiers\n\nA dataset version ID is ds_ "
    "followed by twelve hex characters of a SHA-256 over the content hash of its rows and the "
    "hash of its config.\n\n## 6. Blob store\n\nEvery file is stored once under the SHA-256 of "
    "its bytes.\n",
    "ADR-0008.md": "# ADR-0008 — Sleep\n\n## Decision\n\nWhen the laptop sleeps the Linux VM is "
    "suspended, and a running job notices the gap in its heartbeat when it wakes.\n",
}


class Owner:
    def claim_gpu_for_llm(self) -> None:
        pass

    def note_gpu_use(self) -> None:
        pass


@pytest.fixture
def index_version(runner: Runner, tmp_path: Path) -> str:
    if not (REAL_MODELS / "bge-small-en-v1.5").is_dir():
        pytest.skip("bge-small-en-v1.5 is not pulled")
    link = get_settings().data_dir / "models"
    if not link.exists():
        os.symlink(REAL_MODELS, link)
    source = tmp_path / "docs"
    source.mkdir()
    for name, text in DOCS.items():
        (source / name).write_text(text, encoding="utf-8")
    body = {
        "kind": "pipeline",
        "dataset": "ragdocs",
        "source": {"path": str(source)},
        "dataset_type": "text_corpus",
    }
    with Session(get_engine()) as session, session.begin():
        job, first = job_service.create_pipeline_job(session, PipelineRequest.model_validate(body))
        job_id = job.id
    runner.queue.append(first)
    runner.run_queued()
    runner.collect()
    stages = runner.job(job_id)["stages"]
    return str(stages[-1]["output_version_id"])


def _service(tmp_path: Path, answer: list[str]) -> tuple[RagService, FakeOllama, ListSink]:
    entry = get_model("qwen3-1.7b-q4km")
    folder = model_dir(get_settings().data_dir / "models", entry)
    if not (folder / STAMP).exists():  # the real folder, when the LLM file is pulled
        pytest.skip("the LLM's allow-list entry must be pulled for its name")
    ollama, card = FakeOllama(), FakeCard()
    ollama.card = card
    ollama.parts = [{"message": {"content": piece}, "done": False} for piece in answer] + [
        {"done": True, "prompt_eval_count": 400, "eval_count": len(answer), "eval_duration": 10**8}
    ]
    llm = LlmAdapter(api=ollama, owner=Owner(), settings=get_settings(), card=card)
    sink = ListSink()
    retriever = Retriever(engine=get_engine(), data_dir=get_settings().data_dir)
    return RagService(retriever=retriever, llm=llm, engine=get_engine(), sink=sink), ollama, sink


def _set_threshold(value: float | None) -> None:
    with Session(get_engine()) as session, session.begin():
        write_retrieval(session, RetrievalValues(abstention_threshold=value))


def _events(service: RagService, question: str, version: str) -> list[dict[str, Any]]:
    return list(service.start(question, version, 3))


def test_LLM_2_1_answer_cites_only_given_sources(index_version: str, tmp_path: Path) -> None:
    _set_threshold(None)
    service, ollama, sink = _service(tmp_path, ["It hashes rows and config ", "[1]", " [7]", "."])
    events = _events(service, "How is a dataset version ID computed?", index_version)
    names = [e["event"] for e in events]
    assert names[0] == "meta" and names[-2:] == ["citations", "done"]
    assert events[0]["route"] == "rag" and events[0]["index_version_id"] == index_version
    text = "".join(e["text"] for e in events if e["event"] == "token")
    assert text == "It hashes rows and config [1] ."
    citations = events[-2]
    assert citations["removed"] == [7] and citations["uncited"] is False
    assert [c["n"] for c in citations["citations"]] == [1]
    assert citations["citations"][0]["section"] == "Stratal — Data Model › 3. Identifiers"
    assert len(citations["sources"]) == 3
    prompt = next(c[1] for c in ollama.calls if c[0] == "chat")["messages"]
    assert prompt[0]["role"] == "system" and "[1] DATA_MODEL.md › " in prompt[1]["content"]
    assert any(e["level"] == "WARN" and "[7]" in e["message"] for e in sink.events)


def test_LLM_2_1_an_answer_without_citations_is_marked_uncited(
    index_version: str, tmp_path: Path
) -> None:
    _set_threshold(None)
    service, *_ = _service(tmp_path, ["The ID is a hash."])
    citations = _events(service, "How is a dataset version ID computed?", index_version)[-2]
    assert citations["uncited"] is True and citations["citations"] == []


def test_LLM_2_3_below_the_threshold_abstains_without_the_model(
    index_version: str, tmp_path: Path
) -> None:
    _set_threshold(0.75)
    service, ollama, _ = _service(tmp_path, ["should not be asked"])
    events = _events(service, "What is the capital of Peru?", index_version)
    assert [e["event"] for e in events] == ["meta", "token", "citations", "done"]
    assert events[0]["abstained"] is True and events[0]["device"] == "cpu"
    assert events[1]["text"] == "I don't know."
    citations = events[2]
    assert citations["abstained"] is True and citations["threshold"] == 0.75
    assert citations["nearest"]["relevance"] < 0.75
    assert not any(c[0] == "chat" for c in ollama.calls)
    _set_threshold(None)


def test_NFR_7_rag_runs_with_outbound_connections_refused(
    index_version: str, tmp_path: Path
) -> None:
    """Embedding the question, the index and the answer need no network: every connection
    but the database's is refused (the LLM's own route is ADR-0021's ``llm`` network)."""
    _set_threshold(None)
    service, *_ = _service(tmp_path, ["It hashes rows and config [1]."])
    database = socket.gethostbyname(get_engine().url.host or "localhost")
    attempts: list[str] = []
    original = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in (database, "127.0.0.1"):
            attempts.append(repr(address))
            raise OSError("network disabled for NFR-7")
        original(self, address)

    socket.socket.connect = guarded  # type: ignore[method-assign]
    try:
        events = _events(service, "How is a dataset version ID computed?", index_version)
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]
    assert attempts == [], "nothing tried to reach outside"
    assert [c["n"] for c in events[-2]["citations"]] == [1]
    assert events[-1]["event"] == "done"
