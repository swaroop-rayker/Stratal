# pyright: basic, reportMissingImports=false
# PyTorch exists only in the GPU image, so this one module is checked in basic mode
# (DECISIONS.md, "untyped libraries behind one module each").
"""The semantic clusters' links as blocked matrix products (DATA-2.3, ADR-0013 rule 1)."""

import torch

BLOCK = 2048


def links(unit: list[list[float]], threshold: float) -> list[tuple[int, int]]:
    """Every pair ``i < j`` of normalised vectors whose dot product is at least
    ``threshold``, on the GPU when there is one."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    matrix = torch.tensor(unit, dtype=torch.float32, device=device)
    found: list[tuple[int, int]] = []
    with torch.no_grad():
        for start in range(0, matrix.shape[0], BLOCK):
            block = matrix[start : start + BLOCK] @ matrix.T
            rows, cols = torch.nonzero(block >= threshold, as_tuple=True)
            for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
                i = start + int(r)
                if i < int(c):
                    found.append((i, int(c)))
            del block
    del matrix
    return found
