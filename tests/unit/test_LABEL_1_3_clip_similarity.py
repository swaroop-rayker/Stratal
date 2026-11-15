"""LABEL-1.3: CLIP image-caption similarity. An owner unit.

Strict expected failures until ``stratal.label.scoring.clip_similarity`` is
written (ADR-0012); then remove the marker. The formula is the owner's; these
tests pin only the contract.
"""

import math

import pytest

from stratal.label.scoring import clip_similarity

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="owner unit LABEL-1.3 not written yet"
)


def test_LABEL_1_3_one_finite_score_per_pair() -> None:
    images = [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.3, 0.3, 0.9]]
    texts = [[1.0, 0.1, 0.0], [0.0, 1.0, 0.5], [0.2, 0.1, 1.0]]

    scores = clip_similarity(images, texts)

    assert len(scores) == 3
    assert all(isinstance(s, float) and math.isfinite(s) for s in scores)
    assert scores == clip_similarity(images, texts)


def test_LABEL_1_3_vector_length_does_not_matter() -> None:
    image, text = [0.2, 0.5, -0.1], [0.3, 0.4, 0.0]

    (base,) = clip_similarity([image], [text])
    (scaled,) = clip_similarity([[v * 7.5 for v in image]], [[v * 0.01 for v in text]])

    assert scaled == pytest.approx(base, abs=1e-6)


def test_LABEL_1_3_aligned_beats_orthogonal_beats_opposite() -> None:
    image = [1.0, 0.0]
    aligned, orthogonal, opposite = clip_similarity(
        [image, image, image], [[2.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
    )

    assert aligned > orthogonal >= opposite


def test_LABEL_1_3_bad_input_is_refused() -> None:
    assert clip_similarity([], []) == []
    with pytest.raises(ValueError):
        clip_similarity([[1.0, 0.0]], [])
    with pytest.raises(ValueError):
        clip_similarity([[1.0, 0.0]], [[1.0, 0.0, 0.0]])
    with pytest.raises(ValueError):
        clip_similarity([[0.0, 0.0]], [[1.0, 0.0]])
