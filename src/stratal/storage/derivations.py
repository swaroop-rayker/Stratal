"""The derivation cache: expensive per-item results stored by content (ADR-0004).

A value is keyed by ``(kind, input_hash, params_hash)``: what was computed, the
content hash of what it was computed from, and a hash of everything else that
affects the result. Values are never overwritten (INV-11): the same key always
means the same value, so a second writer's insert is simply ignored.

Stages read and write in batches and commit each batch, so work finished
before a crash is not done again (NFR-5, LABEL-1.2).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from sqlalchemy import Engine, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stratal.db.models import Derivation

KIND_PHASH = "phash"
KIND_PREPROCESS = "preprocess"


def params_hash(params: Mapping[str, Any]) -> str:
    """SHA-256 of ``params`` as canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class DerivationCache(Protocol):
    def get_many(self, kind: str, params_hash: str, input_hashes: Sequence[str]) -> dict[str, Any]:
        """The cached values among ``input_hashes``, by input hash. Misses are absent."""
        ...

    def put_many(self, kind: str, params_hash: str, values: Mapping[str, Any]) -> None:
        """Store ``values`` (input hash → JSON-compatible value) and commit them."""
        ...


class PgDerivationCache:
    """The cache in PostgreSQL. Each ``put_many`` is its own transaction."""

    def __init__(self, engine: Engine, *, job_id: str | None = None) -> None:
        self._engine = engine
        self._job_id = job_id

    def get_many(self, kind: str, params_hash: str, input_hashes: Sequence[str]) -> dict[str, Any]:
        if not input_hashes:
            return {}
        keys = [(kind, h, params_hash) for h in set(input_hashes)]
        query = select(Derivation.input_hash, Derivation.value).where(
            tuple_(Derivation.kind, Derivation.input_hash, Derivation.params_hash).in_(keys)
        )
        with Session(self._engine) as session:
            return {input_hash: value for input_hash, value in session.execute(query)}

    def put_many(self, kind: str, params_hash: str, values: Mapping[str, Any]) -> None:
        if not values:
            return
        rows = [
            {
                "kind": kind,
                "input_hash": input_hash,
                "params_hash": params_hash,
                "value": value,
                "created_by_job_id": self._job_id,
            }
            for input_hash, value in values.items()
        ]
        statement = insert(Derivation).values(rows).on_conflict_do_nothing()
        with Session(self._engine) as session, session.begin():
            session.execute(statement)


class MemoryDerivationCache:
    """The same contract in a dict. For unit tests and one-off scripts."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], Any] = {}
        self.puts = 0

    def get_many(self, kind: str, params_hash: str, input_hashes: Sequence[str]) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for input_hash in input_hashes:
            key = (kind, input_hash, params_hash)
            if key in self.values:
                found[input_hash] = self.values[key]
        return found

    def put_many(self, kind: str, params_hash: str, values: Mapping[str, Any]) -> None:
        self.puts += 1
        for input_hash, value in values.items():
            self.values.setdefault((kind, input_hash, params_hash), value)
