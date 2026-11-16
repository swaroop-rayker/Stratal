"""DATA-4.1, 4.2, 4.4 to 4.6: the split stage around the owner's units, with fakes.

The owner's units are replaced by the fakes in ``stratal.testing.fakes`` or by
scripted stand-ins, so these tests pass now and test only the agent's shell:
the columns written, the carry-over lookup, the contract checks, the
independent leakage check and the report. The database lookup of the previous
split is tested in ``tests/integration/test_M5_split.py``.
"""

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.split_stage import ContractBroken, SplitConfig, SplitUnits, leakage_check
from stratal.data.splitting import (
    SPLITS,
    TEST,
    TRAIN,
    VAL,
    GroupToAssign,
    LeakageItem,
    Proportions,
    SplitGroup,
)
from stratal.data.versioning import derive_version_identity
from stratal.storage.manifest import Row
from stratal.testing import stagekit
from stratal.testing.fakes import (
    ListSink,
    hashed_splits,
    one_stratum,
    singleton_split_groups,
)

FAKES = SplitUnits(group=singleton_split_groups, strata=one_stratum, assign=hashed_splits)


def _sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _row(
    key: str,
    *,
    status: str = "accepted",
    sha: str | None = None,
    phash: str | None = None,
    dup_of: str | None = None,
    dup_group: str | None = None,
) -> Row:
    sha = sha or _sha(key)
    row: Row = {
        "row_key": key,
        "sha256": sha,
        "status": status,
        "phash": phash or sha[:16],
        "width": 640,
        "height": 480,
        "derived_width": 640,
        "derived_height": 480,
        "caption": "a picture",
        "clip_score": 0.3,
        "dup_of": dup_of,
        "dup_group_id": dup_group,
    }
    if status == "duplicate":
        row.update(decided_at_stage="clean", reason_code="near_duplicate")
    elif status == "filtered":
        row.update(decided_at_stage="filter", reason_code="clip_below_threshold")
    return row


def _version(data_dir: Path, rows: Sequence[Row], *, stage: str = "filter") -> VersionRecord:
    return commit_version(
        data_dir=data_dir,
        stage=stage,
        parent_version_id="ds_parent00000",
        rows=sorted(rows, key=lambda r: r["row_key"]),
        config={"made_by": "test"},
        derive=derive_version_identity,
        sink=ListSink(),
    )


def _plain(count: int, prefix: str = "img") -> list[Row]:
    return [_row(f"{prefix}_{i:04d}.jpg") for i in range(count)]


class RecordingAssign:
    """Records what it is given; returns ``fixed`` for the anchors it names and
    delegates the rest to the hashing fake."""

    def __init__(self, fixed: Mapping[str, str] | None = None) -> None:
        self.fixed = dict(fixed or {})
        self.groups: list[GroupToAssign] = []

    def __call__(
        self, groups: Sequence[GroupToAssign], *, proportions: Proportions, seed: int
    ) -> dict[str, str]:
        self.groups = list(groups)
        result = hashed_splits(groups, proportions=proportions, seed=seed)
        result.update({a: s for a, s in self.fixed.items() if a in result})
        return result

    def previous_of(self, row_key: str) -> str | None:
        for group in self.groups:
            for member in group.members:
                if member.row_key == row_key:
                    return member.previous
        raise KeyError(row_key)


# ------------------------------------------------------------------ DATA-4.1


@pytest.mark.parametrize(
    "fields",
    [
        {"train": 0.9, "val": 0.2, "test": -0.1},
        {"train": 0.8, "val": 0.1, "test": 0.0},
        {"train": 0.8, "val": 0.2, "test": 0.1},
        {"train": float("nan")},
    ],
    ids=["negative", "sums to 0.9", "sums to 1.1", "not a number"],
)
def test_DATA_4_1_proportions_that_are_negative_or_do_not_sum_to_1_are_refused(
    fields: dict[str, float],
) -> None:
    """AC2. The job API turns this ValidationError into ``validation_error`` with the
    field named (tests/integration/test_M5_split.py)."""
    with pytest.raises(ValidationError, match="proportions"):
        SplitConfig.model_validate(fields)


def test_DATA_4_1_valid_proportions_and_keys_are_kept_in_the_config() -> None:
    config = SplitConfig.model_validate({"train": 0.7, "val": 0.15, "test": 0.15, "seed": 3})

    assert config.proportions == Proportions(0.7, 0.15, 0.15)
    dumped = config.model_dump(mode="json")
    assert dumped["stratum_keys"] == ["clip_quartile", "orientation", "source_folder"]
    assert dumped["leakage_distance"] == 12
    assert dumped["continues_version_id"] is None
    with pytest.raises(ValidationError):
        SplitConfig.model_validate({"stratum_keys": ["orientation", "orientation"]})


