# pyright: basic, reportMissingImports=false
# sentence-transformers, tokenizers and faiss exist only in the worker images, so this
# module is checked in basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""The ``index`` stage and the pieces retrieval shares with it (LLM-2.1, ADR-0022 rule 4).

* **Embeddings:** every accepted chunk is embedded with ``bge-small-en-v1.5`` on the
  CPU, in batches, normalised; each vector is a derivation (``text_embedding``) keyed
  by the chunk text's hash and the model, so re-indexing the same chunks costs nothing.
* **The index:** FAISS's ``IndexFlatIP`` over the normalised vectors (exact cosine
  search), serialised into the blob store. The manifest's ``stratal.rag_index`` names
  the blob, the model, its revision, the query prefix, and the row order: accepted
  rows by row key. FAISS's file holds vectors and no code (NFR-8).
* **Tokens:** the same model's tokenizer counts tokens for ``chunk``.

The rows themselves are passed on unchanged.
"""

import base64
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.corpus import EMBEDDING_MODEL, META_RAG_INDEX, IndexConfig
from stratal.data.records import canonical_json, inherited_metadata
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.pretrained.catalog import local_path
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import DerivationCache, params_hash
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows, read_metadata

STAGE = "index"
KIND_TEXT_EMBEDDING = "text_embedding"


class Embedder(Protocol):
    dimension: int
    version: str

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class BgeEmbedder:
    """bge-small-en-v1.5 through sentence-transformers on the CPU, normalised vectors."""

    def __init__(self, models_dir: Path, name: str = EMBEDDING_MODEL) -> None:
        import sentence_transformers
        import torch

        torch.set_num_threads(4)
        self._model = sentence_transformers.SentenceTransformer(
            str(local_path(models_dir, name)), device="cpu", local_files_only=True
        )
        dimension = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dimension = int(dimension())
        self.version = str(sentence_transformers.__version__)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(list(texts), normalize_embeddings=True, batch_size=32)
        return np.asarray(vectors, dtype=np.float32)


def bge_token_counter(models_dir: Path, name: str = EMBEDDING_MODEL) -> Callable[[str], int]:
    """Counts tokens as the embedding model reads them (special tokens left out)."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(local_path(models_dir, name) / "tokenizer.json"))

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return count


def _encode(vector: np.ndarray) -> str:
    return base64.b64encode(vector.astype(np.float32).tobytes()).decode()


def _decode(value: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(value), dtype=np.float32)


def serialise_index(vectors: np.ndarray) -> bytes:
    import faiss

    index = faiss.IndexFlatIP(vectors.shape[1])
    if len(vectors):
        index.add(vectors)
    return bytes(faiss.serialize_index(index).tobytes())


def load_index(data: bytes) -> Any:
    import faiss

    return faiss.deserialize_index(np.frombuffer(data, dtype=np.uint8))


@dataclass(frozen=True)
class IndexResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]


def run_index(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: IndexConfig,
    derive: VersionDeriver,
    sink: EventSink,
    cache: DerivationCache,
    embedder: Embedder,
    checkpoint: Callable[[], None] = lambda: None,
) -> IndexResult:
    started = time.monotonic()
    made_by = read_metadata(parent_manifest).get("stratal.stage")
    if made_by != "chunk":
        raise StratalError(
            f"Indexing needs a chunk version; {parent_version_id} was made by {made_by!r}.",
            suggested_action="Run the text-corpus pipeline through chunk first.",
        )
    accepted = sorted(
        (
            (str(r["row_key"]), str(r["sha256"]), str(r["text"] or ""))
            for r in iter_rows(parent_manifest, columns=["row_key", "status", "sha256", "text"])
            if r["status"] == STATUS_ACCEPTED
        ),
        key=lambda item: item[0],
    )
    params = params_hash(
        {
            "model": config.embedding_model,
            "revision": config.embedding_revision,
            "sentence_transformers": embedder.version,
            "normalised": True,
        }
    )
    hashes = [sha for _, sha, _ in accepted]
    known = cache.get_many(KIND_TEXT_EMBEDDING, params, hashes)
    missing = [(sha, text) for _, sha, text in accepted if sha not in known]
    sink.emit(
        stage=STAGE,
        message=f"Embedding {len(missing):,} of {len(accepted):,} chunks with "
        f"{config.embedding_model} on the CPU ({len(accepted) - len(missing):,} from the cache)",
        metrics={"chunks": len(accepted), "cached": len(accepted) - len(missing)},
    )
    unique = list(dict(missing).items())
    for start in range(0, len(unique), config.batch_size):
        checkpoint()
        batch = unique[start : start + config.batch_size]
        vectors = embedder.embed([text for _, text in batch])
        values = {sha: _encode(vector) for (sha, _), vector in zip(batch, vectors, strict=True)}
        cache.put_many(KIND_TEXT_EMBEDDING, params, values)
        known.update(values)
        sink.emit(
            stage=STAGE,
            message=f"Embedded {min(start + len(batch), len(unique)):,} of {len(unique):,} chunks",
            step=min(start + len(batch), len(unique)),
            total=len(unique),
        )
    dimension = embedder.dimension
    matrix = (
        np.stack([_decode(str(known[sha])) for sha in hashes])
        if hashes
        else np.zeros((0, dimension), dtype=np.float32)
    )
    stored = BlobStore(data_dir).put_bytes(serialise_index(matrix))
    description = {
        "sha256": stored.sha256,
        "dimension": dimension,
        "count": len(hashes),
        "model": config.embedding_model,
        "revision": config.embedding_revision,
        "query_prefix": config.query_prefix,
        "order": "accepted rows by row_key",
        "metric": "inner product of normalised vectors (cosine)",
    }
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=iter_rows(parent_manifest),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
        metadata={
            **inherited_metadata(parent_manifest),
            META_RAG_INDEX: canonical_json(description),
        },
    )
    sink.emit(
        stage=STAGE,
        message=f"Indexed {len(hashes):,} chunks · {dimension} dimensions · "
        f"{stored.size_bytes / 2**20:.1f} MB",
        metrics={"vectors": len(hashes), "dimension": dimension, "index_bytes": stored.size_bytes},
    )
    return IndexResult(
        record,
        {
            "vectors": len(hashes),
            "embedded": len(unique),
            "cached": len(accepted) - len(missing),
            "seconds": round(time.monotonic() - started, 2),
        },
    )


def index_of(manifest: Path) -> dict[str, Any] | None:
    """The ``stratal.rag_index`` description of an index version, or ``None``."""
    text = read_metadata(manifest).get(META_RAG_INDEX)
    return None if text is None else dict(json.loads(text))
