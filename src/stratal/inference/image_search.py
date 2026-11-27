# pyright: basic, reportMissingImports=false
# transformers, PyTorch, FAISS and NumPy exist only in the worker images (inference runs the
# worker-gpu image), so this module is checked in basic mode.
"""Image search: text to CLIP to the nearest images of a labelled version (LLM-2.4,
ADR-0022 rule 12).

The images' vectors already exist: the ``label`` stage stored every preprocessed
image's CLIP vector as a ``clip_image_embedding`` derivation (M11, ADR-0013 rule 2).
A query's vector comes from CLIP's text tower on the CPU (ADR-0021 rule 7), so a search
never takes the GPU lease and works while a job trains. For the version asked, an exact
FAISS index is built at the first query and the most recent one is kept (10,000 images
of 512 floats is 20 MB).
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stratal.core.errors import NoImageEmbeddings
from stratal.data import registry
from stratal.db.models import Derivation
from stratal.label.semantic import unpack_vector
from stratal.pretrained.catalog import get_model, local_path
from stratal.storage.derivations import KIND_CLIP_IMAGE
from stratal.storage.manifest import STATUS_ACCEPTED, iter_rows

Json = dict[str, Any]
CLIP_MODEL = "clip-vit-b-32-laion2b"
TOP = 10


class ClipText:
    """CLIP ViT-B/32's text tower on the CPU: a normalised 512-number vector per query."""

    def __init__(self, models_dir: Path, name: str = CLIP_MODEL) -> None:
        import torch
        from transformers import CLIPTextModelWithProjection, CLIPTokenizerFast

        folder = str(local_path(models_dir, name))
        self._torch = torch
        self._model = CLIPTextModelWithProjection.from_pretrained(folder).eval()
        self._tokenizer = CLIPTokenizerFast.from_pretrained(folder)

    def embed(self, text: str) -> np.ndarray:
        with self._torch.no_grad():
            inputs = self._tokenizer([text], return_tensors="pt", padding=True, truncation=True)
            vector = self._model(**inputs).text_embeds[0].numpy().astype(np.float32)
        return vector / (np.linalg.norm(vector) or 1.0)


@dataclass
class _Images:
    version_id: str
    index: Any
    rows: list[Json]


class ImageSearch:
    def __init__(
        self,
        *,
        engine: Engine,
        data_dir: Path,
        text_factory: Callable[[Path], Any] = ClipText,
    ) -> None:
        self._engine = engine
        self._data_dir = data_dir
        self._text_factory = text_factory
        self._text: Any = None
        self._cached: _Images | None = None
        self._lock = threading.Lock()

    def _load(self, version_id: str) -> _Images:
        cached = self._cached
        if cached is not None and cached.version_id == version_id:
            return cached
        with Session(self._engine) as session:
            _, manifest = registry.manifest_for(session, self._data_dir, version_id)
        rows = [
            {
                "row_key": str(r["row_key"]),
                "sha256": r["sha256"],
                "derived_sha256": r["derived_sha256"],
            }
            for r in iter_rows(manifest, columns=["row_key", "status", "sha256", "derived_sha256"])
            if r["status"] == STATUS_ACCEPTED and r.get("derived_sha256")
        ]
        vectors: dict[str, list[float]] = {}
        shas = sorted({str(r["derived_sha256"]) for r in rows})
        with Session(self._engine) as session:
            for start in range(0, len(shas), 1000):
                for found in session.scalars(
                    select(Derivation).where(
                        Derivation.kind == KIND_CLIP_IMAGE,
                        Derivation.input_hash.in_(shas[start : start + 1000]),
                    )
                ):
                    vectors.setdefault(found.input_hash, unpack_vector(str(found.value)))
        rows = [r for r in rows if r["derived_sha256"] in vectors]
        if not rows:
            raise NoImageEmbeddings(
                f"{version_id} has no CLIP image vectors to search.",
                suggested_action="Search a version made by the label stage or after it; the "
                "label stage computes the vectors (a query never does).",
            )
        import faiss

        matrix = np.asarray([vectors[str(r["derived_sha256"])] for r in rows], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-12)
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        loaded = _Images(version_id, index, rows)
        self._cached = loaded
        return loaded

    def search(self, text: str, version_id: str, k: int = TOP) -> Iterator[Json]:
        """The events of one image search: ``meta``, ``images``, ``done``."""
        started = time.monotonic()
        with self._lock:
            loaded = self._load(version_id)
            if self._text is None:
                self._text = self._text_factory(self._data_dir / "models")
            query = self._text.embed(text).reshape(1, -1)
            scores, positions = loaded.index.search(query, min(k, len(loaded.rows)))
        entry = get_model(CLIP_MODEL)
        images = [
            {**loaded.rows[int(p)], "similarity": round(float(s), 4)}
            for s, p in zip(scores[0], positions[0], strict=True)
            if p >= 0
        ]
        return self._events(images, loaded, version_id, entry, started)

    def _events(
        self, images: list[Json], loaded: _Images, version_id: str, entry: Any, started: float
    ) -> Iterator[Json]:
        total = round(1000 * (time.monotonic() - started), 1)
        yield {
            "event": "meta",
            "route": "images",
            "model": entry.name,
            "revision": entry.revision,
            "display": entry.display,
            "device": "cpu",
            "cold_start": False,
            "labelled_version_id": version_id,
        }
        yield {
            "event": "images",
            "images": images,
            "version_id": version_id,
            "searched": len(loaded.rows),
        }
        yield {
            "event": "done",
            "ttft_ms": total,
            "total_ms": total,
            "prompt_tokens": None,
            "output_tokens": 0,
            "tokens_per_s": None,
        }
