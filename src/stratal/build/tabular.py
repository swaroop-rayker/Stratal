"""The tabular input block (ADR-0017 rule 6; M10's open points 8 and 10).

A table enters a network as one vector: each numeric column becomes one
standardised feature; each categorical column is one-hot encoded (one feature
per category seen in the train split, plus one for unknown), or, if chosen,
mapped through an ``Embedding``. The vector's width is the builder's locked
input (BUILD-1.3). The encoders are fitted on the train split only (ML-1.3) and
stored with the weights; both arrive with tabular training in M10.
"""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TabularColumn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    kind: Literal["numeric", "categorical"]
    categories: int = Field(
        default=0, ge=0, description="Distinct values in the train split (categorical only)."
    )
    encoding: Literal["one_hot", "embedding"] = Field(
        default="one_hot", description="How a categorical column enters the network."
    )
    embedding_dim: int | None = Field(
        default=None, ge=1, le=512, description="Embedding width; empty: min(50, ⌈n / 2⌉)."
    )

    @model_validator(mode="after")
    def _categorical_only(self) -> "TabularColumn":
        if self.kind == "numeric" and (self.categories or self.encoding != "one_hot"):
            raise ValueError(f"'{self.name}' is numeric: it has no categories or encoding")
        if self.kind == "categorical" and self.categories < 1:
            raise ValueError(f"'{self.name}' is categorical but has no categories")
        return self

    @property
    def width(self) -> int:
        if self.kind == "numeric":
            return 1
        if self.encoding == "one_hot":
            return self.categories + 1  # one more for a category the train split never had
        return self.embedding_dim or default_embedding_dim(self.categories)

    @property
    def parameters(self) -> int:
        if self.kind == "categorical" and self.encoding == "embedding":
            return (self.categories + 1) * self.width
        return 0


def default_embedding_dim(categories: int) -> int:
    """A common rule of thumb: half the categories, at most 50."""
    return min(50, math.ceil(categories / 2))


def input_block(columns: list[TabularColumn]) -> tuple[int, int]:
    """``(width, parameters)`` of the input block for ``columns`` (features only)."""
    return sum(c.width for c in columns), sum(c.parameters for c in columns)
