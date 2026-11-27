"""Fakes for the LLM adapter (ADR-0021): Ollama's API and the card, with no service and no
GPU. Used by the unit tests and by the holder's integration tests."""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class FakeStream:
    def __init__(self, parts: list[dict[str, Any]], gate: threading.Event | None = None) -> None:
        self.parts = parts
        self.gate = gate
        self.closed = False

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for part in self.parts:
            if self.gate is not None:
                self.gate.wait(5)
            yield part

    def close(self) -> None:
        self.closed = True


class FakeOllama:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.blobs: set[str] = set()
        self.created: list[str] = []
        self.loaded = False
        self.offload_all = True
        self.parts: list[dict[str, Any]] = [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " there."}, "done": False},
            {"done": True, "prompt_eval_count": 12, "eval_count": 3, "eval_duration": 30_000_000},
        ]
        self.gate: threading.Event | None = None
        self.streams: list[FakeStream] = []
        self.card: FakeCard | None = None

    def models(self) -> list[str]:
        return [f"{n}" for n in self.created]

    def has_blob(self, digest: str) -> bool:
        return digest in self.blobs

    def push_blob(self, digest: str, path: Path) -> None:
        self.calls.append(("push", (digest, path.name)))
        self.blobs.add(digest)

    def create(self, name: str, filename: str, digest: str, num_ctx: int) -> None:
        self.calls.append(("create", (name, filename, digest, num_ctx)))
        self.created.append(name)

    def load(self, name: str) -> dict[str, Any]:
        self.calls.append(("load", name))
        self.loaded = True
        if self.card is not None:
            self.card.used += 1657
        return {}

    def ps(self) -> list[dict[str, Any]]:
        size = 1_629_948_476
        if not self.loaded:
            return []
        vram = size if self.offload_all else size // 2
        return [{"name": self.created[-1], "size": size, "size_vram": vram}]

    def unload(self, name: str) -> None:
        self.calls.append(("unload", name))
        self.loaded = False
        if self.card is not None and not self.card.stuck:
            self.card.used = self.card.base

    def chat(self, body: dict[str, Any]) -> FakeStream:
        self.calls.append(("chat", body))
        stream = FakeStream(self.parts, self.gate)
        self.streams.append(stream)
        return stream


class FakeCard:
    def __init__(self, used: float = 300.0, total: float = 6141.0) -> None:
        self.base = used
        self.used = used
        self.total = total
        self.stuck = False

    def used_mb(self) -> float | None:
        return self.used

    def total_mb(self) -> float | None:
        return self.total
