"""Train/val/test splits (DATA-4.1 to DATA-4.5). Owner units (ADR-0012).

Three functions, called by the split stage (:mod:`stratal.data.split_stage`,
the agent's shell) in this order on every row of a ``filter`` version that is
still ``accepted``:

1. :func:`group_for_split` joins rows that show the same picture or the same
   moment into split groups (DATA-4.2, leakage prevention).
2. :func:`strata_for` gives each row its stratum (DATA-4.3).
3. :func:`assign_splits` gives each group one split (DATA-4.1, DATA-4.4,
   DATA-4.5). The shell has already looked up, for every member, the split its
   content had in the dataset's previous split with the same proportions and
   seed; the function keeps those and fills each stratum's shortfall with the
   new groups.

The shell then writes ``split``, ``split_group`` and ``split_stratum`` on every
accepted row, lists the rows whose split changed, and checks the result
against these contracts and with its own all-pairs leakage check before
anything is committed. Nothing here touches manifests, the database or events.

The requirements and their reasoning: SPEC.md §DATA-4 and
``docs/drafts/DATA-4_v1.md``. Tests: ``tests/unit/test_DATA_4_*.py``.

Written by the agent at the owner's request on 2026-11-16 ("write DATA-4 units
for me"). Each function's docstring states the contract first, then how it is
met and why; ``docs/learning/sys-design.md`` explains the algorithms at length.
"""

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

ALGORITHM_VERSION = 2
"""Bump this whenever you change what any function in this file returns.

It is part of the split stage's config, so a new value gives a new version ID
(ARCHITECTURE.md §3, stage memoization). It does not reset carry-over: rows of
an earlier split keep their split under a new algorithm as long as the
proportions and the seed are the same (DATA-4.5).

History: 1 covered the stubs and the fakes (the M5 dry run registered a coco1k
split made with fakes under it, ``ds_28027327ad3f``); 2 is the first real
algorithm, with the overshoot spreading and the repair pass (2026-11-16).
"""

TRAIN = "train"
VAL = "val"
TEST = "test"
SPLITS: tuple[str, ...] = (TRAIN, VAL, TEST)
"""Every split name, in the order used to break ties."""


@dataclass(frozen=True)
class Proportions:
    """Target share of rows per split. The split stage's config refuses bad values
    with ``validation_error`` (DATA-4.1.AC2); the functions here check again."""

    train: float = 0.8
    val: float = 0.1
    test: float = 0.1

    def of(self, split: str) -> float:
        """The share of ``split``, one of :data:`SPLITS`."""
        return {TRAIN: self.train, VAL: self.val, TEST: self.test}[split]


# ---------------------------------------------------------------- DATA-4.2


@dataclass(frozen=True)
class LeakageItem:
    """One accepted row, as the leakage grouping sees it."""

    row_key: str
    sha256: str
    """SHA-256 of the original file (64 lowercase hex). Unique among accepted rows (INV-05)."""
    phash: str
    """The clean stage's perceptual hash: lowercase hex, the same length for every row."""
    dup_group_id: str | None
    """The duplicate group this row keeps (CL-17), or ``None``. See DATA_MODEL.md §5."""
    semantic_cluster_id: str | None = None
    """The semantic cluster this row is in (DATA-2.3, ADR-0013), or ``None``."""


@dataclass(frozen=True)
class SplitGroup:
    """Rows that must land in the same split."""

    anchor: str
    """The smallest ``sha256`` among the members. Written to ``split_group``."""
    members: tuple[str, ...]
    """Every member's ``row_key``, sorted ascending."""


