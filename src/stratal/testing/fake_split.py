"""Replaces the split stage's owner units with fakes, in the processes that import it.

Name this module in ``STRATAL_EXTRA_STAGES`` to run whole pipelines through the
split before DATA-4.1 to DATA-4.5 exist. Every row is its own group, there is
one stratum, and new groups are placed by hashing (see
:mod:`stratal.testing.fakes`), so shares are right only on average and near
pairs can leak; the stage's own leakage check reports any that do.
"""

from stratal.data.split_stage import SplitConfig, SplitUnits
from stratal.runtime.stages import StageDef, make_split_runner, register
from stratal.testing.fakes import hashed_splits, one_stratum, singleton_split_groups

FAKE_UNITS = SplitUnits(group=singleton_split_groups, strata=one_stratum, assign=hashed_splits)

register(StageDef("split", "cpu", SplitConfig, make_split_runner(FAKE_UNITS)))
