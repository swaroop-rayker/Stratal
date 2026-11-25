# pyright: basic, reportMissingImports=false
"""A table's input layer for its embedding columns (DATA-7.3; ADR-0017 rule 6).

``table_prep`` hands a network one row per record: numbers, ordinal positions and
one-hot slots first (the *lead*), then one category index per ``embedding``
column. :class:`TableInput` looks each index up in that column's ``Embedding``,
joins the vectors to the lead, and passes the result to the compiled graph, whose
locked input is ``TablePrep.width`` (BUILD-1.3).

It is added only when a version has embedding columns, so every other model's
weights keep their names and load as before (ADR-0019 Addendum B, as built).
"""

import torch
from torch import nn

from stratal.train.table_prep import TablePrep


class TableInput(nn.Module):
    """``Embedding`` lookups for a table's embedding columns, then ``body``."""

    def __init__(self, prep: TablePrep, body: nn.Module) -> None:
        super().__init__()
        self.lead = prep.lead_width
        self.embeddings = nn.ModuleList(nn.Embedding(c.slots, c.dim) for c in prep.embedded)
        self.body = body

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        parts = [x[:, : self.lead]]
        index = x[:, self.lead :].long()
        for k, embedding in enumerate(self.embeddings):
            parts.append(embedding(index[:, k]))
        return self.body(torch.cat(parts, dim=1))


def with_table_input(model: nn.Module, prep: TablePrep | None) -> nn.Module:
    """``model`` behind a :class:`TableInput` when ``prep`` has embedding columns."""
    if prep is None or not prep.embedded:
        return model
    return TableInput(prep, model)


def graph_of(model: nn.Module) -> nn.Module:
    """The compiled graph inside ``model``, for naming its nodes (freezing, ADR-0014)."""
    return model.body if isinstance(model, TableInput) else model
