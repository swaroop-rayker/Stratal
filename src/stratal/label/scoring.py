"""CLIP image-caption similarity (LABEL-1.3). An owner unit (ADR-0012).

The label stage encodes images and captions with the CLIP model (the adapter
returns raw, unnormalised embeddings) and calls this function to turn each pair
of embeddings into one score. The score is stored as ``clip_score`` and the
filter compares it with a threshold (LABEL-1.4, OC-5).

Tests: ``tests/unit/test_LABEL_1_3_clip_similarity.py`` (strict expected failures
until this is written).
"""

from collections.abc import Sequence

from stratal.label.models import Vector

ALGORITHM_VERSION = 1
"""Bump when the formula changes; it is part of the cache key and the stage config."""


def clip_similarity(image_vectors: Sequence[Vector], text_vectors: Sequence[Vector]) -> list[float]:
    """One score per pair: ``image_vectors[i]`` with ``text_vectors[i]``.

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

    Left to you, and to record in DECISIONS.md: the formula (plain cosine, or
    CLIPScore's 2.5 · max(cos, 0) from Hessel et al., 2021, or another), and
    therefore the scale the OC-5 threshold will be chosen on.
    """
    raise NotImplementedError("Owner unit LABEL-1.3: clip_similarity")
