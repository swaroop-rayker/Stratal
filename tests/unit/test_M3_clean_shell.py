"""The clean stage's shell (agent's plumbing around DATA-2.1 and DATA-2.2).

The owner's functions are replaced by fakes and by scripted groups, so these
tests check only what the shell does with whatever it is given: marking rows
(CL-17), keeping every row (INV-06), enforcing INV-05 and the grouping
contracts, caching pHash values (ADR-0004) and reporting (MON-3.1).
"""

from pathlib import Path

import pytest
from PIL import Image

from stratal.core.errors import StratalError
from stratal.data.clean import OWNER_UNITS, CleanConfig, ContractBroken, DedupeUnits, group_id
from stratal.data.dedupe import DuplicateGroup
from stratal.storage.derivations import MemoryDerivationCache
from stratal.testing import stagekit
from stratal.testing.fakes import (
    ListSink,
    ScriptedGroups,
    fake_perceptual_hash,
    no_exact_duplicates,
    no_near_duplicates,
)
from stratal.testing.synthetic import ImageFolder, make_image_folder

FAKES = DedupeUnits(no_exact_duplicates, fake_perceptual_hash, no_near_duplicates)


def _folder(tmp_path: Path, **planted: int) -> tuple[ImageFolder, Path]:
    return make_image_folder(tmp_path / "src", **planted), tmp_path / "data"


def test_CL_17_exact_groups_are_marked_with_group_keeper_and_reason(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=6, exact_copies=2)
    raw = stagekit.ingest(folder.root, data)
    (c0, o0), (c1, o1) = folder.exact_copies
    groups = [
        DuplicateGroup(keeper=o0, members=tuple(sorted((c0, o0)))),
        DuplicateGroup(keeper=o1, members=tuple(sorted((c1, o1)))),
    ]
    groups.sort(key=lambda g: g.keeper)
    units = DedupeUnits(ScriptedGroups(groups), fake_perceptual_hash, no_near_duplicates)

    rows = stagekit.rows_of(data, stagekit.clean(data, raw, units).record)

    for copy, original in folder.exact_copies:
        gid = group_id("exact", tuple(sorted((copy, original))))
        assert rows[copy]["status"] == "duplicate"
        assert rows[copy]["decided_at_stage"] == "clean"
        assert rows[copy]["reason_code"] == "exact_duplicate"
        assert rows[copy]["dup_of"] == original
        assert rows[original]["status"] == "accepted" and rows[original]["dup_of"] is None
        assert rows[copy]["dup_group_id"] == rows[original]["dup_group_id"] == gid
        assert rows[copy]["dup_kind"] == rows[original]["dup_kind"] == "exact"
        assert rows[copy]["phash"] == rows[original]["phash"], "same content, same hash"


def test_CL_17_near_groups_are_marked_and_exact_copies_are_not_offered_again(
    tmp_path: Path,
) -> None:
    folder, data = _folder(tmp_path, valid=4, exact_copies=1, jpeg_pairs=1)
    raw = stagekit.ingest(folder.root, data)
    (copy, original) = folder.exact_copies[0]
    high, low = folder.jpeg_pairs[0]
    exact = ScriptedGroups([DuplicateGroup(original, tuple(sorted((copy, original))))])
    near = ScriptedGroups([DuplicateGroup(high, tuple(sorted((high, low))))])

    result = stagekit.clean(data, raw, DedupeUnits(exact, fake_perceptual_hash, near))
    rows = stagekit.rows_of(data, result.record)

    assert copy not in near.calls[0], "an exact duplicate is not hashed or grouped again"
    assert original in near.calls[0]
    assert rows[low]["status"] == "duplicate" and rows[low]["reason_code"] == "near_duplicate"
    assert rows[low]["dup_of"] == high and rows[high]["status"] == "accepted"
    assert result.metrics["duplicates_exact"] == 1
    assert result.metrics["duplicates_near"] == 1


def test_INV_06_clean_keeps_every_row_including_rejected_ones(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=5, corrupt=2, not_images=1)
    raw = stagekit.ingest(folder.root, data)

    result = stagekit.clean(data, raw, FAKES)
    before, after = stagekit.rows_of(data, raw), stagekit.rows_of(data, result.record)

    assert after.keys() == before.keys()
    for key, row in before.items():
        if row["status"] != "accepted":
            assert after[key] == row, "rows decided earlier pass through unchanged"
    assert result.record.parent_version_id == raw.version_id
    assert result.record.stage == "clean"


