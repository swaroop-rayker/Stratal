"""Stage 5: train/val/test splits (DATA-4.1 to DATA-4.6). The shell around the owner's units.

This module is the agent's plumbing. The decisions (which rows belong
together, each row's stratum, and which split each group gets) are made by
the owner's functions in :mod:`stratal.data.splitting`, passed in as
:class:`SplitUnits`. This module:

1. reads the accepted rows of a ``filter`` version;
2. looks up what the previous split knew about each row (DATA-4.5): by its own
   content hash, or, for the kept row of a duplicate group, by the hashes of
   the copies it replaced;
3. calls the owner's units and checks what they return against their
   contracts, failing with :class:`ContractBroken` before anything is written;
4. writes ``split``, ``split_group`` and ``split_stratum`` on every accepted
   row and clears them on every other row (DATA-4.1);
5. runs its own all-pairs leakage check, independent of the grouping
   (DATA-4.2.AC2), and builds the split report (DATA-4.6), which is stored
   under ``counts["split"]`` of the version.

Which earlier split is continued is decided by the runner, which has the
database, and recorded in the config as ``continues_version_id`` (DATA-4.4).

Tables and texts (ADR-0019 rule 6) take a path of their own through the same
stage: they have no pHash or image size, and an all-pairs check would be slow
on tens of thousands of captions. Their groups are the rows that share a
``text_group`` (the captions of one image); their stratum is the class, or for
a numeric target its quintile; the owner's assignment unit places the groups,
and the leakage check looks for a group or a content hash in two splits.
"""

import bisect
import dataclasses
import math
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratal.core.errors import StratalError
from stratal.data import splitting
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.records import (
    LABELLED_TEXT,
    RECORD_TYPES,
    TABULAR,
    inherited_metadata,
    table_schema_of,
    table_task,
)
from stratal.data.splitting import (
    DEFAULT_STRATUM_KEYS,
    SPLITS,
    GroupToAssign,
    LeakageItem,
    Member,
    Proportions,
    SplitGroup,
    StratumItem,
    StratumKey,
)
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.manifest import (
    STATUS_ACCEPTED,
    STATUS_DUPLICATE,
    Row,
    iter_rows,
    read_metadata,
)

STAGE = "split"
PARENT_STAGE = "filter"
PARENT_STAGES: dict[str, str] = {
    "image_caption": PARENT_STAGE,
    "labelled_images": "preprocess",
    TABULAR: "clean_table",
    LABELLED_TEXT: "clean_text",
}
"""The stage whose version the split takes, per dataset type (DATA-5.2: labelled images
have no label or filter stage)."""
PROPORTION_TOLERANCE = 1e-9
EXAMPLES = 10
"""How many leaking pairs the report names; the count is always complete."""


