"""Replaces the clean stage's owner units with fakes, in the processes that import it.

Name this module in ``STRATAL_EXTRA_STAGES`` to run whole pipelines through the
clean stage before DATA-2.1 and DATA-2.2 exist. The fakes find no duplicates,
so use folders without planted copies (see :mod:`stratal.testing.fakes`).
"""

from stratal.data.clean import CleanConfig, DedupeUnits
from stratal.runtime.stages import StageDef, make_clean_runner, register
from stratal.testing.fakes import fake_perceptual_hash, no_exact_duplicates, no_near_duplicates

FAKE_UNITS = DedupeUnits(
    exact=no_exact_duplicates, phash=fake_perceptual_hash, near=no_near_duplicates
)

register(StageDef("clean", "cpu", CleanConfig, make_clean_runner(FAKE_UNITS)))
