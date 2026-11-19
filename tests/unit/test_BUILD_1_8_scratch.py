# pyright: basic, reportMissingImports=false
# PyTorch is installed only in the worker images, so the type checker cannot see it here.
"""BUILD-1.8: the from-scratch attention, positional encoding and LSTM cell match PyTorch.

Needs PyTorch, so it runs in the GPU worker's image (check.ps1's torch step,
``-m torch tests/integration tests/unit``); in the dev image it is skipped.
Strict expected failures until the units are written (ADR-0012).
"""

import ast
import inspect
import math
import textwrap
from typing import Any

import pytest

torch = pytest.importorskip("torch")

from stratal.build.scratch import (  # noqa: E402
    MultiHeadSelfAttention,
    PositionalEncoding,
    ScratchLSTMCell,
)

pytestmark = pytest.mark.torch
unit = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="BUILD-1.8 unit")


def _causal(length: int) -> Any:
    return torch.triu(torch.full((length, length), float("-inf")), diagonal=1)


@unit
@pytest.mark.parametrize("causal", [False, True])
def test_BUILD_1_8_attention_matches_pytorch(causal: bool) -> None:
    torch.manual_seed(0)
    ours = MultiHeadSelfAttention(16, 4, causal=causal).eval()
    theirs = torch.nn.MultiheadAttention(16, 4, batch_first=True).eval()
    theirs.load_state_dict(ours.state_dict())
    x = torch.randn(3, 7, 16)

    got = ours(x)
    want = theirs(x, x, x, attn_mask=_causal(7) if causal else None, need_weights=False)[0]

    assert got.shape == (3, 7, 16)
    torch.testing.assert_close(got, want, atol=1e-5, rtol=1e-5)


@unit
def test_BUILD_1_8_causal_attention_ignores_the_future() -> None:
    torch.manual_seed(1)
    layer = MultiHeadSelfAttention(8, 2, causal=True).eval()
    x = torch.randn(1, 5, 8)
    changed = x.clone()
    changed[:, 3:] += 10.0  # only positions 3 and 4 differ

    torch.testing.assert_close(layer(x)[:, :3], layer(changed)[:, :3])


def test_BUILD_1_8_attention_has_pytorchs_parameter_layout() -> None:
    """Plumbing: the layout the comparison relies on, and ADR-0017's 4E² + 4E."""
    layer = MultiHeadSelfAttention(16, 4)
    reference = torch.nn.MultiheadAttention(16, 4, batch_first=True)
    assert {k: v.shape for k, v in layer.state_dict().items()} == {
        k: v.shape for k, v in reference.state_dict().items()
    }
    assert sum(p.numel() for p in layer.parameters()) == 4 * 16 * 16 + 4 * 16
    with pytest.raises(ValueError, match="divisible"):
        MultiHeadSelfAttention(10, 4)


@unit
def test_BUILD_1_8_positional_encoding_matches_the_formula() -> None:
    layer = PositionalEncoding(6, max_len=50).eval()
    x = torch.zeros(2, 4, 6)

    got = layer(x)

    for pos in range(4):
        for i in range(3):
            angle = pos / 10000 ** (2 * i / 6)
            assert got[1, pos, 2 * i].item() == pytest.approx(math.sin(angle), abs=1e-6)
            assert got[1, pos, 2 * i + 1].item() == pytest.approx(math.cos(angle), abs=1e-6)


@unit
def test_BUILD_1_8_positional_encoding_adds_to_the_input_and_checks_length() -> None:
    layer = PositionalEncoding(4, max_len=3).eval()
    x = torch.randn(2, 3, 4)
    torch.testing.assert_close(layer(x) - layer(torch.zeros_like(x)), x)
    with pytest.raises(ValueError, match="max_len"):
        layer(torch.zeros(1, 4, 4))


def test_BUILD_1_8_positional_encoding_has_no_parameters_and_needs_an_even_width() -> None:
    assert sum(p.numel() for p in PositionalEncoding(8).parameters()) == 0
    assert not PositionalEncoding(8).state_dict(), "the table is not saved with the weights"
    with pytest.raises(ValueError, match="even"):
        PositionalEncoding(7)


@unit
def test_BUILD_1_8_lstm_cell_matches_pytorch_step_by_step() -> None:
    torch.manual_seed(2)
    ours = ScratchLSTMCell(5, 6)
    theirs = torch.nn.LSTMCell(5, 6)
    theirs.load_state_dict(ours.state_dict())
    xs = torch.randn(4, 3, 5)  # four steps, batch of three

    state_a = state_b = None
    for x in xs:
        state_a = ours(x, state_a)
        state_b = theirs(x, state_b)
        torch.testing.assert_close(state_a[0], state_b[0], atol=1e-6, rtol=1e-6)
        torch.testing.assert_close(state_a[1], state_b[1], atol=1e-6, rtol=1e-6)


def test_BUILD_1_8_lstm_cell_has_pytorchs_parameter_layout() -> None:
    layer, reference = ScratchLSTMCell(5, 6), torch.nn.LSTMCell(5, 6)
    assert {k: v.shape for k, v in layer.state_dict().items()} == {
        k: v.shape for k, v in reference.state_dict().items()
    }


@pytest.mark.parametrize(
    "unit_class", [MultiHeadSelfAttention, PositionalEncoding, ScratchLSTMCell]
)
def test_BUILD_1_8_forward_passes_use_no_ready_made_layer(unit_class: type) -> None:
    """The point of BUILD-1.8: no call to PyTorch's own attention or LSTM in a forward."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(unit_class.forward)))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    banned = {"MultiheadAttention", "scaled_dot_product_attention", "LSTMCell", "LSTM", "lstm_cell"}
    assert not names & banned