def group_for_split(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
    """Join rows that show the same picture or moment into split groups (DATA-4.2).

    ``items`` holds every accepted row of the version being split, in no
    particular order. ``max_distance`` is the leakage distance from the split
    stage's config (12 bits by default; DECISIONS.md, M5).

    Must satisfy:

    1. Every item is in exactly one group. A row with no near neighbour is a
       group of one (unlike the dedupe functions, which return only groups of
       two or more).
    2. Two items with the same non-null ``dup_group_id`` are in the same group.
    2b. Two items with the same non-null ``semantic_cluster_id`` are in the same
       group (DATA-4.8, ADR-0013 rule 4; added in M11 by the agent at the owner's
       request).
    3. Two items whose perceptual hashes are at most ``max_distance`` bits
       apart (Hamming distance of the hex strings read as bits) are in the
       same group.
    4. Grouping is transitive: if A joins B and B joins C, all three are one
       group. Two items are never in one group unless a chain of rule-2,
       rule-2b and rule-3 links joins them.
    5. ``anchor`` is the smallest ``sha256`` among a group's members;
       ``members`` is sorted ascending; the list is sorted by ``anchor``.
    6. The same items in any order give the same result (NFR-4).
    7. ``ValueError`` when ``max_distance`` is negative, when the hashes do
       not all have the same length, or when a ``row_key`` or a ``sha256``
       occurs twice.
    8. It must finish for 20,000 items in under a minute on the CPU worker
       (the phase-1 dataset is 10,000).

    Why rule 2 matters even though the clean stage leaves only one accepted row
    per duplicate group: a group's kept row can be a different file from one
    version to the next. The shell uses the duplicate group to carry the split
    over to the new keeper (DATA-4.5, second bullet).

    How: a union-find (disjoint sets) over the items' positions. Rule 2 joins
    every item to the first one seen with its duplicate-group ID. Rule 3
    compares every pair: XOR of the two hashes read as integers, then a
    population count (``int.bit_count``). The groups are the connected
    components, so rule 4 holds by construction.

    Why every pair rather than an index: at a 12-bit distance the pigeonhole
    index of the dedupe step (``max_distance + 1`` exact-match chunks of 64
    bits) has chunks of about 5 bits, so its buckets hold hundreds of items and
    it saves little. The direct comparison in a list comprehension measured
    4.5 s for 10,000 rows and 18 s for 20,000 in the CPU worker image. It is
    simple and obviously complete.
    """
    if max_distance < 0:
        raise ValueError(f"max_distance must be at least 0, got {max_distance}")
    _no_repeats("row_key", [item.row_key for item in items])
    _no_repeats("sha256", [item.sha256 for item in items])
    widths = {len(item.phash) for item in items}
    if len(widths) > 1:
        raise ValueError(f"hashes of different lengths cannot be compared: {sorted(widths)}")

    ordered = sorted(items, key=lambda item: item.sha256)
    sets = _DisjointSets(len(ordered))
    first_of_group: dict[str, int] = {}
    for index, item in enumerate(ordered):
        if item.dup_group_id is not None:
            sets.union(first_of_group.setdefault(item.dup_group_id, index), index)
    hashes = [int(item.phash, 16) for item in ordered]
    for i, value in enumerate(hashes):
        near = [
            j
            for j, other in enumerate(hashes[i + 1 :], i + 1)
            if (value ^ other).bit_count() <= max_distance
        ]
        for j in near:
            sets.union(i, j)

    members: defaultdict[int, list[int]] = defaultdict(list)
    for index in range(len(ordered)):
        members[sets.find(index)].append(index)
    groups = [
        SplitGroup(
            anchor=ordered[min(indices)].sha256,  # ordered by sha256: the first is the smallest
            members=tuple(sorted(ordered[i].row_key for i in indices)),
        )
        for indices in members.values()
    ]
    return sorted(groups, key=lambda group: group.anchor)


def _no_repeats(what: str, values: Sequence[str]) -> None:
    repeated = [value for value, count in Counter(values).items() if count > 1]
    if repeated:
        raise ValueError(f"{what} {sorted(repeated)[0]!r} occurs more than once")


class _DisjointSets:
    """Union-find with path halving and union by size."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, index: int) -> int:
        parent = self._parent
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]


# ---------------------------------------------------------------- DATA-4.3


class StratumKey(StrEnum):
    """Row attributes a split can be stratified on (DATA-4.3)."""

    CLIP_QUARTILE = "clip_quartile"
    """``q1`` (lowest scores) to ``q4``."""
    ORIENTATION = "orientation"
    """``landscape``, ``portrait`` or ``square``."""
    SOURCE_FOLDER = "source_folder"
    """The first component of ``row_key``, or ``.`` for a file at the source's root."""


DEFAULT_STRATUM_KEYS: tuple[StratumKey, ...] = (
    StratumKey.CLIP_QUARTILE,
    StratumKey.ORIENTATION,
    StratumKey.SOURCE_FOLDER,
)
NO_STRATUM = "all"
"""The stratum of every row when the split is not stratified (no keys)."""


@dataclass(frozen=True)
class StratumItem:
    """One accepted row, as the stratification sees it."""

    row_key: str
    clip_score: float | None
    width: int
    """Of the upright image: the shell passes ``derived_width`` (after EXIF rotation)."""
    height: int
    """Of the upright image: ``derived_height``."""


def strata_for(items: Sequence[StratumItem], *, keys: Sequence[StratumKey]) -> dict[str, str]:
    """Each row's stratum: ``{row_key: stratum}`` (DATA-4.3).

    ``items`` holds every accepted row of the version being split. The stratum
    is each key's value for the row, in the order of ``keys``, joined by
    ``|``: with the default keys, e.g. ``q3|landscape|.``.

    Must satisfy:

    1. The result has exactly the given row keys.
    2. ``clip_quartile``: the boundaries are the 25th, 50th and 75th
       percentiles of the given items' scores, by linear interpolation between
       closest ranks (what ``statistics.quantiles(scores, n=4,
       method="inclusive")`` computes). A score equal to a boundary belongs to
       the lower quartile. A missing or non-finite score is a ``ValueError``
       naming the row, but only when this key is used.
    3. ``orientation``: ``landscape`` when width > height, ``portrait`` when
       height > width, ``square`` when they are equal.
    4. ``source_folder``: the part of ``row_key`` before its first ``/``, or
       ``.`` when it has none. A ``|`` in a folder name is replaced by ``_``
       so the stratum can always be split back into its keys.
    5. No keys: every row's stratum is :data:`NO_STRATUM`. A key given twice is
       a ``ValueError``.
    6. The same items in any order give the same result.

    How: the three boundaries are computed once from all scores (they depend
    only on the multiset of scores, so not on order). A row's quartile is 1
    plus the number of boundaries its score is strictly above, which puts a
    score equal to a boundary in the lower quartile. With a single row,
    ``statistics.quantiles`` cannot interpolate, so all three boundaries are
    that row's score and it is ``q1``.
    """
    if len(set(keys)) != len(keys):
        raise ValueError(f"a stratum key is given twice: {[str(k) for k in keys]}")
    if not keys:
        return {item.row_key: NO_STRATUM for item in items}

    boundaries: list[float] = []
    if StratumKey.CLIP_QUARTILE in keys:
        bad = [i.row_key for i in items if i.clip_score is None or not math.isfinite(i.clip_score)]
        if bad:
            raise ValueError(f"row {sorted(bad)[0]!r} has no finite CLIP score to stratify on")
        scores = [float(item.clip_score) for item in items if item.clip_score is not None]
        if len(scores) >= 2:
            boundaries = statistics.quantiles(scores, n=4, method="inclusive")
        elif scores:
            boundaries = [scores[0]] * 3

    def value(item: StratumItem, key: StratumKey) -> str:
        if key is StratumKey.CLIP_QUARTILE:
            score = float(item.clip_score or 0.0)
            return f"q{1 + sum(1 for boundary in boundaries if score > boundary)}"
        if key is StratumKey.ORIENTATION:
            if item.width > item.height:
                return "landscape"
            return "portrait" if item.height > item.width else "square"
        folder = item.row_key.split("/", 1)[0] if "/" in item.row_key else "."
        return folder.replace("|", "_")

    return {item.row_key: "|".join(value(item, key) for key in keys) for item in items}


# ------------------------------------------------- DATA-4.1, DATA-4.4, DATA-4.5


@dataclass(frozen=True)
class Member:
    """One row of a split group, with what the previous split knew about it."""

    row_key: str
    previous: str | None
    """The split this row's content had in the dataset's previous split made with
    the same proportions and seed (one of :data:`SPLITS`), or ``None`` if it had
    none there. The shell matches by content hash, and for the kept row of a
    duplicate group also by the hashes of the copies it replaced (DATA-4.5)."""


@dataclass(frozen=True)
class GroupToAssign:
    """One split group, ready to be given a split."""

    anchor: str
    stratum: str
    """The stratum of the group's anchor row; the group counts there as a whole."""
    members: tuple[Member, ...]


def assign_splits(
    groups: Sequence[GroupToAssign], *, proportions: Proportions, seed: int
) -> dict[str, str]:
    """One split per group: ``{anchor: split}`` (DATA-4.1, DATA-4.4, DATA-4.5).

    ``groups`` holds every split group of the version, in no particular order.
    Below, a stratum's *target* for a split is its proportion times the rows in
    that stratum, and *one group* means the size of the largest group in the
    stratum (or, for the totals, in the whole version).

    Must satisfy:

    1. Every anchor gets exactly one of :data:`SPLITS`, and only given anchors
       appear.
    2. **Carried over:** a group whose members' known ``previous`` splits are
       all the same gets that split. Members with ``previous=None`` are new
       rows that joined it.
    3. **Merged:** a group whose members have different ``previous`` splits
       goes to the split that holds the most of its members. On a tie, the
       split with the larger proportion wins, then the earlier in
       :data:`SPLITS`. (The shell reports every member whose split changed.)
    4. **New:** groups with no known member fill each stratum towards its
       targets. When no group is carried over (a first split), every
       stratum's row count per split differs from its target by less than one
       group (DATA-4.3.AC1), and so does every split's total (DATA-4.1.AC1).
    5. With carried groups, rule 4's bounds hold whenever placing the new
       groups alone can reach them (DATA-4.5.AC1: split 9,500 rows, add 500
       from the same mix, split again).
    6. **Reproducible:** the same groups in any order, proportions and seed
       give the same result (DATA-4.4.AC1). A different seed changes which new
       groups go where.
    7. **Random within a stratum:** besides its stratum, nothing about a row
       decides its split. Sorting by ``row_key`` and cutting at 80 % and 90 %
       is wrong: file names often encode time or source.
    8. ``ValueError`` when a proportion is negative, the proportions do not sum
       to 1 (within 1e-9), an anchor occurs twice, a group has no members, or a
       ``previous`` value is not one of :data:`SPLITS`.

    How, in four steps. A split's *room* in a stratum is its target there
    minus the rows it already has; its *global room* is the sum over strata.

    1. **Known groups** (rules 2 and 3) are settled first and counted as
       carried rows of their stratum.
    2. **Multi-row groups**, over all strata (in name order), largest first
       within a stratum. A group goes to a split whose room fits it, chosen at
       random with weights equal to that room. A group that fits no room
       (a stratum of 5 rows that is one group of 5, say) has to overshoot one
       split. It goes to the split with the most *global* room, among those
       where the overshoot stays under one group. Spreading the overshoots by
       global room is what keeps the totals right when many small strata
       overshoot; choosing by room within the stratum alone sent all ten such
       strata to train in the overshoot test.
    3. **Single rows.** Each stratum's remaining room is projected onto
       "non-negative and summing to its single rows" (a Euclidean projection
       onto a simplex), which handles splits already overfilled by carried
       rows or overshoots. The resulting matrix of real numbers (strata by
       splits) is rounded so that every cell is its floor or its ceiling,
       every stratum's row sums to its single rows, and every split's column
       sums to its own total rounded. This is *controlled rounding*: a matrix
       with integer row sums always has such a rounding (Bacharach, 1966). It
       is found as a small bipartite matching between strata that need one
       more row and splits that need one more row. Rounding each stratum on
       its own would let errors of up to one row per stratum add up in the
       totals (the 48-strata test). The single rows of each stratum are then
       shuffled and dealt out to those quotas.
    4. **Repair.** Strata made mostly of groups have few single rows to even
       out their overshoots, so the totals can still be a group or more off.
       Then new groups are moved, one at a time, from a split over its total
       to one under it, where the stratum stays within its bound
       (:func:`_repair_totals`).

    Steps 2 and 3 alone kept the totals within bounds in every one of 1,000
    random mixes with realistic grouping (about 2 % of rows in groups), but
    in only 634 of 1,000 where 30 % of strata were mostly groups. With step 4
    all of 3,000 random mixes passed, those where every stratum is mostly
    groups included (a seeded sample of these is
    ``test_DATA_4_1_random_mixes_stay_within_one_group``). Step 4 is a greedy
    local search, so this is measured, not proven: it stops when no single
    move helps.

    All randomness comes from ``random.Random(f"{seed}|{stratum}")``, so it is
    reproducible and independent of input order (groups are sorted by anchor
    first). A string seed is hashed with SHA-512 by ``random``, so it does not
    depend on ``PYTHONHASHSEED``.
    """
    _check_proportions(proportions)
    _no_repeats("anchor", [group.anchor for group in groups])
    for group in groups:
        if not group.members:
            raise ValueError(f"group {group.anchor} has no members")
        odd = [m.previous for m in group.members if m.previous not in (None, *SPLITS)]
        if odd:
            raise ValueError(f"group {group.anchor} has a member in unknown split {odd[0]!r}")

    result: dict[str, str] = {}
    rows_in: Counter[str] = Counter()
    carried: defaultdict[str, Counter[str]] = defaultdict(Counter)
    new: defaultdict[str, list[GroupToAssign]] = defaultdict(list)
    for group in sorted(groups, key=lambda g: g.anchor):
        size = len(group.members)
        rows_in[group.stratum] += size
        known = Counter(m.previous for m in group.members if m.previous is not None)
        if known:
            split = max(known, key=lambda s: (known[s], proportions.of(s), -SPLITS.index(s)))
            result[group.anchor] = split
            carried[group.stratum][split] += size
        else:
            new[group.stratum].append(group)

    strata = sorted(new)
    room = {s: {k: proportions.of(k) * rows_in[s] - carried[s][k] for k in SPLITS} for s in strata}
    global_room = {k: sum(room[s][k] for s in strata) for k in SPLITS}
    largest: Counter[str] = Counter()
    for group in groups:
        largest[group.stratum] = max(largest[group.stratum], len(group.members))
    rngs = {s: random.Random(f"{seed}|{s}") for s in strata}

    for stratum in strata:  # step 2
        multi = [g for g in new[stratum] if len(g.members) > 1]
        rngs[stratum].shuffle(multi)
        multi.sort(key=lambda g: -len(g.members))  # stable: random order within a size
        for group in multi:
            size = len(group.members)
            split = _place_group(size, room[stratum], global_room, largest[stratum], rngs[stratum])
            result[group.anchor] = split
            room[stratum][split] -= size
            global_room[split] -= size

    singles = {s: [g for g in new[s] if len(g.members) == 1] for s in strata}  # step 3
    wanted = {s: _project_onto_simplex(room[s], len(singles[s])) for s in strata}
    quotas = _controlled_round(wanted, {s: len(singles[s]) for s in strata})
    for stratum in strata:
        rows = singles[stratum]
        rngs[stratum].shuffle(rows)
        position = 0
        for split in SPLITS:
            for group in rows[position : position + quotas[stratum][split]]:
                result[group.anchor] = split
            position += quotas[stratum][split]

    _repair_totals(groups, new, result, proportions, largest, random.Random(f"{seed}|repair"))
    return result


def _repair_totals(
    groups: Sequence[GroupToAssign],
    new: Mapping[str, Sequence[GroupToAssign]],
    result: dict[str, str],
    proportions: Proportions,
    largest: Mapping[str, int],
    rng: random.Random,
) -> None:
    """Step 4: move new groups until every split's total is within one group.

    Steps 2 and 3 keep every stratum within its bound. Strata made mostly of
    large groups have few single rows to even things out, so their overshoots
    can still add up in the totals. While a total is a group or more off, a
    new group is moved from a split over its total to one under it, choosing
    groups (in a seeded random order) whose stratum stays within its bound
    after the move and whose move brings the two totals closer to their
    targets. Carried groups never move (rule 2).
    """
    rows = sum(len(g.members) for g in groups)
    tolerance = max((len(g.members) for g in groups), default=0)
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    rows_in: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for group in groups:
        size = len(group.members)
        counts[group.stratum][result[group.anchor]] += size
        rows_in[group.stratum] += size
        totals[result[group.anchor]] += size
    movable = [g for s in sorted(new) for g in new[s]]
    rng.shuffle(movable)

    def gap(split: str) -> float:
        return totals[split] - proportions.of(split) * rows

    for _ in range(len(movable)):
        if all(abs(gap(split)) < tolerance for split in SPLITS):
            return
        over = max(SPLITS, key=gap)
        under = min(SPLITS, key=gap)
        moved = False
        for group in movable:
            size, stratum = len(group.members), group.stratum
            if result[group.anchor] != over:
                continue
            if max(abs(gap(over) - size), abs(gap(under) + size)) >= max(
                abs(gap(over)), abs(gap(under))
            ):
                continue
            in_stratum = counts[stratum]
            bound = largest[stratum]
            if abs(in_stratum[over] - size - proportions.of(over) * rows_in[stratum]) >= bound:
                continue
            if abs(in_stratum[under] + size - proportions.of(under) * rows_in[stratum]) >= bound:
                continue
            result[group.anchor] = under
            in_stratum[over] -= size
            in_stratum[under] += size
            totals[over] -= size
            totals[under] += size
            moved = True
            break
        if not moved:
            return


def _place_group(
    size: int,
    room: Mapping[str, float],
    global_room: Mapping[str, float],
    largest: int,
    rng: random.Random,
) -> str:
    """The split for one multi-row group. See :func:`assign_splits`, step 2."""
    fits = [split for split in SPLITS if room[split] >= size - _EPS]
    if fits:
        return rng.choices(fits, weights=[room[s] for s in fits])[0]
    bearable = [split for split in SPLITS if size - room[split] < largest]
    return max(
        bearable or SPLITS,
        key=lambda s: (global_room[s], room[s], -SPLITS.index(s)),
    )


_EPS = 1e-9


def _check_proportions(proportions: Proportions) -> None:
    shares = {split: proportions.of(split) for split in SPLITS}
    bad = [s for s, share in shares.items() if not math.isfinite(share) or share < 0]
    if bad:
        raise ValueError(f"proportions must be finite and not negative: {bad}")
    if abs(sum(shares.values()) - 1.0) > _EPS:
        raise ValueError(f"proportions must sum to 1, not {sum(shares.values())!r}")


def _project_onto_simplex(needs: Mapping[str, float], total: int) -> dict[str, float]:
    """The point ``x >= 0`` with ``sum(x) == total`` closest to ``needs``.

    ``x = max(needs - lam, 0)`` for the one ``lam`` that makes the sum right.
    Sorting the needs from the largest, ``lam`` is found from the longest
    prefix whose members all stay positive (Held, Wolfe and Crowder, 1974).
    """
    if total <= 0:
        return dict.fromkeys(needs, 0.0)
    shift = 0.0
    running = 0.0
    for count, value in enumerate(sorted(needs.values(), reverse=True), start=1):
        running += value
        candidate = (running - total) / count
        if value - candidate > 0:
            shift = candidate
    return {split: max(need - shift, 0.0) for split, need in needs.items()}


def _controlled_round(
    wanted: Mapping[str, Mapping[str, float]], rows: Mapping[str, int]
) -> dict[str, dict[str, int]]:
    """Round a strata-by-splits matrix: cells to floor or ceiling, row sums kept
    exact, column sums to their own rounding. See :func:`assign_splits`, step 2."""
    strata = sorted(wanted)
    base = {s: {k: math.floor(wanted[s][k] + _EPS) for k in SPLITS} for s in strata}
    open_cell = {s: {k: wanted[s][k] - base[s][k] > _EPS for k in SPLITS} for s in strata}
    row_need = {s: rows[s] - sum(base[s].values()) for s in strata}

    column = {k: sum(wanted[s][k] for s in strata) for k in SPLITS}
    column_total = {k: math.floor(column[k] + _EPS) for k in SPLITS}
    by_remainder = sorted(SPLITS, key=lambda k: (-(column[k] - column_total[k]), SPLITS.index(k)))
    for index in range(sum(rows.values()) - sum(column_total.values())):
        column_total[by_remainder[index % len(SPLITS)]] += 1
    column_need = {k: column_total[k] - sum(base[s][k] for s in strata) for k in SPLITS}

    extra = {s: dict.fromkeys(SPLITS, 0) for s in strata}

    def augment(stratum: str, seen: set[str]) -> bool:
        """Give ``stratum`` one more row in some split, rerouting others if needed."""
        for split in SPLITS:
            if split in seen or not open_cell[stratum][split] or extra[stratum][split]:
                continue
            seen.add(split)
            if column_need[split] > 0:
                column_need[split] -= 1
                extra[stratum][split] = 1
                return True
            for other in strata:
                if other != stratum and extra[other][split] and augment(other, seen):
                    extra[other][split] = 0
                    extra[stratum][split] = 1
                    return True
        return False

    for stratum in strata:
        for _ in range(row_need[stratum]):
            if not augment(stratum, set()):
                # Not expected (a rounding always exists); keep the row sum exact anyway.
                free = [k for k in SPLITS if not extra[stratum][k]]
                best = max(free, key=lambda k: wanted[stratum][k] - base[stratum][k])
                extra[stratum][best] = 1
    return {s: {k: base[s][k] + extra[s][k] for k in SPLITS} for s in strata}


def targets(rows: int, proportions: Proportions) -> Mapping[str, float]:
    """``{split: proportion × rows}``: the exact, unrounded target per split.

    Plumbing, not an owner unit: the shell's report and the tests both use it.
    """
    return {split: proportions.of(split) * rows for split in SPLITS}
