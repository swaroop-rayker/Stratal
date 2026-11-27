# pyright: basic, reportMissingImports=false
# FAISS, sentence-transformers and NumPy exist only in the worker images (inference runs the
# worker-gpu image), so this module is checked in basic mode.
"""RAG in the inference service (LLM-2.1, LLM-2.3; ADR-0022 rules 5 to 8).

1. **Retrieve on the CPU.** The question is embedded with the index's own query
   prefix by bge-small and the top ``k`` chunks are found in the index version's
   FAISS index; relevance is the cosine. No lease is taken.
2. **Abstain below the owner's threshold** without asking the model: the answer is
   "I don't know." with the nearest source and its relevance (rule 8).
3. **The prompt:** numbered sources ``[n] document › section`` and their text, at most
   3,000 tokens of them, the lowest-ranked dropped whole when they do not fit (rule 6).
4. **Citations are checked, not trusted** (rule 7): while the answer streams, a
   ``[n]`` that names no source given is removed and logged; at the end a
   ``citations`` event lists the sources cited, the others given, what was removed,
   and whether the answer is ``uncited``.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import NotFound, ValidationFailed
from stratal.data import registry
from stratal.data.rag_index import BgeEmbedder, bge_token_counter, index_of, load_index
from stratal.data.retrieval_settings import read_retrieval
from stratal.inference.citations import DONT_KNOW, CitationFilter
from stratal.mon.sink import EventSink, LogSink
from stratal.storage.blobs import BlobStore
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows

Json = dict[str, Any]
SOURCE_BUDGET_TOKENS = 3000
CACHE_KEEP = 2
# Its first wording gave "like [2]" as the example, and the model then cited [2] far more
# often than its rank warranted (M12 acceptance, decision 11: 25 of 33). The example now
# names no single number.
SYSTEM_RAG = (
    "You are Stratal's assistant. Answer only from the numbered sources in the message. "
    "They are listed most relevant first. After each sentence, write in square brackets the "
    "number of every source that sentence uses, each by its own number, for example [1] or "
    '[1, 3]. If the sources do not answer the question, say "I don\'t know".'
)


@dataclass(frozen=True)
class Hit:
    n: int
    row_key: str
    document: str
    section: str
    text: str
    relevance: float
    char_start: int | None
    char_end: int | None

    def view(self, *, cited: bool | None = None) -> Json:
        out: Json = {
            "n": self.n,
            "row_key": self.row_key,
            "document": self.document,
            "section": self.section,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "relevance": round(self.relevance, 4),
            "snippet": self.text[:240],
        }
        if cited is not None:
            out["cited"] = cited
        return out


@dataclass
class _Index:
    version_id: str
    description: Json
    index: Any
    rows: list[Json]


class Retriever:
    """Index versions loaded on demand (the two most recent kept) and the embedder."""

    def __init__(
        self,
        *,
        engine: Engine,
        data_dir: Path,
        embedder_factory: Callable[[Path], Any] = BgeEmbedder,
        counter_factory: Callable[[Path], Callable[[str], int]] = bge_token_counter,
    ) -> None:
        self._engine = engine
        self._data_dir = data_dir
        self._embedder_factory = embedder_factory
        self._counter_factory = counter_factory
        self._embedder: Any = None
        self._count: Callable[[str], int] | None = None
        self._cache: OrderedDict[str, _Index] = OrderedDict()
        self._lock = threading.Lock()

    def _models(self) -> Path:
        return self._data_dir / "models"

    def embed(self, text: str) -> np.ndarray:
        with self._lock:
            if self._embedder is None:
                self._embedder = self._embedder_factory(self._models())
            return self._embedder.embed([text])

    def count(self, text: str) -> int:
        if self._count is None:
            self._count = self._counter_factory(self._models())
        return self._count(text)

    def load(self, version_id: str) -> _Index:
        with self._lock:
            found = self._cache.get(version_id)
            if found is not None:
                self._cache.move_to_end(version_id)
                return found
        with Session(self._engine) as session:
            try:
                _, manifest = registry.manifest_for(session, self._data_dir, version_id)
            except NotFound:
                raise
        description = index_of(manifest)
        if description is None:
            raise ValidationFailed(
                f"{version_id} is not an index version, so it cannot answer questions.",
                suggested_action="Pick the corpus's index version (the last stage, index).",
                errors=[
                    {"loc": ["body", "index_version_id"], "msg": "not an index", "code": "invalid"}
                ],
            )
        blob = BlobStore(self._data_dir).path_for(str(description["sha256"]))
        index = load_index(blob.read_bytes())
        rows = sorted(
            (
                {
                    "row_key": str(r["row_key"]),
                    "document": str(r.get("document") or ""),
                    "section": str(r.get("section") or ""),
                    "text": str(r.get("text") or ""),
                    "char_start": r.get("char_start"),
                    "char_end": r.get("char_end"),
                }
                for r in iter_rows(manifest)
                if r["status"] == STATUS_ACCEPTED
            ),
            key=lambda r: r["row_key"],
        )
        loaded = _Index(version_id, description, index, rows)
        with self._lock:
            self._cache[version_id] = loaded
            while len(self._cache) > CACHE_KEEP:
                self._cache.popitem(last=False)
        return loaded

    def search(self, version_id: str, question: str, k: int) -> tuple[_Index, list[Hit]]:
        loaded = self.load(version_id)
        if not loaded.rows:
            return loaded, []
        query = self.embed(str(loaded.description.get("query_prefix") or "") + question)
        scores, positions = loaded.index.search(query, min(k, len(loaded.rows)))
        hits: list[Hit] = []
        for n, (score, position) in enumerate(zip(scores[0], positions[0], strict=True), start=1):
            if position < 0:
                continue
            row = loaded.rows[int(position)]
            hits.append(
                Hit(
                    n=n,
                    row_key=row["row_key"],
                    document=row["document"],
                    section=row["section"],
                    text=row["text"],
                    relevance=float(score),
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                )
            )
        return loaded, hits

    def prompt(self, question: str, hits: Sequence[Hit]) -> tuple[list[Json], list[Hit]]:
        """The messages, and the sources that fit in the budget (the best ones, whole)."""
        given: list[Hit] = []
        used = 0
        for hit in hits:
            block = f"[{hit.n}] {hit.document} › {hit.section}\n{hit.text}"
            tokens = self.count(block)
            if given and used + tokens > SOURCE_BUDGET_TOKENS:
                break
            given.append(hit)
            used += tokens
        sources = "\n\n".join(f"[{h.n}] {h.document} › {h.section}\n{h.text}" for h in given)
        messages = [
            {"role": "system", "content": SYSTEM_RAG},
            {"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"},
        ]
        return messages, given


class RagService:
    """A RAG answer: retrieval and abstention on the CPU, the answer from the LLM adapter."""

    def __init__(
        self, *, retriever: Retriever, llm: Any, engine: Engine, sink: EventSink | None = None
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._engine = engine
        self._sink: EventSink = sink or LogSink()

    def start(self, question: str, index_version_id: str, k: int | None = None) -> Iterator[Json]:
        """Retrieve now, and start the LLM if it is needed, so a refusal happens before the
        stream begins; returns the events."""
        started = time.monotonic()
        with Session(self._engine) as session:
            settings = read_retrieval(session)
        k = k or settings.default_k
        _, hits = self._retriever.search(index_version_id, question, k)
        retrieval_ms = round(1000 * (time.monotonic() - started), 1)
        top = hits[0].relevance if hits else 0.0
        threshold = settings.abstention_threshold
        base = {
            "route": "rag",
            "index_version_id": index_version_id,
            "k": k,
            "top_relevance": round(top, 4),
        }
        if not hits or (threshold is not None and top < threshold):
            return self._abstain(base, hits, threshold, started, retrieval_ms)
        messages, given = self._retriever.prompt(question, hits)
        generation = self._llm.start(
            messages, route="rag", extra={**base, "retrieval_ms": retrieval_ms}
        )
        return self._answer(generation, given, threshold)

    def _abstain(
        self,
        base: Json,
        hits: list[Hit],
        threshold: float | None,
        started: float,
        retrieval_ms: float,
    ) -> Iterator[Json]:
        nearest = hits[0].view() if hits else None
        yield {
            "event": "meta",
            **base,
            "device": "cpu",
            "cold_start": False,
            "abstained": True,
            "retrieval_ms": retrieval_ms,
        }
        yield {"event": "token", "text": "I don't know."}
        yield {
            "event": "citations",
            "citations": [],
            "sources": [],
            "removed": [],
            "uncited": False,
            "abstained": True,
            "nearest": nearest,
            "threshold": threshold,
        }
        total = round(1000 * (time.monotonic() - started), 1)
        yield {
            "event": "done",
            "ttft_ms": total,
            "total_ms": total,
            "prompt_tokens": None,
            "output_tokens": 0,
            "tokens_per_s": None,
        }

    def _answer(self, generation: Any, given: list[Hit], threshold: float | None) -> Iterator[Json]:
        check = CitationFilter({h.n for h in given})
        text: list[str] = []
        for event in generation.events():
            if event["event"] == "token":
                out = check.feed(str(event["text"]))
                if out:
                    text.append(out)
                    yield {"event": "token", "text": out}
                continue
            if event["event"] == "done":
                rest = check.flush()
                if rest:
                    text.append(rest)
                    yield {"event": "token", "text": rest}
                for n in check.removed:
                    self._sink.emit(
                        stage="inference",
                        level="WARN",
                        message=f"Citation [{n}] refers to no source given; removed",
                        reason=f"The answer cited [{n}], but only sources 1 to {len(given)} were "
                        "given (LLM-2.1).",
                        suggested_action="Nothing to do: the marker was removed from the answer.",
                    )
                answer = "".join(text)
                cited = set(check.used)
                yield {
                    "event": "citations",
                    "citations": [h.view(cited=True) for h in given if h.n in cited],
                    "sources": [h.view(cited=h.n in cited) for h in given],
                    "removed": check.removed,
                    "uncited": not cited and not DONT_KNOW.search(answer),
                    "abstained": False,
                    "model_said_dont_know": bool(DONT_KNOW.search(answer)),
                    "threshold": threshold,
                }
            yield event