def test_DATA_4_1_only_accepted_rows_get_a_split_group_and_stratum(tmp_path: Path) -> None:
    rows = [*_plain(30), _row("gone.jpg", status="filtered")]
    rows.append(_row("copy.jpg", status="duplicate", dup_of="img_0000.jpg", dup_group="dg_1"))
    parent = _version(tmp_path, rows)

    result = stagekit.split(tmp_path, parent, FAKES)

    after = stagekit.rows_of(tmp_path, result.record)
    assert len(after) == len(rows), "INV-06: no row is dropped"
    for key, row in after.items():
        if row["status"] == "accepted":
            assert row["split"] in SPLITS
            assert row["split_group"] == row["sha256"], "the fake makes every row its own group"
            assert row["split_stratum"] == "all"
        else:
            assert (row["split"], row["split_group"], row["split_stratum"]) == (None, None, None), (
                key
            )
    assert result.record.stage == "split"


def test_DATA_4_1_the_split_runs_only_on_a_filter_version(tmp_path: Path) -> None:
    parent = _version(tmp_path, _plain(3), stage="label")

    with pytest.raises(StratalError, match="filter"):
        stagekit.split(tmp_path, parent, FAKES)


# ------------------------------------------------------------------ DATA-4.6


def test_DATA_4_6_the_report_adds_up_to_the_accepted_rows_and_matches_the_manifest(
    tmp_path: Path,
) -> None:
    rows = [*_plain(200), _row("gone.jpg", status="filtered")]
    parent = _version(tmp_path, rows)

    result = stagekit.split(tmp_path, parent, FAKES)

    report = result.report
    after = [r for r in stagekit.rows_of(tmp_path, result.record).values() if r["split"]]
    assert report["accepted"] == 200 == len(after)
    for split in SPLITS:
        assert report["splits"][split]["rows"] == sum(1 for r in after if r["split"] == split)
    assert sum(s["rows"] for s in report["splits"].values()) == 200
    assert sum(s["rows"] for s in report["strata"].values()) == 200
    assert report["groups"] == {
        "count": 200,
        "multi_row_groups": 0,
        "rows_in_multi_row_groups": 0,
        "largest": 1,
        "by_size": {"1": 200},
    }
    assert report["carried_over"] + report["newly_assigned"] + report["moved"] == 200
    assert report["leakage_check"]["passed"] is True
    assert result.record.counts["split"] == report, "stored with the version"
    assert result.metrics["accepted"] == 200


def test_DATA_4_6_the_report_says_whether_each_split_is_within_one_group(
    tmp_path: Path,
) -> None:
    parent = _version(tmp_path, _plain(10))
    keys = [f"img_{i:04d}.jpg" for i in range(10)]
    all_train = SplitUnits(
        group=singleton_split_groups,
        strata=one_stratum,
        assign=RecordingAssign({_sha(key): TRAIN for key in keys}),
    )

    report = stagekit.split(tmp_path, parent, all_train).report

    assert report["splits"][TRAIN] == {"rows": 10, "target": 8.0}
    assert report["within_one_group"] is False
    assert report["strata"]["all"]["within_one_group"] is False


# ------------------------------------------------------------------ DATA-4.2


def test_DATA_4_2_the_leakage_check_finds_near_pairs_and_shared_groups_across_splits() -> None:
    rows = [
        ("a.jpg", "0000000000000000", None, TRAIN),
        ("b.jpg", "0000000000000fff", None, TEST),  # 12 bits from a: leaks
        ("c.jpg", "ffff000000000000", None, TRAIN),
        ("d.jpg", "fffff00000001fff", None, VAL),  # 17 bits from c, 25 from a: fine
        ("e.jpg", "0f0f0f0f0f0f0f0f", "dg_x", VAL),
        ("f.jpg", "f0f0f0f0f0f0f0f0", "dg_x", TRAIN),  # far apart but one duplicate group
        ("g.jpg", "0000000000000001", None, TRAIN),  # 1 bit from a, same split: fine
    ]

    check = leakage_check(rows, max_distance=12)

    assert check["violations"] == 3, check["examples"]
    found = {tuple(sorted(e["rows"])) for e in check["examples"]}
    assert found == {("a.jpg", "b.jpg"), ("b.jpg", "g.jpg"), ("e.jpg", "f.jpg")}
    assert check["passed"] is False
    assert check["cross_split_pairs_checked"] == 4 * 2 + 4 * 1 + 2 * 1


