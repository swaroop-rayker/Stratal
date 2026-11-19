"""Task metrics by type (EVAL-1.2). Owner units (ADR-0012).

EVAL-1.2: classification (accuracy, F1, confusion matrix), regression (MSE, MAE,
R²), clustering (silhouette, Davies–Bouldin), language models (perplexity),
translation (BLEU, chrF) and captioning (CLIP score, CIDEr). CIDEr is CIDEr-D,
written in house with no Java dependency, and computed only where reference
captions exist (SPEC CL-13).

The stubs, contracts and tests were delivered at the start of M8. At M10's
kickoff the owner asked the agent to write the bodies (ADR-0012's log, K1) and
chose libraries in production (K2): scikit-learn's metrics for classification,
regression and clustering, sacreBLEU for BLEU and chrF. CIDEr-D is written here
(CL-13), and so are perplexity and the mean CLIP score, which are one line each
(ADR-0019 rule 24).

The libraries are imported inside the functions that use them, so ``api`` can
import this module for :func:`metric_set` without them (it has no ML libraries).
They are present in both worker images. Inputs are checked here, before a
library sees them, so every contract's ``ValueError`` holds whatever the
library would do.

Every function is pure: plain Python values in, plain values out. Tests:
``tests/unit/test_EVAL_1_2_task_metrics.py``.
"""

import importlib
import math
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

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


def _library(name: str) -> Any:
    """A metrics library, imported where it is used (it is absent from ``api`` and ``dev``)."""
    return importlib.import_module(name)


def _pairs(first: Sequence[Any], second: Sequence[Any], what: str) -> None:
    if len(first) != len(second):
        raise ValueError(f"{what}: {len(first)} values against {len(second)}")
    if not first:
        raise ValueError(f"{what}: nothing to score")


def _finite(values: Sequence[float], what: str) -> list[float]:
    numbers = [float(v) for v in values]
    if not all(math.isfinite(v) for v in numbers):
        raise ValueError(f"{what}: a value is not finite")
    return numbers


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
    _pairs(y_true, y_pred, "classification")
    order = list(labels)
    index = {label: i for i, label in enumerate(order)}
    if len(index) != len(order):
        raise ValueError("classification: a label is listed twice")
    unknown = [v for v in (*y_true, *y_pred) if v not in index]
    if unknown:
        raise ValueError(f"classification: {unknown[0]!r} is not one of the labels")
    # scikit-learn wants one type of label; positions in ``labels`` are that.
    true = [index[v] for v in y_true]
    pred = [index[v] for v in y_pred]
    positions = list(range(len(order)))
    metrics = _library("sklearn.metrics")
    f1 = metrics.f1_score(true, pred, labels=positions, average=None, zero_division=0.0)
    matrix = metrics.confusion_matrix(true, pred, labels=positions)
    per_class = {label: float(f1[i]) for i, label in enumerate(order)}
    return ClassificationMetrics(
        accuracy=float(metrics.accuracy_score(true, pred)),
        macro_f1=sum(per_class.values()) / len(per_class),
        per_class_f1=per_class,
        confusion_matrix=[[int(n) for n in row] for row in matrix],
        labels=order,
    )


def regression(y_true: Sequence[float], y_pred: Sequence[float]) -> RegressionMetrics:
    """Must satisfy:

    1. ``mse`` = mean((y − ŷ)²); ``mae`` = mean(|y − ŷ|).
    2. ``r2`` = 1 − Σ(y − ŷ)² / Σ(y − ȳ)²; when every ``y_true`` is the same,
       ``r2`` is 1.0 if the predictions are exact and 0.0 otherwise.
    3. ``ValueError`` for empty or unequal-length sequences or non-finite values.
    """
    _pairs(y_true, y_pred, "regression")
    true, pred = _finite(y_true, "regression"), _finite(y_pred, "regression")
    metrics = _library("sklearn.metrics")
    # force_finite (scikit-learn's default) gives rule 2's 1.0 or 0.0 for a constant target.
    return RegressionMetrics(
        mse=float(metrics.mean_squared_error(true, pred)),
        mae=float(metrics.mean_absolute_error(true, pred)),
        r2=float(metrics.r2_score(true, pred)),
    )


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
    _pairs(points, labels, "clustering")
    dims = {len(point) for point in points}
    if len(dims) != 1 or 0 in dims:
        raise ValueError("clustering: points must all have the same, non-zero dimension")
    clusters = len(set(labels))
    if clusters < 2 or clusters >= len(points):
        raise ValueError(
            f"clustering: {clusters} clusters for {len(points)} points; need 2 to points − 1"
        )
    rows = [_finite(point, "clustering") for point in points]
    metrics = _library("sklearn.metrics")
    return ClusteringMetrics(
        silhouette=float(metrics.silhouette_score(rows, list(labels), metric="euclidean")),
        davies_bouldin=float(metrics.davies_bouldin_score(rows, list(labels))),
    )


