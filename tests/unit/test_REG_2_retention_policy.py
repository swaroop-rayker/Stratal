"""REG-2.1 to REG-2.5, REG-2.8: the keep rule on its own (stratal.registry.policy)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stratal.registry.policy import Decision, VersionFacts, plan_lineage

T0 = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)


def _lineage(runs: int, **changes: dict[str, Any]) -> list[VersionFacts]:
    """A base and ``runs`` trained versions r1 (oldest) … rN (newest), 100 bytes each."""
    versions = [VersionFacts("base", "base", "present", T0, 100)]
    for n in range(1, runs + 1):
        version = VersionFacts(f"r{n}", "trained", "present", T0 + timedelta(minutes=n), 100)
        versions.append(replace(version, **changes.get(f"r{n}", {})))
    return versions


def _ids(decisions: list[Decision]) -> list[str]:
    return sorted(d.version_id for d in decisions)


def test_REG_2_2_default_keeps_only_base_and_latest() -> None:
    plan = plan_lineage(_lineage(3), keep_intermediate=0)

    assert plan.latest == "r3"
    assert _ids(plan.keep) == ["base", "r3"]
    assert _ids(plan.delete) == ["r1", "r2"]
    assert plan.bytes_to_free == 200


def test_REG_2_3_n_2_keeps_the_two_before_latest() -> None:
    plan = plan_lineage(_lineage(5), keep_intermediate=2)

    assert _ids(plan.keep) == ["base", "r3", "r4", "r5"]
    assert _ids(plan.delete) == ["r1", "r2"]


def test_REG_2_3_all_keeps_everything() -> None:
    plan = plan_lineage(_lineage(5), keep_intermediate=None)

    assert plan.delete == [] and len(plan.keep) == 6


def test_REG_2_1_the_base_is_kept_whatever_n_is() -> None:
    for n in (0, 1, None):
        assert "base" in _ids(plan_lineage(_lineage(10), keep_intermediate=n).keep)
    assert _ids(plan_lineage(_lineage(0), keep_intermediate=0).keep) == ["base"]


def test_REG_2_5_a_pinned_version_survives_n_0() -> None:
    plan = plan_lineage(_lineage(3, r2={"pinned": True}), keep_intermediate=0)

    assert _ids(plan.delete) == ["r1"]
    assert "pinned" in next(d.reason for d in plan.keep if d.version_id == "r2")


def test_REG_2_8_in_use_and_parent_versions_are_blocked_not_deleted() -> None:
    plan = plan_lineage(
        _lineage(4, r1={"in_use": "loaded for inference"}, r2={"children": ("r9",)}),
        keep_intermediate=0,
    )

    assert _ids(plan.blocked) == ["r1", "r2"]
    assert _ids(plan.delete) == ["r3"]
    reasons = {d.version_id: d.reason for d in plan.blocked}
    assert "loaded for inference" in reasons["r1"] and "r9" in reasons["r2"]


def test_REG_2_6_partial_versions_and_deleted_weights_are_left_alone() -> None:
    versions = [
        *_lineage(2),
        VersionFacts("gone", "trained", "weights_deleted", T0 - timedelta(days=1)),
        VersionFacts("paused", "partial", "present", T0 + timedelta(hours=1)),
        VersionFacts("crashed", "trained", "deleting", T0 - timedelta(hours=1), 100),
    ]
    plan = plan_lineage(versions, keep_intermediate=0)

    assert plan.latest == "r2", "a partial run is not the latest"
    assert _ids(plan.delete) == ["r1"]
    assert "paused" in _ids(plan.keep)
    every = _ids(plan.delete) + _ids(plan.keep) + _ids(plan.blocked)
    assert "gone" not in every and "crashed" not in every


def test_REG_2_3_a_negative_n_is_refused() -> None:
    with pytest.raises(ValueError):
        plan_lineage(_lineage(1), keep_intermediate=-1)
