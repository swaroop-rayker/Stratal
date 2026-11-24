# pyright: basic, reportMissingImports=false
"""REG-1.4 (M11, SCR-11 v2 question 2): Try it says how a text was read.

The predict response for a text carries its token count, the tokens outside the
vocabulary and where it was cut. Needs PyTorch (the inference inputs module).
"""

import pytest

pytest.importorskip("torch")

from stratal.data.tokenize import PAD, UNK, Vocabulary
from stratal.inference.inputs import text_facts

pytestmark = pytest.mark.torch

VOCAB = Vocabulary((PAD, UNK, "a", "man", "riding", "wave", "on", "top", "of", "surfboard", "."))


def test_REG_1_4_a_known_text_counts_its_tokens_punctuation_included() -> None:
    facts = text_facts("A man riding a wave on top of a surfboard.", VOCAB, 16)
    assert facts == {"tokens": 11, "unknown": 0, "truncated_at": None}


def test_REG_1_4_unknown_words_and_a_cut_are_reported() -> None:
    facts = text_facts("A dog riding a wave on top of a surfboard near the pier.", VOCAB, 8)
    assert facts == {"tokens": 14, "unknown": 4, "truncated_at": 8}, "dog, near, the, pier"
