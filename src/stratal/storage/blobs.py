"""Content-addressed blob store (ADR-0004, docs/DATA_MODEL.md §6).

Every file is stored once, under the SHA-256 of its bytes. A blob is never
modified after it is written. Writes go to ``tmp/`` first and are renamed into
place, so a crash leaves either no blob or a complete one.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1 << 20


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    size_bytes: int
    created: bool
    """True if this call wrote the blob; False if it was already present."""


class BlobStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "blobs" / "sha256"
        self._tmp = data_dir / "tmp"

    def path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError(f"not a SHA-256 hex digest: {sha256!r}")
        return self._root / sha256[:2] / sha256[2:4] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put_file(self, source: Path) -> StoredBlob:
        """Copy ``source`` into the store, hashing it in the same pass."""
        self._tmp.mkdir(parents=True, exist_ok=True)
        staging = self._tmp / f"blob-{uuid.uuid4().hex}"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, staging.open("wb") as writer:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    writer.write(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            sha256 = digest.hexdigest()
            final = self.path_for(sha256)
            if final.exists():
                return StoredBlob(sha256, size, created=False)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            final.chmod(0o444)
            return StoredBlob(sha256, size, created=True)
        finally:
            staging.unlink(missing_ok=True)

    def total_bytes(self) -> int:
        """Bytes held by all blobs. Used by tests and disk audits."""
        if not self._root.exists():
            return 0
        return sum(p.stat().st_size for p in self._root.rglob("*") if p.is_file())
