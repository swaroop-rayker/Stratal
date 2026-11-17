"""EVAL-1.2: the right metric set for each task, with values checked by hand.

Strict expected failures until the owner writes ``stratal.eval.task_metrics``
(ADR-0012); remove the markers then. The metric-set mapping is plumbing and
passes already.
"""

import math

import pytest

from stratal.eval import task_metrics as tm

owner_unit = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="owner unit")


def test_EVAL_1_2_each_task_names_its_metric_set() -> None:
    assert tm.metric_set("classification") == ("accuracy", "macro_f1", "confusion_matrix")
    assert tm.metric_set("regression") == ("mse", "mae", "r2")
    assert tm.metric_set("clustering") == ("silhouette", "davies_bouldin")
    assert tm.metric_set("language_model") == ("perplexity",)
    assert tm.metric_set("translation") == ("bleu", "chrf")
    assert tm.metric_set("captioning") == ("clip_score", "cider_d")


@owner_unit
def test_EVAL_1_2_classification_accuracy_f1_and_confusion_matrix() -> None:
    # Per class: 0 → P 1/2, R 1/2, F1 0.5; 1 → P 2/3, R 1, F1 0.8; 2 → P 1, R 1/2, F1 2/3.
    result = tm.classification([0, 0, 1, 1, 2, 2], [0, 1, 1, 1, 2, 0], labels=[0, 1, 2])

    assert result.accuracy == pytest.approx(4 / 6)
    assert result.per_class_f1 == pytest.approx({0: 0.5, 1: 0.8, 2: 2 / 3})
    assert result.macro_f1 == pytest.approx((0.5 + 0.8 + 2 / 3) / 3)
    assert result.confusion_matrix == [[1, 1, 0], [0, 2, 0], [1, 0, 1]]


@owner_unit
def test_EVAL_1_2_a_class_never_predicted_or_present_has_f1_zero() -> None:
    result = tm.classification(["cat", "cat"], ["cat", "cat"], labels=["cat", "dog"])
    assert result.per_class_f1["dog"] == 0.0
    assert result.macro_f1 == pytest.approx(0.5)


@owner_unit
def test_EVAL_1_2_classification_refuses_bad_input() -> None:
    with pytest.raises(ValueError):
        tm.classification([0, 1], [0], labels=[0, 1])
    with pytest.raises(ValueError):
        tm.classification([0, 3], [0, 1], labels=[0, 1])


@owner_unit
def test_EVAL_1_2_regression_matches_the_textbook_example() -> None:
    # scikit-learn's documentation example for mean_squared_error and r2_score.
    result = tm.regression([3, -0.5, 2, 7], [2.5, 0.0, 2, 8])
    assert result.mse == pytest.approx(0.375)
    assert result.mae == pytest.approx(0.5)
    assert result.r2 == pytest.approx(0.948608137)


@owner_unit
def test_EVAL_1_2_clustering_silhouette_and_davies_bouldin() -> None:
    # Two clusters, each a vertical pair, 4 apart. For every point: a = 1,
    # b = (4 + √17) / 2, s = (b − a) / b. Centroids 4 apart, S = 0.5 each: DB = 0.25.
    points = [[0, 0], [0, 1], [4, 0], [4, 1]]
    result = tm.clustering(points, [0, 0, 1, 1])
    b = (4 + math.sqrt(17)) / 2
    assert result.silhouette == pytest.approx((b - 1) / b)
    assert result.davies_bouldin == pytest.approx(0.25)


@owner_unit
def test_EVAL_1_2_perplexity_is_the_inverse_geometric_mean_probability() -> None:
    assert tm.perplexity([math.log(0.5), math.log(0.25)]) == pytest.approx(math.sqrt(8))
    with pytest.raises(ValueError):
        tm.perplexity([0.1])


@owner_unit
def test_EVAL_1_2_translation_identical_text_scores_100() -> None:
    result = tm.translation(["the cat sat on the mat"], ["the cat sat on the mat"])
    assert result.bleu == pytest.approx(100.0)
    assert result.chrf == pytest.approx(100.0)
    unrelated = tm.translation(["xyz qqq"], ["the cat sat on the mat"])
    assert unrelated.bleu == pytest.approx(0.0)


@owner_unit
def test_EVAL_1_2_cider_d_prefers_the_matching_caption() -> None:
    references = [
        ["a dog runs on the beach", "a dog running on sand"],
        ["two cups of coffee on a table"],
    ]
    matching = tm.cider_d(["a dog runs on the beach", "two cups of coffee on a table"], references)
    unrelated = tm.cider_d(["a red bus in the city", "a man rides a horse"], references)
    assert matching > unrelated
    with pytest.raises(ValueError):
        tm.cider_d(["a dog"], [[]])


@owner_unit
def test_EVAL_1_2_mean_clip_score() -> None:
    assert tm.mean_clip_score([0.2, 0.3, 0.4]) == pytest.approx(0.3)
