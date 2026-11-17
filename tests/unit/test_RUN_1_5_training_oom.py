"""RUN-1.5: an oversized training batch fails cleanly with a suggested smaller one.

Strict expected failures until the owner writes the units in
``stratal.train.memory`` (ADR-0012); remove the markers then.
"""

import math

import pytest

from stratal.core.errors import GpuOom
from stratal.train.memory import SAFETY_MARGIN, suggest_batch_size, training_oom_error

owner_unit = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="owner unit")


@owner_unit
def test_RUN_1_5_the_suggestion_is_the_largest_power_of_two_that_fits() -> None:
    # 0.9 * 4000 = 3600 MB to plan with; 3600 - 1000 = 2600 MB for activations;
    # 2600 / 150 = 17.3 samples, so 16.
    assert suggest_batch_size(failed_batch=32, fixed_mb=1000, per_sample_mb=150, cap_mb=4000) == 16


@owner_unit
def test_RUN_1_5_the_suggestion_is_always_smaller_than_the_failed_batch() -> None:
    # The estimate says 64 would fit, but 8 just failed: suggest 4, never more.
    assert suggest_batch_size(failed_batch=8, fixed_mb=100, per_sample_mb=10, cap_mb=4000) == 4


@owner_unit
def test_RUN_1_5_the_boundary_counts_as_fitting() -> None:
    cap = 1000.0
    fixed = SAFETY_MARGIN * cap - 8 * 50  # exactly room for 8 samples of 50 MB
    assert suggest_batch_size(failed_batch=16, fixed_mb=fixed, per_sample_mb=50, cap_mb=cap) == 8


@owner_unit
def test_RUN_1_5_no_suggestion_when_nothing_fits() -> None:
    assert suggest_batch_size(failed_batch=16, fixed_mb=3700, per_sample_mb=10, cap_mb=4000) is None
    assert suggest_batch_size(failed_batch=1, fixed_mb=10, per_sample_mb=1, cap_mb=4000) is None


@owner_unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"failed_batch": 0, "fixed_mb": 1, "per_sample_mb": 1, "cap_mb": 10},
        {"failed_batch": 4, "fixed_mb": -1, "per_sample_mb": 1, "cap_mb": 10},
        {"failed_batch": 4, "fixed_mb": 1, "per_sample_mb": math.nan, "cap_mb": 10},
        {"failed_batch": 4, "fixed_mb": 1, "per_sample_mb": 1, "cap_mb": 0},
    ],
)
def test_RUN_1_5_invalid_inputs_are_refused(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        suggest_batch_size(**kwargs)  # type: ignore[arg-type]


@owner_unit
def test_RUN_1_5_the_error_says_reduce_batch_size_from_32_to_16() -> None:
    error = training_oom_error(failed_batch=32, suggested=16, peak_mb=5100, cap_mb=5220)

    assert isinstance(error, GpuOom) and error.code == "gpu_oom"
    assert "reduce batch size from 32 → 16" in error.detail
    assert error.suggested_action is not None and "batch_size" in error.suggested_action
    assert "16" in error.suggested_action
    assert "5.0 GB" in error.detail, "the peak, 5100 MB / 1024"
    assert "5.1 GB" in error.detail, "the cap, 5220 MB / 1024"


@owner_unit
def test_RUN_1_5_without_a_suggestion_the_error_offers_a_smaller_model_or_the_cpu() -> None:
    error = training_oom_error(failed_batch=1, suggested=None, peak_mb=5100, cap_mb=5220)

    assert isinstance(error, GpuOom)
    assert "batch of 1" in error.detail
    assert error.suggested_action is not None and "device: cpu" in error.suggested_action
