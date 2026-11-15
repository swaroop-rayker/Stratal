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
``docs/drafts/DATA-4_v1.md``. Tests: ``tests/unit/test_DATA_4_*.py`` (strict
expected failures until these are written).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

ALGORITHM_VERSION = 1
"""Bump this whenever you change what any function in this file returns.

It is part of the split stage's config, so a new value gives a new version ID
(ARCHITECTURE.md §3, stage memoization). It does not reset carry-over: rows of
an earlier split keep their split under a new algorithm as long as the
proportions and the seed are the same (DATA-4.5).
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
    3. Two items whose perceptual hashes are at most ``max_distance`` bits
       apart (Hamming distance of the hex strings read as bits) are in the
       same group.
    4. Grouping is transitive: if A joins B and B joins C, all three are one
       group. Two items are never in one group unless a chain of rule-2 and
       rule-3 links joins them.
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
    """
    raise NotImplementedError("Owner unit DATA-4.2: group_for_split")


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
    """
    raise NotImplementedError("Owner unit DATA-4.3: strata_for")


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
    """
    raise NotImplementedError("Owner unit DATA-4.1/4.4/4.5: assign_splits")


def targets(rows: int, proportions: Proportions) -> Mapping[str, float]:
    """``{split: proportion × rows}``: the exact, unrounded target per split.

    Plumbing, not an owner unit: the shell's report and the tests both use it.
    """
    return {split: proportions.of(split) * rows for split in SPLITS}