def test_INV_05_clean_refuses_a_grouping_that_leaves_equal_content_accepted(
    tmp_path: Path,
) -> None:
    folder, data = _folder(tmp_path, valid=4, exact_copies=1)
    raw = stagekit.ingest(folder.root, data)

    with pytest.raises(ContractBroken, match="INV-05"):
        stagekit.clean(data, raw, FAKES)

    assert len(list((data / "manifests").glob("*.parquet"))) == 1, "only the raw version exists"


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([DuplicateGroup("a", ("a",))], "fewer than two"),
        ([DuplicateGroup("a", ("b", "a"))], "not sorted"),
        ([DuplicateGroup("z", ("a", "b"))], "is not one of"),
        ([DuplicateGroup("a", ("a", "nope"))], "not among"),
        ([DuplicateGroup("a", ("a", "b")), DuplicateGroup("b", ("b", "c"))], "more than one"),
        ([DuplicateGroup("c", ("c", "d")), DuplicateGroup("a", ("a", "b"))], "sorted by keeper"),
    ],
)
def test_ADR_0012_clean_refuses_near_groups_that_break_the_contract(
    tmp_path: Path, groups: list[DuplicateGroup], message: str
) -> None:
    source = tmp_path / "src"
    folder = make_image_folder(source, valid=4)
    for old, new in zip(folder.valid, ("a", "b", "c", "d"), strict=True):
        (source / old).rename(source / new)
    data = tmp_path / "data"
    raw = stagekit.ingest(source, data)
    units = DedupeUnits(no_exact_duplicates, fake_perceptual_hash, ScriptedGroups(groups))

    with pytest.raises(ContractBroken, match=message):
        stagekit.clean(data, raw, units)


def test_ADR_0012_clean_refuses_an_exact_group_of_different_content(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=3)
    raw = stagekit.ingest(folder.root, data)
    a, b, _ = folder.valid
    units = DedupeUnits(
        ScriptedGroups([DuplicateGroup(a, (a, b))]), fake_perceptual_hash, no_near_duplicates
    )

    with pytest.raises(ContractBroken, match="different content"):
        stagekit.clean(data, raw, units)


def test_ADR_0012_clean_refuses_a_hash_that_is_not_lowercase_hex(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=2)
    raw = stagekit.ingest(folder.root, data)

    def shouting(image: object, *, hash_size: int) -> str:
        del image, hash_size
        return "ABCDEF"

    with pytest.raises(ContractBroken, match="lowercase hex"):
        stagekit.clean(data, raw, DedupeUnits(no_exact_duplicates, shouting, no_near_duplicates))


def test_ADR_0012_an_owner_unit_not_written_yet_surfaces_as_not_implemented(
    tmp_path: Path,
) -> None:
    folder, data = _folder(tmp_path, valid=2)
    raw = stagekit.ingest(folder.root, data)

    with pytest.raises(NotImplementedError, match=r"DATA-2\.1"):
        stagekit.clean(data, raw, OWNER_UNITS)


def test_ADR_0004_phash_values_are_cached_by_content_and_reused(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=10)
    raw = stagekit.ingest(folder.root, data)
    cache = MemoryDerivationCache()
    calls: list[int] = []

    def counting(image: Image.Image, *, hash_size: int) -> str:
        calls.append(1)
        return fake_perceptual_hash(image, hash_size=hash_size)

    units = DedupeUnits(no_exact_duplicates, counting, no_near_duplicates)
    first = stagekit.clean(data, raw, units, cache=cache)
    second = stagekit.clean(data, raw, units, cache=cache)

    assert len(calls) == 10, "each image hashed once across both runs"
    assert (first.metrics["hashed"], first.metrics["cache_hits"]) == (10, 0)
    assert (second.metrics["hashed"], second.metrics["cache_hits"]) == (0, 10)
    assert first.record.version_id == second.record.version_id

    bumped = stagekit.clean(data, raw, units, cache=cache, config=CleanConfig(algorithm_version=2))
    assert bumped.metrics["hashed"] == 10, "a new algorithm version recomputes"
    assert bumped.record.version_id != first.record.version_id


def test_ADR_0004_a_missing_blob_fails_the_stage_instead_of_rejecting_the_row(
    tmp_path: Path,
) -> None:
    folder, data = _folder(tmp_path, valid=3)
    raw = stagekit.ingest(folder.root, data)
    victim = next(iter(stagekit.rows_of(data, raw).values()))["sha256"]
    blob = data / "blobs" / "sha256" / victim[:2] / victim[2:4] / victim
    blob.chmod(0o644)
    blob.unlink()

    with pytest.raises(StratalError, match="missing from the data volume"):
        stagekit.clean(data, raw, FAKES)


def test_MON_3_1_cleaning_emits_its_messages_and_metrics(tmp_path: Path) -> None:
    folder, data = _folder(tmp_path, valid=300)
    raw = stagekit.ingest(folder.root, data)
    sink = ListSink()

    result = stagekit.clean(data, raw, FAKES, sink=sink)

    messages = sink.messages()
    assert "Finding exact duplicates among 300 accepted files" in messages
    hashing = [e for e in sink.events if e["substage"] == "phash"]
    assert [e["message"] for e in hashing] == [
        "Hashing images (pHash) 256/300",
        "Hashing images (pHash) 300/300",
    ]
    assert hashing[-1]["step"] == hashing[-1]["total"] == 300
    assert "Comparing hashes: 0 near-duplicate groups found" in messages
    assert [e["message"].split(" → ")[0] for e in sink.events if e["substage"] == "staging"] == [
        "Writing manifest",
        "Computing version hash",
        "Recording lineage",
    ]
    order = [e["substage"] for e in sink.events]
    assert order.index("compare") < order.index("staging"), "work is reported before the commit"
    for key in ("duplicates_exact", "duplicates_near", "duplicates_semantic", "images_per_s"):
        assert key in result.metrics
