# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU and ML images, so this module is checked in
# basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""From-scratch layers: multi-head self-attention, positional encoding, an LSTM cell (BUILD-1.8).

Owner units (ADR-0012), written by the agent at the owner's request (M9's kickoff,
2026-11-19); how they work is explained in ``docs/learning/sys-design.md``. The
constructors are plumbing: they create the parameters with the
same names and shapes as PyTorch's own layers, so a test can copy weights across
and compare outputs, and so the parameter counts match ADR-0017's arithmetic.
The ``forward`` bodies are the units.

Only the operations a textbook would use are allowed in a unit: matrix products,
element-wise arithmetic, ``softmax``, ``sigmoid``, ``tanh``, masking and
dropout. Calling ``torch.nn.MultiheadAttention``, ``torch.nn.functional.
scaled_dot_product_attention`` or ``torch.nn.LSTMCell`` would defeat BUILD-1.8,
and a test checks that the forward passes do not.

Tests: ``tests/unit/test_BUILD_1_8_scratch.py`` (in the torch image).
"""

import math

import torch
from torch import nn


class MultiHeadSelfAttention(nn.Module):
    """Multi-head self-attention over a sequence (BUILD-1.8; ADR-0017 rule 3, ``attention``).

    Parameters, laid out as in ``torch.nn.MultiheadAttention`` so weights copy across:
    ``in_proj_weight`` (3E × E, the query, key and value projections stacked in that
    order), ``in_proj_bias`` (3E), and ``out_proj`` (a Linear E → E). With bias that is
    4E² + 4E parameters.

    ``forward(x)`` must satisfy:

    1. ``x`` is ``(N, L, E)`` (batch first); the result has the same shape.
    2. Queries, keys and values are ``x`` projected by the three blocks of
       ``in_proj_weight`` (plus ``in_proj_bias``), split into ``num_heads`` heads of
       E / H features each.
    3. Each head computes ``softmax(Q Kᵀ / √(E / H)) V``. With ``causal``, position i
       attends only to positions ≤ i (the others get −∞ before the softmax).
    4. Dropout with probability ``dropout`` is applied to the attention weights, in
       training mode only.
    5. The heads are concatenated back to E features and passed through ``out_proj``.
    6. In eval mode, given the same weights, the result equals
       ``torch.nn.MultiheadAttention(E, H, batch_first=True)(x, x, x, attn_mask=mask,
       need_weights=False)[0]`` within 1e-5, ``mask`` being the causal mask or none.
    """

    def __init__(
        self,
        embed_dim: int,
        num_heads: int,
        *,
        dropout: float = 0.0,
        causal: bool = False,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if embed_dim % num_heads:
            raise ValueError(f"embed_dim {embed_dim} is not divisible by num_heads {num_heads}")
        self.embed_dim, self.num_heads, self.causal = embed_dim, num_heads, causal
        self.dropout = dropout
        self.in_proj_weight = nn.Parameter(torch.empty(3 * embed_dim, embed_dim))
        self.in_proj_bias = nn.Parameter(torch.zeros(3 * embed_dim)) if bias else None
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        nn.init.xavier_uniform_(self.in_proj_weight)
        if bias:
            nn.init.zeros_(self.out_proj.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, length, width = x.shape
        heads, per_head = self.num_heads, width // self.num_heads
        # One matrix product makes queries, keys and values; split them, then the heads.
        projected = x @ self.in_proj_weight.T
        if self.in_proj_bias is not None:
            projected = projected + self.in_proj_bias
        q, k, v = projected.split(width, dim=-1)

        def split_heads(t: torch.Tensor) -> torch.Tensor:  # (N, L, E) -> (N, H, L, E/H)
            return t.reshape(batch, length, heads, per_head).transpose(1, 2)

        q, k, v = split_heads(q), split_heads(k), split_heads(v)
        scores = (q @ k.transpose(-2, -1)) / math.sqrt(per_head)  # (N, H, L, L)
        if self.causal:
            future = torch.ones(length, length, dtype=torch.bool, device=x.device).triu(1)
            scores = scores.masked_fill(future, float("-inf"))
        weights = torch.softmax(scores, dim=-1)
        if self.training and self.dropout > 0:
            weights = nn.functional.dropout(weights, p=self.dropout)
        mixed = (weights @ v).transpose(1, 2).reshape(batch, length, width)  # heads joined
        return self.out_proj(mixed)


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding, added to a sequence (BUILD-1.8; ``positional_encoding``).

    No parameters: the table is a buffer that is not saved with the weights.

    ``forward(x)`` must satisfy:

    1. ``x`` is ``(N, L, D)``; the result is ``x + PE[:L]`` broadcast over the batch,
       then dropout with probability ``dropout`` in training mode.
    2. ``PE[pos, 2i] = sin(pos / 10000^(2i / D))`` and ``PE[pos, 2i + 1] =
       cos(pos / 10000^(2i / D))``, as in *Attention Is All You Need* §3.5.
    3. ``L > max_len`` raises ``ValueError`` naming both.

    The constructor raises ``ValueError`` for an odd ``d_model`` (the sin and cos pairs
    need an even width; ADR-0017's ``odd_model_dim``).
    """

    def __init__(self, d_model: int, *, max_len: int = 512, dropout: float = 0.0) -> None:
        super().__init__()
        if d_model % 2:
            raise ValueError(f"d_model must be even for sin/cos pairs, got {d_model}")
        self.d_model, self.max_len, self.dropout = d_model, max_len, dropout
        position = torch.arange(max_len, dtype=torch.float64).unsqueeze(1)  # (L, 1)
        rates = torch.pow(10000.0, -torch.arange(0, d_model, 2, dtype=torch.float64) / d_model)
        table = torch.zeros(max_len, d_model, dtype=torch.float64)
        table[:, 0::2] = torch.sin(position * rates)  # even features: sin
        table[:, 1::2] = torch.cos(position * rates)  # odd features: cos, same frequency
        self.register_buffer("table", table.float(), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        length = x.shape[1]
        if length > self.max_len:
            raise ValueError(
                f"a sequence of {length} positions is longer than max_len {self.max_len}"
            )
        out = x + self.table[:length].to(x.dtype)
        if self.training and self.dropout > 0:
            out = nn.functional.dropout(out, p=self.dropout)
        return out


class ScratchLSTMCell(nn.Module):
    """One LSTM cell, one time step (BUILD-1.8; ``lstm`` with ``implementation: scratch``).

    Parameters, as in ``torch.nn.LSTMCell``: ``weight_ih`` (4H × I), ``weight_hh``
    (4H × H), and with bias ``bias_ih`` and ``bias_hh`` (4H each); the four gate
    blocks are in PyTorch's order: input, forget, cell (g), output.

    ``forward(x, state)`` must satisfy:

    1. ``x`` is ``(N, I)``; ``state`` is ``(h, c)``, each ``(N, H)``, or ``None`` for zeros.
    2. ``gates = x Wᵢₕᵀ + bᵢₕ + h Wₕₕᵀ + bₕₕ``, split into i, f, g, o;
       ``i, f, o = σ(·)``, ``g = tanh(·)``; ``c' = f ⊙ c + i ⊙ g``; ``h' = o ⊙ tanh(c')``.
    3. Returns ``(h', c')``.
    4. Given the same weights, equals ``torch.nn.LSTMCell`` within 1e-6, step after
       step.
    """

    def __init__(self, input_size: int, hidden_size: int, *, bias: bool = True) -> None:
        super().__init__()
        self.input_size, self.hidden_size = input_size, hidden_size
        self.weight_ih = nn.Parameter(torch.empty(4 * hidden_size, input_size))
        self.weight_hh = nn.Parameter(torch.empty(4 * hidden_size, hidden_size))
        self.bias_ih = nn.Parameter(torch.empty(4 * hidden_size)) if bias else None
        self.bias_hh = nn.Parameter(torch.empty(4 * hidden_size)) if bias else None
        bound = 1 / math.sqrt(hidden_size)  # PyTorch's own initialisation
        for parameter in self.parameters():
            nn.init.uniform_(parameter, -bound, bound)

    def forward(
        self, x: torch.Tensor, state: tuple[torch.Tensor, torch.Tensor] | None = None
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if state is None:
            zeros = x.new_zeros(x.shape[0], self.hidden_size)
            state = (zeros, zeros)
        h, c = state
        gates = x @ self.weight_ih.T + h @ self.weight_hh.T
        if self.bias_ih is not None and self.bias_hh is not None:
            gates = gates + self.bias_ih + self.bias_hh
        i, f, g, o = gates.chunk(4, dim=-1)  # PyTorch's order: input, forget, cell, output
        i, f, o = torch.sigmoid(i), torch.sigmoid(f), torch.sigmoid(o)
        g = torch.tanh(g)
        c = f * c + i * g  # forget part of the old memory, write part of the new
        h = o * torch.tanh(c)  # expose part of the memory as the output
        return h, c
