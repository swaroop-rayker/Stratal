# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the worker images, so the type checker cannot see it here.
"""DATA-7.3: an ``embedding`` column reaches the network through its input layer.

ADR-0019 Addendum B, as built: ``TableInput`` looks each embedding column's index
up in an ``Embedding`` in front of the compiled graph. A table without embedding
columns gets no input layer, so its weights keep their names. Runs in the torch
image (check.ps1's torch step).
"""

import pytest

torch = pytest.importorskip("torch")

from stratal.build.shapes import Shape  # noqa: E402
from stratal.build.templates import BUILTINS  # noqa: E402
from stratal.data.dataset_types import Column, TableSchema  # noqa: E402
from stratal.ml.data import SplitTable, TabularData  # noqa: E402
from stratal.train.config import ModelSpec  # noqa: E402
from stratal.train.reference import build_model  # noqa: E402
from stratal.train.table_input import TableInput, graph_of, with_table_input  # noqa: E402
from stratal.train.table_prep import fit  # noqa: E402

pytestmark = pytest.mark.torch


def _prep(encoding: str | None):
    schema = TableSchema(
        (
            Column("alcohol", "numeric"),
            Column("magnesium", "categorical", encoding=encoding),  # type: ignore[arg-type]
            Column("y", "categorical", "target"),
        )
    )
    train = SplitTable(
        {"alcohol": [12.0, 13.0, 14.0, 15.0], "magnesium": ["88", "101", "88", "127"]},
        ["a", "b", "a", "b"],
    )
    return fit(
        TabularData(
            schema, "classification", {"train": train, "val": SplitTable(), "test": SplitTable()}
        )
    )


def _model(prep):
    graph = next(t for t in BUILTINS if t.dataset_type == "tabular").spec
    spec = ModelSpec(name="graph", graph=graph)
    return with_table_input(build_model(spec, Shape("vector", (prep.width,)), 2), prep)


def test_DATA_7_3_an_embedding_choice_reaches_the_input_block() -> None:
    prep = _prep("embedding")
    model = _model(prep)
    assert isinstance(model, TableInput)
    [embedding] = model.embeddings
    assert (embedding.num_embeddings, embedding.embedding_dim) == (4, 2)  # 3 seen + unknown
    x = torch.tensor(prep.encode({"alcohol": [13.0, 14.0], "magnesium": ["101", "999"]}))
    assert x.shape == (2, prep.raw_width) == (2, 2)
    assert model(x).shape == (2, 2)
    assert "embeddings.0.weight" in model.state_dict()
    assert graph_of(model) is model.body


def test_DATA_7_3_the_embedding_learns() -> None:
    prep = _prep("embedding")
    model = _model(prep)
    x = torch.tensor(prep.encode({"alcohol": [13.0], "magnesium": ["88"]}))
    loss = model(x).sum()
    loss.backward()
    assert model.embeddings[0].weight.grad is not None
    grad = model.embeddings[0].weight.grad
    assert grad[2].abs().sum() > 0  # "88" is index 2 of the sorted categories 101, 127, 88
    assert grad[0].abs().sum() == 0


def test_DATA_7_3_no_embedding_column_means_no_input_layer() -> None:
    prep = _prep(None)
    model = _model(prep)
    assert not isinstance(model, TableInput)
    assert not any(k.startswith(("embeddings.", "body.")) for k in model.state_dict())
