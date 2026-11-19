"""Task metrics by type (EVAL-1.2). Owner units (ADR-0012).

EVAL-1.2: classification (accuracy, F1, confusion matrix), regression (MSE, MAE,
R²), clustering (silhouette, Davies–Bouldin), language models (perplexity),
translation (BLEU, chrF) and captioning (CLIP score, CIDEr). CIDEr is CIDEr-D,
written in house with no Java dependency, and computed only where reference
captions exist (SPEC CL-13).

The agent wrote the stubs, the contracts and the tests; the owner writes the
bodies marked ``raise NotImplementedError``. The stub was delivered at the start
of M8 so the phase-2 CNN can be scored as soon as it exists; the requirement is
M10's. Whether to use libraries (scikit-learn, sacrebleu) or own code is the
owner's choice (MILESTONES, phase-2 dependencies).

Every function is pure: plain Python values in, plain values out. Tests:
``tests/unit/test_EVAL_1_2_task_metrics.py``.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Task = Literal[
    "classification", "regression", "clustering", "language_model", "translation", "captioning"
]

METRIC_SETS: Mapping[Task, tuple[str, ...]] = {
    "classification": ("accuracy", "macro_f1", "confusion_matrix"),
    "regression": ("mse", "mae", "r2"),
    "clustering": ("silhouette", "davies_bouldin"),
    "language_model": ("perplexity",),
    "translation": ("bleu", "chrf"),
    "captioning": ("clip_score", "cider_d"),
}
"""Which metrics each task reports (EVAL-1.2.AC1). Plumbing: the list is SPEC's."""


def metric_set(task: Task) -> tuple[str, ...]:
    """The metric names a task reports, in display order."""
    return METRIC_SETS[task]


@dataclass(frozen=True)
class ClassificationMetrics:
    accuracy: float
    macro_f1: float
    per_class_f1: dict[Hashable, float]
    confusion_matrix: list[list[int]]
    """Rows are true labels, columns predicted labels, both in ``labels`` order."""
    labels: list[Hashable]


@dataclass(frozen=True)
class RegressionMetrics:
    mse: float
    mae: float
    r2: float


@dataclass(frozen=True)
class ClusteringMetrics:
    silhouette: float
    davies_bouldin: float


@dataclass(frozen=True)
class TranslationMetrics:
    bleu: float
    chrf: float


def classification(
    y_true: Sequence[Hashable], y_pred: Sequence[Hashable], *, labels: Sequence[Hashable]
) -> ClassificationMetrics:
    """Must satisfy:

    1. ``accuracy`` is the share of positions where prediction equals truth.
    2. Per class, F1 = 2·P·R / (P + R) with precision P = TP / (TP + FP) and
       recall R = TP / (TP + FN), each taken as 0 when its denominator is 0;
       a class with P + R = 0 has F1 = 0 (as scikit-learn does by default).
       ``macro_f1`` is their unweighted mean over ``labels``.
    3. ``confusion_matrix[i][j]`` counts items whose true label is ``labels[i]``
       and predicted label ``labels[j]``.
    4. ``ValueError`` when the sequences differ in length or are empty, or when
       a value is not in ``labels``.
    """
    raise NotImplementedError("EVAL-1.2: classification metrics are the owner's to write")


def regression(y_true: Sequence[float], y_pred: Sequence[float]) -> RegressionMetrics:
    """Must satisfy:

    1. ``mse`` = mean((y − ŷ)²); ``mae`` = mean(|y − ŷ|).
    2. ``r2`` = 1 − Σ(y − ŷ)² / Σ(y − ȳ)²; when every ``y_true`` is the same,
       ``r2`` is 1.0 if the predictions are exact and 0.0 otherwise.
    3. ``ValueError`` for empty or unequal-length sequences or non-finite values.
    """
    raise NotImplementedError("EVAL-1.2: regression metrics are the owner's to write")


def clustering(points: Sequence[Sequence[float]], labels: Sequence[int]) -> ClusteringMetrics:
    """Euclidean distances. Must satisfy:

    1. Silhouette: for each point, a = mean distance to the other members of its
       cluster, b = the smallest mean distance to the members of another
       cluster, s = (b − a) / max(a, b), or 0 for a point alone in its cluster.
       ``silhouette`` is the mean of s over all points.
    2. Davies–Bouldin: S_i = mean distance of cluster i's points to its centroid;
       M_ij = distance between centroids; for each i, R_i = max over j ≠ i of
       (S_i + S_j) / M_ij; ``davies_bouldin`` is the mean of R_i.
    3. ``ValueError`` when there are fewer than 2 clusters, as many clusters as
       points, unequal lengths, or points of different dimensions.
    """
    raise NotImplementedError("EVAL-1.2: clustering metrics are the owner's to write")


def perplexity(token_log_probs: Sequence[float]) -> float:
    """exp(−mean(log p)) over the tokens' natural-log probabilities.

    ``ValueError`` when empty or when a value is positive or not finite.
    """
    raise NotImplementedError("EVAL-1.2: perplexity is the owner's to write")


def translation(hypotheses: Sequence[str], references: Sequence[str]) -> TranslationMetrics:
    """Corpus-level BLEU and chrF on a 0–100 scale, as sacreBLEU computes them by default.

    BLEU: modified n-gram precisions for n = 1..4, geometric mean, brevity penalty,
    13a tokenisation. chrF: character n-grams up to 6, β = 2. One reference per
    hypothesis. Must satisfy: identical text scores 100 on both; text with no
    shared word and no shared character n-gram above length 1 scores 0 BLEU;
    ``ValueError`` for empty or unequal-length inputs.
    """
    raise NotImplementedError("EVAL-1.2: BLEU and chrF are the owner's to write")


def cider_d(candidates: Sequence[str], references: Sequence[Sequence[str]]) -> float:
    """Corpus CIDEr-D (Vedantam et al., 2015), as used for COCO captions (CL-13).

    n-grams for n = 1..4 after lower-casing and splitting on spaces; TF-IDF
    weights with document frequencies from the references of the whole corpus;
    cosine similarity per n with the clipped candidate counts; a Gaussian length
    penalty with σ = 6; averaged over references and n, times 10. Must satisfy:
    a candidate equal to all its references scores higher than an unrelated one;
    ``ValueError`` when an item has no references or the lengths differ.
    """
    raise NotImplementedError("EVAL-1.2: CIDEr-D is the owner's to write")


def mean_clip_score(scores: Sequence[float]) -> float:
    """The mean of the pairs' CLIP scores (the captioning task's CLIP score).

    ``ValueError`` when empty or a value is not finite.
    """
    raise NotImplementedError("EVAL-1.2: the captioning CLIP score is the owner's to write")