class SplitConfig(BaseModel):
    """Settings for the split stage. Every field is recorded in the version (DATA-4.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    train: float = Field(default=0.8, description="Share of rows in train (DATA-4.1).")
    val: float = Field(default=0.1, description="Share of rows in val.")
    test: float = Field(default=0.1, description="Share of rows in test.")
    seed: int = Field(
        default=0, description="Decides which new groups go where within a stratum (DATA-4.4)."
    )
    leakage_distance: int = Field(
        default=12,
        ge=0,
        description="Accepted rows whose pHash values are at most this many bits apart join one "
        "split group (DATA-4.2). 12, approved with DATA-4 (DECISIONS.md, M5).",
    )
    stratum_keys: tuple[StratumKey, ...] = Field(
        default=DEFAULT_STRATUM_KEYS,
        description="Row attributes to stratify on, in order (DATA-4.3); empty for none.",
    )
    continues_version_id: str | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
        description="Set by the stage, not by the user: the earlier split whose assignments "
        "this one keeps (DATA-4.5), or null if there is none.",
    )
    algorithm_version: int = Field(
        default=splitting.ALGORITHM_VERSION, json_schema_extra={"readOnly": True}
    )

    @model_validator(mode="after")
    def _proportions(self) -> "SplitConfig":
        shares = {"train": self.train, "val": self.val, "test": self.test}
        bad = sorted(
            name for name, share in shares.items() if not math.isfinite(share) or share < 0
        )
        if bad:
            raise ValueError(f"proportions must be finite and not negative: {', '.join(bad)}")
        total = self.train + self.val + self.test
        if abs(total - 1.0) > PROPORTION_TOLERANCE:
            raise ValueError(f"proportions must sum to 1, not {total:g}")
        if len(set(self.stratum_keys)) != len(self.stratum_keys):
            raise ValueError("a stratum key is given twice")
        return self

    @property
    def proportions(self) -> Proportions:
        return Proportions(self.train, self.val, self.test)


class Grouper(Protocol):
    def __call__(self, items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]: ...


class Stratifier(Protocol):
    def __call__(
        self, items: Sequence[StratumItem], *, keys: Sequence[StratumKey]
    ) -> dict[str, str]: ...


class Assigner(Protocol):
    def __call__(
        self, groups: Sequence[GroupToAssign], *, proportions: Proportions, seed: int
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class SplitUnits:
    group: Grouper
    strata: Stratifier
    assign: Assigner


OWNER_UNITS = SplitUnits(
    group=splitting.group_for_split, strata=splitting.strata_for, assign=splitting.assign_splits
)


@dataclass(frozen=True)
class PreviousSplit:
    """The earlier split this one continues (DATA-4.5)."""

    version_id: str
    manifest: Path


@dataclass(frozen=True)
class SplitResult:
    record: VersionRecord
    report: dict[str, Any]
    metrics: dict[str, MetricValue]
    seconds: float


class ContractBroken(StratalError):
    """An owner unit returned what its contract forbids. The stage fails; nothing is written."""


class _Keyed(Protocol):
    @property
    def row_key(self) -> str: ...


@dataclass(frozen=True)
class _Accepted:
    row_key: str
    sha256: str
    phash: str
    dup_group_id: str | None
    clip_score: float | None
    width: int
    height: int


@dataclass
class _Plan:
    split: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    group: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    stratum: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    previous: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    groups: list[SplitGroup] = field(default_factory=lambda: list[SplitGroup]())


_COLUMNS = [
    "row_key",
    "sha256",
    "status",
    "phash",
    "dup_group_id",
    "dup_of",
    "clip_score",
    "width",
    "height",
    "derived_width",
    "derived_height",
]


def _broken(unit: str, rule: str, detail: str) -> ContractBroken:
    return ContractBroken(
        f"{unit} broke its contract (rule {rule}): {detail}",
        suggested_action=f"Fix {unit} in src/stratal/data/splitting.py so it meets the rules in "
        "its docstring; tests/unit/test_DATA_4_*.py show the expected behaviour.",
    )


def _read(parent_manifest: Path) -> tuple[list[_Accepted], dict[str, list[str]]]:
    """Accepted rows, and for each kept row the content hashes of the copies it replaced."""
    accepted: list[_Accepted] = []
    copies: defaultdict[str, list[str]] = defaultdict(list)
    for row in iter_rows(parent_manifest, columns=_COLUMNS):
        key = str(row["row_key"])
        if row["status"] == STATUS_DUPLICATE and row["dup_of"] and row["sha256"]:
            copies[str(row["dup_of"])].append(str(row["sha256"]))
        if row["status"] != STATUS_ACCEPTED:
            continue
        width = row["derived_width"] or row["width"]
        height = row["derived_height"] or row["height"]
        if not row["sha256"] or not row["phash"] or not width or not height:
            raise StratalError(
                f"Accepted row {key!r} lacks a content hash, a pHash or a size, which the "
                "clean and preprocess stages set.",
                suggested_action="Split a version made by the full pipeline (ingest to filter).",
            )
        accepted.append(
            _Accepted(
                key,
                str(row["sha256"]),
                str(row["phash"]),
                row["dup_group_id"],
                row["clip_score"],
                int(width),
                int(height),
            )
        )
    return accepted, dict(copies)


def _previous_splits(previous: PreviousSplit | None) -> dict[str, str]:
    """``{sha256: split}`` of the accepted rows of the split being continued."""
    if previous is None:
        return {}
    known: dict[str, str] = {}
    for row in iter_rows(previous.manifest, columns=["sha256", "status", "split"]):
        if row["status"] == STATUS_ACCEPTED and row["split"] in SPLITS and row["sha256"]:
            known[str(row["sha256"])] = str(row["split"])
    return known


def _lookup(
    rows: Sequence[_Accepted], copies: dict[str, list[str]], known: dict[str, str]
) -> dict[str, str]:
    """Each accepted row's split in the continued split, if it had one (DATA-4.5).

    A row is found by its own content hash. Failing that, a kept row inherits
    from the copies it replaced; if they were in different splits (possible
    only if the earlier split leaked), the split holding most of them wins, ties
    going to the earlier name in ``SPLITS``.
    """
    found: dict[str, str] = {}
    for row in rows:
        own = known.get(row.sha256)
        if own is not None:
            found[row.row_key] = own
            continue
        inherited = Counter(known[sha] for sha in copies.get(row.row_key, []) if sha in known)
        if inherited:
            best = max(inherited.values())
            found[row.row_key] = next(s for s in SPLITS if inherited[s] == best)
    return found


def _checked_groups(groups: object, rows: Sequence[_Accepted]) -> list[SplitGroup]:
    unit = "group_for_split"
    if not isinstance(groups, list):
        raise _broken(unit, "1", f"returned {type(groups).__name__}, not a list")
    sha_of = {row.row_key: row.sha256 for row in rows}
    seen: set[str] = set()
    checked: list[SplitGroup] = []
    for group in groups:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(group, SplitGroup) or not group.members:
            raise _broken(unit, "1", f"returned {group!r}, not a SplitGroup with members")
        unknown = [key for key in group.members if key not in sha_of]
        if unknown:
            raise _broken(unit, "1", f"named a row it was not given: {unknown[0]!r}")
        again = [key for key in group.members if key in seen]
        if again:
            raise _broken(unit, "1", f"put {again[0]!r} in more than one group")
        seen.update(group.members)
        if list(group.members) != sorted(group.members):
            raise _broken(unit, "5", f"members of {group.anchor} are not sorted")
        if group.anchor != min(sha_of[key] for key in group.members):
            raise _broken(unit, "5", f"{group.anchor} is not the smallest sha256 of its members")
        checked.append(group)
    missing = sorted(set(sha_of) - seen)
    if missing:
        raise _broken(
            unit, "1", f"left {len(missing)} rows out of every group, e.g. {missing[0]!r}"
        )
    return checked


def _checked_strata(strata: object, rows: Sequence[_Accepted]) -> dict[str, str]:
    unit = "strata_for"
    if not isinstance(strata, dict):
        raise _broken(unit, "1", f"returned {type(strata).__name__}, not a dict")
    returned = cast(dict[object, object], strata)
    given: set[object] = {row.row_key for row in rows}
    if set(returned) != given:
        odd = sorted(str(k) for k in set(returned) ^ given)
        raise _broken(unit, "1", f"the row keys differ from those given, e.g. {odd[0]!r}")
    result: dict[str, str] = {}
    for key, value in returned.items():
        if not isinstance(value, str) or not value:
            raise _broken(unit, "1", f"gave {key!r} the stratum {value!r}")
        result[str(key)] = value
    return result


def _checked_assignment(splits: object, groups: Sequence[GroupToAssign]) -> dict[str, str]:
    unit = "assign_splits"
    if not isinstance(splits, dict):
        raise _broken(unit, "1", f"returned {type(splits).__name__}, not a dict")
    returned = cast(dict[object, object], splits)
    anchors: set[object] = {group.anchor for group in groups}
    if set(returned) != anchors:
        odd = sorted(str(k) for k in set(returned) ^ anchors)
        raise _broken(unit, "1", f"the anchors differ from those given, e.g. {odd[0]!r}")
    result: dict[str, str] = {}
    for anchor, split in returned.items():
        if split not in SPLITS:
            raise _broken(unit, "1", f"gave {anchor} the split {split!r}")
        result[str(anchor)] = str(split)
    for group in groups:
        known = {member.previous for member in group.members if member.previous is not None}
        if len(known) == 1 and result[group.anchor] not in known:
            raise _broken(
                unit,
                "2",
                f"moved group {group.anchor} from {known.pop()} to {result[group.anchor]}, "
                "though no member was in another split",
            )
    return result


def _plan(
    rows: Sequence[_Accepted], previous: dict[str, str], config: SplitConfig, units: SplitUnits
) -> _Plan:
    leakage = [LeakageItem(r.row_key, r.sha256, r.phash, r.dup_group_id) for r in rows]
    groups = _checked_groups(units.group(leakage, max_distance=config.leakage_distance), rows)
    strata_items = [StratumItem(r.row_key, r.clip_score, r.width, r.height) for r in rows]
    strata = _checked_strata(units.strata(strata_items, keys=config.stratum_keys), rows)
    key_of_sha = {row.sha256: row.row_key for row in rows}
    to_assign = [
        GroupToAssign(
            group.anchor,
            strata[key_of_sha[group.anchor]],
            tuple(Member(key, previous.get(key)) for key in group.members),
        )
        for group in groups
    ]
    splits = _checked_assignment(
        units.assign(to_assign, proportions=config.proportions, seed=config.seed), to_assign
    )
    plan = _Plan(stratum=strata, previous=previous, groups=groups)
    for group in groups:
        for key in group.members:
            plan.split[key] = splits[group.anchor]
            plan.group[key] = group.anchor
    return plan


def _apply(rows: Iterator[Row], plan: _Plan) -> Iterator[Row]:
    for row in rows:
        key = str(row["row_key"])
        accepted = row["status"] == STATUS_ACCEPTED
        row.update(
            split=plan.split[key] if accepted else None,
            split_group=plan.group[key] if accepted else None,
            split_stratum=plan.stratum[key] if accepted else None,
        )
        yield row


def leakage_check(
    rows: Sequence[tuple[str, str, str | None, str]], *, max_distance: int
) -> dict[str, Any]:
    """Every pair of accepted rows in different splits that should not be (DATA-4.2.AC2).

    ``rows`` are ``(row_key, phash, dup_group_id, split)``. Independent of the
    owner's grouping on purpose: it compares every cross-split pair directly
    rather than trusting the groups. A pair leaks when its pHash values are at
    most ``max_distance`` bits apart or it shares a duplicate group.
    """
    by_split: dict[str, list[tuple[str, int, str | None]]] = {split: [] for split in SPLITS}
    for key, phash, dup_group, split in sorted(rows):
        by_split[split].append((key, int(phash, 16), dup_group))
    pairs = 0
    violations = 0
    examples: list[dict[str, Any]] = []
    for i, first in enumerate(SPLITS):
        for second in SPLITS[i + 1 :]:
            right = by_split[second]
            pairs += len(by_split[first]) * len(right)
            for key_a, hash_a, group_a in by_split[first]:
                for key_b, hash_b, group_b in right:
                    distance = (hash_a ^ hash_b).bit_count()
                    shared = group_a is not None and group_a == group_b
                    if distance <= max_distance or shared:
                        violations += 1
                        if len(examples) < EXAMPLES:
                            examples.append(
                                {
                                    "rows": [key_a, key_b],
                                    "splits": [first, second],
                                    "distance": distance,
                                    "same_duplicate_group": shared,
                                }
                            )
    return {
        "distance": max_distance,
        "cross_split_pairs_checked": pairs,
        "violations": violations,
        "examples": examples,
        "passed": violations == 0,
    }


def _report(
    rows: Sequence[_Keyed], plan: _Plan, config: SplitConfig, leakage: dict[str, Any]
) -> dict[str, Any]:
    """The split report (DATA-4.6), from the rows as written."""
    proportions = config.proportions
    accepted = len(rows)
    sizes = Counter(len(group.members) for group in plan.groups)
    largest = max(sizes, default=0)
    group_size = {group.anchor: len(group.members) for group in plan.groups}

    totals = Counter(plan.split.values())
    splits = {
        split: {"rows": totals[split], "target": round(proportions.of(split) * accepted, 3)}
        for split in SPLITS
    }
    within_total = all(
        abs(totals[split] - proportions.of(split) * accepted) < max(largest, 1) for split in SPLITS
    )

    per_stratum: defaultdict[str, Counter[str]] = defaultdict(Counter)
    largest_in: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        stratum = plan.stratum[row.row_key]
        per_stratum[stratum][plan.split[row.row_key]] += 1
        size = group_size[plan.group[row.row_key]]
        largest_in[stratum] = max(largest_in[stratum], size)
    strata: dict[str, Any] = {}
    for stratum in sorted(per_stratum):
        counts = per_stratum[stratum]
        total = sum(counts.values())
        strata[stratum] = {
            "rows": total,
            "largest_group": largest_in[stratum],
            "splits": {
                split: {"rows": counts[split], "target": round(proportions.of(split) * total, 3)}
                for split in SPLITS
            },
            "within_one_group": all(
                abs(counts[split] - proportions.of(split) * total) < largest_in[stratum]
                for split in SPLITS
            ),
        }

    moved = [
        {"row_key": key, "from": before, "to": plan.split[key]}
        for key, before in sorted(plan.previous.items())
        if before != plan.split[key]
    ]
    carried = sum(1 for key, before in plan.previous.items() if before == plan.split[key])
    return {
        "accepted": accepted,
        "proportions": {split: proportions.of(split) for split in SPLITS},
        "seed": config.seed,
        "continues_version_id": config.continues_version_id,
        "splits": splits,
        "within_one_group": within_total,
        "strata": strata,
        "strata_within_one_group": all(s["within_one_group"] for s in strata.values()),
        "groups": {
            "count": len(plan.groups),
            "multi_row_groups": sum(n for size, n in sizes.items() if size > 1),
            "rows_in_multi_row_groups": sum(size * n for size, n in sizes.items() if size > 1),
            "largest": largest,
            "by_size": {str(size): sizes[size] for size in sorted(sizes)},
        },
        "carried_over": carried,
        "newly_assigned": accepted - len(plan.previous),
        "moved": len(moved),
        "moved_rows": moved,
        "leakage_check": leakage,
    }


def run_split(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: SplitConfig,
    derive: VersionDeriver,
    sink: EventSink,
    previous: PreviousSplit | None,
    units: SplitUnits = OWNER_UNITS,
    checkpoint: Callable[[], None] = lambda: None,
    dtype: str | None = None,
) -> SplitResult:
    """Split the accepted rows of a ``filter`` version (``preprocess`` for labelled images)
    and commit a ``split`` version.

    ``dtype`` is the parent's dataset type; the split keeps the rows and so the type.
    """
    started = time.monotonic()
    made_by = read_metadata(parent_manifest).get("stratal.stage")
    wanted = PARENT_STAGES.get(dtype or "image_caption", PARENT_STAGE)
    if made_by != wanted:
        raise StratalError(
            f"The split runs on a version made by {wanted!r}; {parent_version_id} was made "
            f"by {made_by!r}.",
            suggested_action=f"Run the pipeline through the {wanted} stage first (DATA-4.1).",
        )
    expected = previous.version_id if previous is not None else None
    if config.continues_version_id != expected:
        raise StratalError(
            f"The config says the split continues {config.continues_version_id!r}, "
            f"but it was given {expected!r}.",
            suggested_action="Leave continues_version_id unset; the stage fills it in.",
        )

    if dtype in RECORD_TYPES:
        return _run_records(
            parent_manifest=parent_manifest,
            parent_version_id=parent_version_id,
            data_dir=data_dir,
            config=config,
            derive=derive,
            sink=sink,
            previous=previous,
            units=units,
            checkpoint=checkpoint,
            dtype=str(dtype),
            started=started,
        )
    rows, copies = _read(parent_manifest)
    sink.emit(
        stage=STAGE,
        substage="group",
        message=f"Grouping {len(rows):,} accepted rows · leakage distance "
        f"{config.leakage_distance} bits",
        metrics={"accepted": len(rows), "leakage_distance": config.leakage_distance},
    )
    previous_lookup = _lookup(rows, copies, _previous_splits(previous))
    if previous is not None:
        sink.emit(
            stage=STAGE,
            substage="carry_over",
            message=f"Continuing {previous.version_id}: {len(previous_lookup):,} of "
            f"{len(rows):,} rows already have a split",
            metrics={"known": len(previous_lookup), "continues": previous.version_id},
        )
    else:
        sink.emit(
            stage=STAGE,
            substage="carry_over",
            message="No earlier split of this dataset with these proportions and seed: "
            "every group is assigned afresh",
            metrics={"known": 0},
        )
    checkpoint()
    plan = _plan(rows, previous_lookup, config, units)
    sink.emit(
        stage=STAGE,
        substage="assign",
        message=f"Assigned {len(plan.groups):,} groups · train {config.train} / val "
        f"{config.val} / test {config.test} · seed {config.seed}",
        metrics={"groups": len(plan.groups), "seed": config.seed},
    )
    checkpoint()
    leakage = leakage_check(
        [(r.row_key, r.phash, r.dup_group_id, plan.split[r.row_key]) for r in rows],
        max_distance=config.leakage_distance,
    )
    if leakage["passed"]:
        sink.emit(
            stage=STAGE,
            substage="leakage_check",
            message=f"Leakage check passed: no two rows within {config.leakage_distance} bits "
            f"in different splits ({leakage['cross_split_pairs_checked']:,} pairs)",
            metrics={"leakage_violations": 0},
        )
    else:
        first = leakage["examples"][0]["rows"]
        sink.emit(
            stage=STAGE,
            substage="leakage_check",
            level="WARN",
            message=f"Leakage check found {leakage['violations']:,} pairs in different splits, "
            f"e.g. {first[0]} and {first[1]}",
            reason=f"Rows within {config.leakage_distance} bits, or in one duplicate group, "
            "were not grouped together (DATA-4.2).",
            suggested_action="Check group_for_split in src/stratal/data/splitting.py; the split "
            "report lists examples under leakage_check.",
            metrics={"leakage_violations": int(leakage["violations"])},
        )
    report = _report(rows, plan, config, leakage)
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), plan),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    record = dataclasses.replace(record, dtype=dtype, counts={**record.counts, "split": report})
    metrics: dict[str, MetricValue] = {
        "accepted": len(rows),
        **{split: report["splits"][split]["rows"] for split in SPLITS},
        "groups": len(plan.groups),
        "largest_group": report["groups"]["largest"],
        "carried_over": report["carried_over"],
        "newly_assigned": report["newly_assigned"],
        "moved": report["moved"],
        "leakage_violations": int(leakage["violations"]),
    }
    return SplitResult(record, report, metrics, round(time.monotonic() - started, 3))


# ---------------------------------------------------------------- tables and texts (ADR-0019)


@dataclass(frozen=True)
class _Record:
    row_key: str
    sha256: str
    group: str | None
    target: str | float | None


def _read_records(parent_manifest: Path) -> list[_Record]:
    columns = ["row_key", "sha256", "status", "label", "target_value", "text_group"]
    found: list[_Record] = []
    for row in iter_rows(parent_manifest, columns=columns):
        if row["status"] != STATUS_ACCEPTED:
            continue
        key = str(row["row_key"])
        if not row["sha256"]:
            raise StratalError(
                f"Accepted row {key!r} has no content hash, which ingest sets.",
                suggested_action="Split a version made by the full pipeline.",
            )
        target = row["label"] if row["label"] is not None else row["target_value"]
        found.append(_Record(key, str(row["sha256"]), row["text_group"], target))
    return found


def record_strata(records: Sequence[_Record], *, numeric: bool) -> dict[str, str]:
    """Each row's stratum: its class, or its target's quintile ``q1``..``q5`` (ADR-0019 rule 6)."""
    if not numeric:
        return {r.row_key: str(r.target) for r in records}
    values = [float(r.target) for r in records if isinstance(r.target, int | float)]
    cuts = statistics.quantiles(values, n=5, method="inclusive") if len(values) >= 2 else []
    return {r.row_key: f"q{bisect.bisect_right(cuts, float(r.target or 0.0)) + 1}" for r in records}


def record_groups(records: Sequence[_Record]) -> list[SplitGroup]:
    """Rows sharing a ``text_group`` form one group; every other row is its own.

    The anchor is the smallest content hash among the members, as for images
    (DATA_MODEL §5, ``split_group``).
    """
    by_group: defaultdict[str, list[_Record]] = defaultdict(list)
    for record in records:
        by_group[record.group if record.group is not None else f"row:{record.row_key}"].append(
            record
        )
    groups = [
        SplitGroup(
            anchor=min(m.sha256 for m in members),
            members=tuple(sorted(m.row_key for m in members)),
        )
        for members in by_group.values()
    ]
    return sorted(groups, key=lambda g: g.anchor)


def record_leakage_check(records: Sequence[_Record], split: dict[str, str]) -> dict[str, Any]:
    """A group, or one content, in two splits (DATA-4.2.AC2 for tables and texts)."""
    seen: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    rows_of: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        for key in (("group", record.group), ("content", record.sha256)):
            if key[1] is None:
                continue
            ident = (key[0], str(key[1]))
            seen[ident].add(split[record.row_key])
            rows_of[ident].append(record.row_key)
    leaking = sorted(ident for ident, splits in seen.items() if len(splits) > 1)
    return {
        "distance": 0,
        "method": "a text group or a content hash in more than one split",
        "cross_split_pairs_checked": len(records),
        "violations": len(leaking),
        "examples": [
            {
                "rows": sorted(rows_of[ident])[:2],
                "splits": sorted(seen[ident]),
                "distance": 0,
                "same_duplicate_group": ident[0] == "group",
            }
            for ident in leaking[:EXAMPLES]
        ],
        "passed": not leaking,
    }


def _run_records(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: SplitConfig,
    derive: VersionDeriver,
    sink: EventSink,
    previous: PreviousSplit | None,
    units: SplitUnits,
    checkpoint: Callable[[], None],
    dtype: str,
    started: float,
) -> SplitResult:
    if config.stratum_keys or config.leakage_distance != 0:
        raise StratalError(
            "For tables and texts the stratum is the class or the target's quintile, and rows "
            "are grouped by text group, not by pHash: stratum_keys must be empty and "
            "leakage_distance 0.",
            suggested_action="Leave stratum_keys and leakage_distance at the type's defaults.",
        )
    schema = table_schema_of(parent_manifest)
    numeric = schema is not None and table_task(schema) == "regression"
    rows = _read_records(parent_manifest)
    groups = record_groups(rows)
    strata = record_strata(rows, numeric=numeric)
    known = _previous_splits(previous)
    previous_lookup = {r.row_key: known[r.sha256] for r in rows if r.sha256 in known}
    sink.emit(
        stage=STAGE,
        substage="group",
        message=f"Grouping {len(rows):,} accepted records into {len(groups):,} groups · "
        f"stratified by {'target quintile' if numeric else 'class'}",
        metrics={"accepted": len(rows), "groups": len(groups)},
    )
    checkpoint()
    key_of_sha = {r.sha256: r.row_key for r in rows}
    to_assign = [
        GroupToAssign(
            group.anchor,
            strata[key_of_sha[group.anchor]],
            tuple(Member(key, previous_lookup.get(key)) for key in group.members),
        )
        for group in groups
    ]
    splits = _checked_assignment(
        units.assign(to_assign, proportions=config.proportions, seed=config.seed), to_assign
    )
    plan = _Plan(stratum=strata, previous=previous_lookup, groups=groups)
    for group in groups:
        for key in group.members:
            plan.split[key] = splits[group.anchor]
            plan.group[key] = group.anchor
    leakage = record_leakage_check(rows, plan.split)
    if not leakage["passed"]:
        raise StratalError(
            f"{leakage['violations']:,} groups or contents ended up in two splits.",
            suggested_action="Check assign_splits: every member of a group gets its split.",
        )
    sink.emit(
        stage=STAGE,
        substage="leakage_check",
        message="Leakage check passed: no text group or content in two splits",
        metrics={"leakage_violations": 0},
    )
    report = _report(rows, plan, config, leakage)
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), plan),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
        metadata=inherited_metadata(parent_manifest),
    )
    record = dataclasses.replace(record, dtype=dtype, counts={**record.counts, "split": report})
    metrics: dict[str, MetricValue] = {
        "accepted": len(rows),
        **{split: report["splits"][split]["rows"] for split in SPLITS},
        "groups": len(groups),
        "largest_group": report["groups"]["largest"],
        "carried_over": report["carried_over"],
        "newly_assigned": report["newly_assigned"],
        "moved": report["moved"],
        "leakage_violations": 0,
    }
    return SplitResult(record, report, metrics, round(time.monotonic() - started, 3))