def perplexity(token_log_probs: Sequence[float]) -> float:
    """exp(−mean(log p)) over the tokens' natural-log probabilities.

    ``ValueError`` when empty or when a value is positive or not finite.
    """
    if not token_log_probs:
        raise ValueError("perplexity: no tokens")
    values = _finite(token_log_probs, "perplexity")
    if any(v > 0 for v in values):
        raise ValueError("perplexity: a log-probability is above 0")
    return math.exp(-sum(values) / len(values))


def translation(hypotheses: Sequence[str], references: Sequence[str]) -> TranslationMetrics:
    """Corpus-level BLEU and chrF on a 0–100 scale, as sacreBLEU computes them by default.

    BLEU: modified n-gram precisions for n = 1..4, geometric mean, brevity penalty,
    13a tokenisation. chrF: character n-grams up to 6, β = 2. One reference per
    hypothesis. Must satisfy: identical text scores 100 on both; text with no
    shared word and no shared character n-gram above length 1 scores 0 BLEU;
    ``ValueError`` for empty or unequal-length inputs.
    """
    _pairs(hypotheses, references, "translation")
    sacrebleu = _library("sacrebleu")
    hyps, refs = list(hypotheses), [list(references)]
    return TranslationMetrics(
        bleu=float(sacrebleu.corpus_bleu(hyps, refs).score),
        chrf=float(sacrebleu.corpus_chrf(hyps, refs).score),
    )


def cider_d(candidates: Sequence[str], references: Sequence[Sequence[str]]) -> float:
    """Corpus CIDEr-D (Vedantam et al., 2015), as used for COCO captions (CL-13).

    n-grams for n = 1..4 after lower-casing and splitting on spaces; TF-IDF
    weights with document frequencies from the references of the whole corpus;
    cosine similarity per n with the clipped candidate counts; a Gaussian length
    penalty with σ = 6; averaged over references and n, times 10. Must satisfy:
    a candidate equal to all its references scores higher than an unrelated one;
    ``ValueError`` when an item has no references or the lengths differ.
    """
    _pairs(candidates, references, "cider_d")
    if any(not refs for refs in references):
        raise ValueError("cider_d: an item has no reference captions")
    cands = [_ngram_counts(c) for c in candidates]
    refs = [[_ngram_counts(r) for r in item] for item in references]
    # Document frequency: in how many items' reference sets each n-gram occurs.
    df: Counter[tuple[str, ...]] = Counter()
    for item in refs:
        df.update({gram for counts in item for gram in counts})
    log_items = math.log(float(len(refs)))
    scores: list[float] = []
    for cand, item in zip(cands, refs, strict=True):
        vec, norm, length = _tfidf(cand, df, log_items)
        total = 0.0
        for ref in item:
            ref_vec, ref_norm, ref_length = _tfidf(ref, df, log_items)
            total += (
                sum(
                    _similarity(vec[n], ref_vec[n], norm[n], ref_norm[n], length - ref_length)
                    for n in range(CIDER_N)
                )
                / CIDER_N
            )
        scores.append(total / len(item) * 10.0)
    return sum(scores) / len(scores)


def mean_clip_score(scores: Sequence[float]) -> float:
    """The mean of the pairs' CLIP scores (the captioning task's CLIP score).

    ``ValueError`` when empty or a value is not finite.
    """
    if not scores:
        raise ValueError("mean_clip_score: no scores")
    values = _finite(scores, "mean_clip_score")
    return sum(values) / len(values)


# ------------------------------------------------------------------------------ CIDEr-D (CL-13)

CIDER_N = 4
CIDER_SIGMA = 6.0


def _ngram_counts(text: str) -> Counter[tuple[str, ...]]:
    words = text.lower().split()
    return Counter(
        tuple(words[i : i + n]) for n in range(1, CIDER_N + 1) for i in range(len(words) - n + 1)
    )


def _tfidf(
    counts: Counter[tuple[str, ...]], df: Counter[tuple[str, ...]], log_items: float
) -> tuple[list[dict[tuple[str, ...], float]], list[float], int]:
    """Per n, the TF-IDF vector and its norm; and the text's length.

    As the reference implementation does, the length is the number of bigrams
    (it is the count it sees at n = 2), and a document frequency below 1 counts as 1.
    """
    vec: list[dict[tuple[str, ...], float]] = [{} for _ in range(CIDER_N)]
    norm = [0.0] * CIDER_N
    length = 0
    for gram, tf in counts.items():
        n = len(gram) - 1
        weight = float(tf) * (log_items - math.log(max(1.0, float(df[gram]))))
        vec[n][gram] = weight
        norm[n] += weight * weight
        if n == 1:
            length += tf
    return vec, [math.sqrt(x) for x in norm], length


def _similarity(
    cand: dict[tuple[str, ...], float],
    ref: dict[tuple[str, ...], float],
    cand_norm: float,
    ref_norm: float,
    delta: int,
) -> float:
    """Cosine with the candidate's weights clipped by the reference's (the "D" in CIDEr-D),
    times a Gaussian penalty on the length difference."""
    value = sum(
        min(weight, ref.get(gram, 0.0)) * ref.get(gram, 0.0) for gram, weight in cand.items()
    )
    if cand_norm != 0.0 and ref_norm != 0.0:
        value /= cand_norm * ref_norm
    return value * math.exp(-(delta**2) / (2 * CIDER_SIGMA**2))
