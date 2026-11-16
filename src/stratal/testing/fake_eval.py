"""Replaces the evaluate stage's statistics with fakes, in the processes that import it.

Name this module in ``STRATAL_EXTRA_STAGES`` to run whole pipelines through the
report before EVAL-1.1's units exist. The report then has every part, but
lumped rejection counts, no filter rate, crude distributions and [0, 1]
intervals (see :mod:`stratal.testing.fakes`).
"""

from stratal.eval.report import EvaluateConfig, ReportStats
from stratal.runtime.stages import StageDef, make_evaluate_runner, register
from stratal.testing.fakes import (
    crude_score_distribution,
    lumped_rejection_counts,
    no_filter_rate,
    overall_only_acceptance,
)

FAKE_STATS = ReportStats(
    rejections=lumped_rejection_counts,
    filter_rate=no_filter_rate,
    distribution=crude_score_distribution,
    acceptance=overall_only_acceptance,
)

register(StageDef("evaluate", "cpu", EvaluateConfig, make_evaluate_runner(FAKE_STATS)))
