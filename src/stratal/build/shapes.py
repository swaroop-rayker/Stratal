"""Per-sample tensor shapes for the model builder (ADR-0017 rule 4).

A shape leaves out the batch axis and says what kind of tensor it is, because
the kind decides which layers may follow: a convolution needs an image, an
embedding needs integer tokens, the output head needs a vector.
"""

import math
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["image", "vector", "sequence", "tokens"]

RANK: dict[Kind, int] = {"image": 3, "vector": 1, "sequence": 2, "tokens": 1}
"""Axes per kind: image (C, H, W), vector (F,), sequence (L, D), tokens (L,)."""


@dataclass(frozen=True)
class Shape:
    kind: Kind
    dims: tuple[int, ...]
    vocab: int | None = None
    """The vocabulary size of integer tokens; ``None`` for every other kind."""

    def __post_init__(self) -> None:
        if len(self.dims) != RANK[self.kind]:
            raise ValueError(f"a {self.kind} has {RANK[self.kind]} axes, got {self.dims}")
        if any(d < 1 for d in self.dims):
            raise ValueError(f"every axis needs at least 1 element, got {self.dims}")
        if (self.kind == "tokens") != (self.vocab is not None):
            raise ValueError("tokens, and only tokens, carry a vocabulary size")

    @property
    def elements(self) -> int:
        return math.prod(self.dims)

    def describe(self) -> str:
        """``image 3×32×32``, ``vector 128``, ``tokens 64 (vocabulary 20,000)``."""
        text = f"{self.kind} {'×'.join(str(d) for d in self.dims)}"
        return f"{text} (vocabulary {self.vocab:,})" if self.vocab is not None else text

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "dims": list(self.dims), "vocab": self.vocab}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Shape":
        return cls(data["kind"], tuple(int(d) for d in data["dims"]), data.get("vocab"))


class SpecProblem(Exception):
    """A layer cannot take its input. ``code`` is stable (ADR-0017 rule 4)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