def test_DATA_4_2_a_grouping_that_misses_a_near_pair_is_reported_with_a_warning(
    tmp_path: Path,
) -> None:
    rows = [
        _row("near_a.jpg", phash="00000000000000ff"),
        _row("near_b.jpg", phash="00000000000000fe"),
    ]
    rows += _plain(40)
    parent = _version(tmp_path, rows)
    sha_a, sha_b = _sha("near_a.jpg"), _sha("near_b.jpg")
    apart = RecordingAssign({sha_a: TRAIN, sha_b: TEST})
    sink = ListSink()

    result = stagekit.split(
        tmp_path, parent, SplitUnits(singleton_split_groups, one_stratum, apart), sink=sink
    )

    assert result.report["leakage_check"]["violations"] == 1
    warning = [e for e in sink.events if e["level"] == "WARN"]
    assert len(warning) == 1 and warning[0]["reason"] and warning[0]["suggested_action"]


def test_DATA_4_2_rows_the_grouping_joins_share_split_and_split_group(tmp_path: Path) -> None:
    rows = [
        _row("near_a.jpg", phash="00000000000000ff"),
        _row("near_b.jpg", phash="00000000000000fe"),
    ]
    rows += _plain(40)
    parent = _version(tmp_path, rows)
    anchor = min(_sha("near_a.jpg"), _sha("near_b.jpg"))

    def joined(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
        groups = [g for g in singleton_split_groups(items, max_distance=max_distance)]
        groups = [g for g in groups if g.members[0] not in {"near_a.jpg", "near_b.jpg"}]
        groups.append(SplitGroup(anchor, ("near_a.jpg", "near_b.jpg")))
        return sorted(groups, key=lambda g: g.anchor)

    result = stagekit.split(tmp_path, parent, SplitUnits(joined, one_stratum, hashed_splits))

    after = stagekit.rows_of(tmp_path, result.record)
    assert after["near_a.jpg"]["split"] == after["near_b.jpg"]["split"]
    assert after["near_a.jpg"]["split_group"] == after["near_b.jpg"]["split_group"] == anchor
    assert result.report["leakage_check"]["passed"] is True
    assert result.report["groups"]["rows_in_multi_row_groups"] == 2


# ----------------------------------------------- contract checks on the units


def test_DATA_4_2_a_grouping_that_drops_a_row_fails_the_stage_before_writing(
    tmp_path: Path,
) -> None:
    parent = _version(tmp_path, _plain(5))

    def drops(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
        return singleton_split_groups(items[1:], max_distance=max_distance)

    with pytest.raises(ContractBroken, match="left 1 rows"):
        stagekit.split(tmp_path, parent, SplitUnits(drops, one_stratum, hashed_splits))
    assert sorted(p.name for p in (tmp_path / "manifests").iterdir()) == [
        f"{parent.version_id}.parquet"
    ]


def test_DATA_4_2_a_group_whose_anchor_is_not_its_smallest_hash_fails_the_stage(
    tmp_path: Path,
) -> None:
    parent = _version(tmp_path, _plain(3))

    def wrong_anchor(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
        del max_distance
        keys = tuple(sorted(item.row_key for item in items))
        return [SplitGroup(max(item.sha256 for item in items), keys)]

    with pytest.raises(ContractBroken, match="rule 5"):
        stagekit.split(tmp_path, parent, SplitUnits(wrong_anchor, one_stratum, hashed_splits))


def test_DATA_4_5_an_assignment_that_moves_an_undisputed_group_fails_the_stage(
    tmp_path: Path,
) -> None:
    first = stagekit.split(tmp_path, _version(tmp_path, _plain(20)), FAKES)
    moved_key = "img_0003.jpg"
    was = stagekit.rows_of(tmp_path, first.record)[moved_key]["split"]
    other = next(s for s in SPLITS if s != was)
    parent = _version(tmp_path, [*_plain(20), _row("new.jpg")])
    mover = RecordingAssign({_sha(moved_key): other})

    with pytest.raises(ContractBroken, match="rule 2"):
        stagekit.split(
            tmp_path,
            parent,
            SplitUnits(singleton_split_groups, one_stratum, mover),
            previous=first.record,
        )


# ------------------------------------------------------------------ DATA-4.4


def test_DATA_4_4_the_same_parent_config_and_seed_give_the_same_manifest_and_id(
    tmp_path: Path,
) -> None:
    parent = _version(tmp_path, _plain(100))

    first = stagekit.split(tmp_path, parent, FAKES, config=SplitConfig(seed=7))
    again = stagekit.split(tmp_path, parent, FAKES, config=SplitConfig(seed=7))
    other = stagekit.split(tmp_path, parent, FAKES, config=SplitConfig(seed=8))

    assert again.record.version_id == first.record.version_id
    assert again.record.content_hash == first.record.content_hash
    assert other.record.version_id != first.record.version_id
    assert other.record.config["seed"] == 8

    def splits(record: VersionRecord) -> dict[str, str]:
        return {k: v["split"] for k, v in stagekit.rows_of(tmp_path, record).items()}

    assert splits(again.record) == splits(first.record)
    assert splits(other.record) != splits(first.record)


def test_DATA_4_4_a_config_that_names_another_previous_split_is_refused(tmp_path: Path) -> None:
    parent = _version(tmp_path, _plain(5))
    config = SplitConfig().model_copy(update={"continues_version_id": "ds_000000000000"})

    with pytest.raises(StratalError, match="continues"):
        stagekit.split(tmp_path, parent, FAKES, config=config)


# ------------------------------------------------------------------ DATA-4.5


def test_DATA_4_5_rows_are_found_in_the_previous_split_by_content_hash(tmp_path: Path) -> None:
    first = stagekit.split(tmp_path, _version(tmp_path, _plain(20)), FAKES)
    before = stagekit.rows_of(tmp_path, first.record)
    renamed = _row("renamed.jpg", sha=_sha("img_0005.jpg"))
    rows = [r for r in _plain(20) if r["row_key"] != "img_0005.jpg"] + [renamed, _row("new.jpg")]
    recorder = RecordingAssign()

    result = stagekit.split(
        tmp_path,
        _version(tmp_path, rows),
        SplitUnits(singleton_split_groups, one_stratum, recorder),
        previous=first.record,
    )

    assert recorder.previous_of("img_0001.jpg") == before["img_0001.jpg"]["split"]
    assert recorder.previous_of("renamed.jpg") == before["img_0005.jpg"]["split"]
    assert recorder.previous_of("new.jpg") is None
    assert result.record.config["continues_version_id"] == first.record.version_id
    report = result.report
    assert (report["carried_over"], report["newly_assigned"], report["moved"]) == (20, 1, 0)


def test_DATA_4_5_a_new_kept_copy_inherits_the_split_of_the_copy_it_replaced(
    tmp_path: Path,
) -> None:
    first = stagekit.split(tmp_path, _version(tmp_path, _plain(20)), FAKES)
    old_split = stagekit.rows_of(tmp_path, first.record)["img_0007.jpg"]["split"]
    rows = [r for r in _plain(20) if r["row_key"] != "img_0007.jpg"]
    rows += [
        _row("img_0007_better.jpg", dup_group="dg_7"),
        _row(
            "img_0007.jpg",
            status="duplicate",
            dup_of="img_0007_better.jpg",
            dup_group="dg_7",
        ),
    ]
    recorder = RecordingAssign()

    result = stagekit.split(
        tmp_path,
        _version(tmp_path, rows),
        SplitUnits(singleton_split_groups, one_stratum, recorder),
        previous=first.record,
    )

    assert recorder.previous_of("img_0007_better.jpg") == old_split
    after = stagekit.rows_of(tmp_path, result.record)
    assert after["img_0007_better.jpg"]["split"] == old_split
    assert after["img_0007.jpg"]["split"] is None


def test_DATA_4_5_the_report_lists_exactly_the_moved_rows(tmp_path: Path) -> None:
    """AC2 at the shell: whatever the assignment moved is listed, nothing else."""
    first = stagekit.split(tmp_path, _version(tmp_path, _plain(30)), FAKES)
    before = {k: r["split"] for k, r in stagekit.rows_of(tmp_path, first.record).items()}
    train_key = next(k for k, s in sorted(before.items()) if s == TRAIN)
    test_key = next(k for k, s in sorted(before.items()) if s != TRAIN)
    anchor = min(_sha(train_key), _sha(test_key))
    rows = [*_plain(30), _row("bridge.jpg")]

    def bridged(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
        joined = {train_key, test_key, "bridge.jpg"}
        groups = [
            g
            for g in singleton_split_groups(items, max_distance=max_distance)
            if g.members[0] not in joined
        ]
        groups.append(SplitGroup(min(anchor, _sha("bridge.jpg")), tuple(sorted(joined))))
        return sorted(groups, key=lambda g: g.anchor)

    group_anchor = min(anchor, _sha("bridge.jpg"))
    to_train = RecordingAssign({group_anchor: TRAIN})

    result = stagekit.split(
        tmp_path,
        _version(tmp_path, rows),
        SplitUnits(bridged, one_stratum, to_train),
        previous=first.record,
    )

    assert result.report["moved_rows"] == [
        {"row_key": test_key, "from": before[test_key], "to": TRAIN}
    ]
    assert result.report["moved"] == 1
    assert result.report["carried_over"] == 29
    assert result.report["newly_assigned"] == 1


def test_DATA_4_5_without_a_previous_split_every_row_is_new(tmp_path: Path) -> None:
    sink = ListSink()

    result = stagekit.split(tmp_path, _version(tmp_path, _plain(12)), FAKES, sink=sink)

    report: dict[str, Any] = result.report
    assert (report["carried_over"], report["newly_assigned"], report["moved"]) == (0, 12, 0)
    assert report["continues_version_id"] is None
    assert any("assigned afresh" in m for m in sink.messages())
