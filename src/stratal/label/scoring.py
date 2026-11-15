"""CLIP image-caption similarity (LABEL-1.3).

An owner unit (ADR-0012), written by the agent at the owner's request on
2026-11-15. How and why it works is explained in ``docs/learning/sys-design.md``.

The label stage encodes images and captions with the CLIP model (the adapter
returns raw, unnormalised embeddings) and calls this function to turn each pair
of embeddings into one score. The score is stored as ``clip_score`` and the
filter compares it with a threshold (LABEL-1.4, OC-5).

Tests: ``tests/unit/test_LABEL_1_3_clip_similarity.py`` and
``tests/unit/test_LABEL_M4_unit_decisions.py``.
"""

import math
from collections.abc import Sequence

from stratal.label.models import Vector

ALGORITHM_VERSION = 1
"""Bump when the formula changes; it is part of the cache key and the stage config."""


def clip_similarity(image_vectors: Sequence[Vector], text_vectors: Sequence[Vector]) -> list[float]:
    """One score per pair: the cosine between ``image_vectors[i]`` and ``text_vectors[i]``.

    Must satisfy:

    1. The two sequences have the same length and every vector the same
       dimension; otherwise ``ValueError``. Empty input gives an empty list.
    2. Scores are finite floats, and the same inputs always give the same scores.
    3. Scaling either vector by a positive factor does not change its score: the
       embeddings come unnormalised, and their length carries no meaning here.
    4. A pair whose vectors point the same way scores higher than a pair whose
       vectors are orthogonal, which scores higher than one pointing opposite
       ways (unless you clip negatives: then orthogonal and opposite may tie).
    5. A zero vector is a ``ValueError``, not a silent 0 or NaN.

    Formula: plain cosine similarity, ``a·b / (|a| |b|)``, in [-1, 1]. CLIP was
    trained to make exactly this number large for matching pairs (its
    contrastive loss is a softmax over these cosines, times a learned
    temperature), so it is the model's own notion of "matches". It is not
    rescaled or clipped:

    - The OC-5 threshold is then chosen on the scale spike S-06 measured (about
      0.30 for good captions with this model), and stays meaningful if the
      formula is later reported another way.
    - CLIPScore (Hessel et al., 2021) is ``2.5 · max(cos, 0)``; it can be
      computed from the stored value at report time, while clipping here would
      throw away the difference between "unrelated" and "contradictory".

    Values that are not finite numbers are refused like zero vectors: a NaN
    would otherwise compare false with every threshold and silently pass the
    filter. The result is clamped to [-1, 1] against rounding.
    """
    if len(image_vectors) != len(text_vectors):
        raise ValueError(
            f"{len(image_vectors)} image vectors but {len(text_vectors)} text vectors; "
            "the score is per pair"
        )
    if not image_vectors:
        return []
    dimension = len(image_vectors[0])
    scores: list[float] = []
    for index, (image, text) in enumerate(zip(image_vectors, text_vectors, strict=True)):
        if len(image) != dimension or len(text) != dimension:
            raise ValueError(
                f"pair {index}: vectors of length {len(image)} and {len(text)}, "
                f"expected {dimension}"
            )
        image_squared = _squared_length(image, index, "image")
        text_squared = _squared_length(text, index, "text")
        cosine = math.sumprod(image, text) / _denominator(image_squared, text_squared)
        scores.append(min(1.0, max(-1.0, cosine)))
    return scores


def _squared_length(vector: Vector, index: int, side: str) -> float:
    """``|v|²``, refusing zero, infinite and NaN vectors."""
    squared = math.sumprod(vector, vector)
    if not math.isfinite(squared):
        raise ValueError(f"pair {index}: the {side} vector contains a value that is not finite")
    if squared == 0.0:
        raise ValueError(f"pair {index}: the {side} vector is all zeros and has no direction")
    return squared


def _denominator(a_squared: float, b_squared: float) -> float:
    """``|a| |b|`` as ``sqrt(|a|² |b|²)``: one rounding, and exactly ``|a|²`` when a = b.

    The square root of a correctly rounded square gives back the number, so a
    vector scored against itself gives exactly 1.0 rather than 0.9999999999999998.
    Two separate roots are the fallback when the product would overflow or
    underflow, which CLIP's embeddings (lengths around 10) never come near.
    """
    product = a_squared * b_squared
    if math.isfinite(product) and product > 0.0:
        return math.sqrt(product)
    return math.sqrt(a_squared) * math.sqrt(b_squared)
