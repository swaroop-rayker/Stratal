"""Stage 1: mark exact and near duplicates (DATA-2.1, DATA-2.2). The shell around the owner's units.

This module is the agent's plumbing. The decisions (which rows are copies of
which, and which one is kept) are made by the owner's functions in
:mod:`stratal.data.dedupe`, passed in as :class:`DedupeUnits`. This module
feeds them, caches pHash values by content, checks what they return against
their contract, writes the result as a new version and reports progress.

Rows are marked, never removed (CL-17, INV-06): a duplicate keeps its row,
with ``status = duplicate``, the group it belongs to and the row it duplicates.

Memory: only one small record per accepted row is held (key, hashes, size),
never the rows themselves. The manifest is read twice, once to collect and once
to write (NFR-2).
"""

import hashlib
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import PIL
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import StratalError
from stratal.data import dedupe
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.dedupe import DuplicateGroup, ExactItem, HashedItem
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import KIND_PHASH, DerivationCache, params_hash
from stratal.storage.manifest import (
    STATUS_ACCEPTED,
    STATUS_DUPLICATE,
    STATUS_REJECTED,
    Row,
    iter_rows,
)

STAGE = "clean"
_HASH_BATCH = 256
_HEX = frozenset("0123456789abcdef")


class CleanConfig(BaseModel):
    """Settings for the clean stage. The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phash_hash_size: int = Field(
        default=8,
        ge=4,
        le=32,
        description="Side of the pHash grid; the hash has hash_size² bits (DATA-2.2).",
    )
    phash_max_distance: int = Field(
        default=8,
        ge=0,
        le=1024,
        description="Images whose pHashes differ in at most this many bits are near duplicates "
        "(DATA-2.2). 8 chosen by the owner from measured distances (OC-5, 2026-11-15).",
    )
    algorithm_version: int = Field(
        default=dedupe.ALGORITHM_VERSION,
        ge=1,
        json_schema_extra={"readOnly": True},
        description="Version of the owner's dedupe code. A new value gives a new version ID "
        "and recomputes cached pHash values.",
    )


class PerceptualHasher(Protocol):
    def __call__(self, image: Image.Image, *, hash_size: int) -> str: ...


class NearGrouper(Protocol):
    def __call__(
        self, items: Sequence[HashedItem], *, max_distance: int
    ) -> list[DuplicateGroup]: ...


ExactGrouper = Callable[[Sequence[ExactItem]], list[DuplicateGroup]]


@dataclass(frozen=True)
class DedupeUnits:
    """The three owner functions the stage calls. Tests pass fakes here."""

    exact: ExactGrouper
    phash: PerceptualHasher
    near: NearGrouper


OWNER_UNITS = DedupeUnits(
    exact=dedupe.group_exact_duplicates,
    phash=dedupe.perceptual_hash,
    near=dedupe.group_near_duplicates,
)


class ContractBroken(StratalError):
    """An owner unit returned what its contract forbids. The stage fails; nothing is written."""


@dataclass(frozen=True)
class _Mark:
    group_id: str
    kind: str
    keeper: str


@dataclass
class _Accepted:
    sha256: str
    width: int
    height: int
    size_bytes: int


@dataclass
class _Plan:
    """What pass 1 decided, applied to the rows in pass 2."""

    marks: dict[str, _Mark] = field(default_factory=lambda: dict[str, _Mark]())
    phash: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """sha256 → pHash, for every accepted image that could be decoded."""
    unreadable: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """row_key → reason detail, for accepted rows whose image failed to decode here."""


@dataclass(frozen=True)
class CleanResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]
    seconds: float


def group_id(kind: str, members: Sequence[str]) -> str:
    """A stable ID for a group: the same members of the same kind always get the same ID."""
    digest = hashlib.sha256(f"{kind}\x00".encode() + "\x00".join(members).encode()).hexdigest()
    return f"dg_{digest[:12]}"


def _check_groups(
    groups: list[DuplicateGroup], allowed: set[str], *, unit: str, already: set[str]
) -> None:
    """Fail the stage if ``groups`` breaks the shared rules of the grouping contracts."""

    def broken(detail: str) -> ContractBroken:
        return ContractBroken(
            f"{unit} broke its contract: {detail}",
            suggested_action=f"Fix {unit} in src/stratal/data/dedupe.py so it meets the rules in "
            "its docstring; its tests in tests/unit/ show the expected behaviour.",
        )

    if not isinstance(groups, list):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise broken(f"it returned {type(groups).__name__}, not a list")
    seen: set[str] = set()
    previous_keeper: str | None = None
    for group in groups:
        members = list(group.members)
        if len(members) < 2:
            raise broken(f"group kept by {group.keeper!r} has fewer than two members")
        if members != sorted(members) or len(set(members)) != len(members):
            raise broken(f"members of the group kept by {group.keeper!r} are not sorted and unique")
        if group.keeper not in members:
            raise broken(f"keeper {group.keeper!r} is not one of its group's members")
        if previous_keeper is not None and group.keeper <= previous_keeper:
            raise broken("the groups are not sorted by keeper")
        previous_keeper = group.keeper
        unknown = [m for m in members if m not in allowed]
        if unknown:
            raise broken(f"row {unknown[0]!r} was not among the rows it was given")
        repeated = [m for m in members if m in seen or m in already]
        if repeated:
            raise broken(f"row {repeated[0]!r} is in more than one group")
        seen.update(members)


def _collect(parent_manifest: Path) -> tuple[int, dict[str, _Accepted]]:
    """Pass 1 over the parent: the row count, and the accepted rows' facts."""
    total = 0
    accepted: dict[str, _Accepted] = {}
    columns = ["row_key", "sha256", "status", "width", "height", "size_bytes"]
    for row in iter_rows(parent_manifest, columns=columns):
        total += 1
        if row["status"] == STATUS_ACCEPTED:
            accepted[row["row_key"]] = _Accepted(
                sha256=row["sha256"],
                width=int(row["width"]),
                height=int(row["height"]),
                size_bytes=int(row["size_bytes"]),
            )
    return total, accepted


def _missing_blob(row_key: str, sha256: str) -> StratalError:
    return StratalError(
        f"The stored file of {row_key!r} (blob {sha256[:12]}…) is missing from the data volume.",
        suggested_action="Check that the stratal-data volume is the one these versions were "
        "made on; re-ingest the source if the blobs were lost.",
    )


def _check_hash(value: object, hash_size: int, expected_len: int | None) -> str:
    if not isinstance(value, str) or not value or set(value) - _HEX:
        raise ContractBroken(
            f"perceptual_hash returned {value!r}; it must be lowercase hex "
            f"(hash_size={hash_size}).",
            suggested_action="Fix perceptual_hash in src/stratal/data/dedupe.py (rule 2).",
        )
    if expected_len is not None and len(value) != expected_len:
        raise ContractBroken(
            f"perceptual_hash returned hashes of {expected_len} and {len(value)} characters "
            f"for the same hash_size={hash_size}; the length must be fixed.",
            suggested_action="Fix perceptual_hash in src/stratal/data/dedupe.py (rule 2).",
        )
    return value


class _Cleaner:
    def __init__(
        self,
        *,
        data_dir: Path,
        config: CleanConfig,
        sink: EventSink,
        cache: DerivationCache,
        units: DedupeUnits,
        checkpoint: Callable[[], None],
    ) -> None:
        self.blobs = BlobStore(data_dir)
        self.config = config
        self.sink = sink
        self.cache = cache
        self.units = units
        self.checkpoint = checkpoint
        self.hashed = 0
        self.cache_hits = 0

    def exact(self, accepted: dict[str, _Accepted], plan: _Plan) -> list[DuplicateGroup]:
        self.sink.emit(
            stage=STAGE,
            substage="exact",
            message=f"Finding exact duplicates among {len(accepted):,} accepted files",
        )
        items = [ExactItem(key, facts.sha256) for key, facts in sorted(accepted.items())]
        groups = self.units.exact(items)
        _check_groups(groups, set(accepted), unit="group_exact_duplicates", already=set())
        for group in groups:
            hashes = {accepted[m].sha256 for m in group.members}
            if len(hashes) != 1:
                raise ContractBroken(
                    f"group_exact_duplicates put files with different content in the group kept "
                    f"by {group.keeper!r}.",
                    suggested_action="Fix group_exact_duplicates (rule 2): only equal sha256 "
                    "values are exact duplicates.",
                )
            gid = group_id("exact", group.members)
            for member in group.members:
                plan.marks[member] = _Mark(gid, "exact", group.keeper)
        return groups

    def remaining(self, accepted: dict[str, _Accepted], plan: _Plan) -> dict[str, _Accepted]:
        """Accepted rows left after exact duplicates; enforces INV-05."""
        left = {
            key: facts
            for key, facts in accepted.items()
            if key not in plan.marks or plan.marks[key].keeper == key
        }
        owner: dict[str, str] = {}
        for key, facts in sorted(left.items()):
            if facts.sha256 in owner:
                raise ContractBroken(
                    f"After exact-duplicate marking, {owner[facts.sha256]!r} and {key!r} are both "
                    "accepted with the same content (INV-05).",
                    suggested_action="Fix group_exact_duplicates (rule 2): every row whose hash "
                    "occurs more than once must be in a group.",
                )
            owner[facts.sha256] = key
        return left

    def hash_images(self, left: dict[str, _Accepted], plan: _Plan) -> None:
        """pHash every remaining image once, through the cache, committing per batch."""
        size = self.config.phash_hash_size
        # Pillow's resampling feeds the hash, so a Pillow upgrade must not reuse old values.
        params = params_hash(
            {
                "kind": KIND_PHASH,
                "hash_size": size,
                "algorithm": self.config.algorithm_version,
                "pillow": PIL.__version__,
            }
        )
        by_hash = {facts.sha256: key for key, facts in sorted(left.items())}
        shas = sorted(by_hash)
        total = len(shas)
        expected_len: int | None = None
        for start in range(0, total, _HASH_BATCH):
            self.checkpoint()
            batch = shas[start : start + _HASH_BATCH]
            found = self.cache.get_many(KIND_PHASH, params, batch)
            self.cache_hits += len(found)
            fresh: dict[str, str] = {}
            for sha in batch:
                if sha in found:
                    value = found[sha]
                else:
                    path = self.blobs.path_for(sha)
                    if not path.is_file():
                        raise _missing_blob(by_hash[sha], sha)
                    try:
                        with Image.open(path) as opened:
                            opened.load()
                            image = opened.copy()
                    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
                        detail = f"{type(exc).__name__}: {exc}".replace(str(path), by_hash[sha])
                        plan.unreadable[by_hash[sha]] = detail[:500]  # no local path (E-010)
                        continue
                    # Outside the try: an error raised by the owner's function is a bug to
                    # report, not a corrupt image.
                    value = self.units.phash(image, hash_size=size)
                    self.hashed += 1
                value = _check_hash(value, size, expected_len)
                expected_len = len(value)
                plan.phash[sha] = value
                if sha not in found:
                    fresh[sha] = value
            self.cache.put_many(KIND_PHASH, params, fresh)
            done = min(start + _HASH_BATCH, total)
            self.sink.emit(
                stage=STAGE,
                substage="phash",
                message=f"Hashing images (pHash) {done:,}/{total:,}",
                step=done,
                total=total,
                metrics={"hashed": self.hashed, "cache_hits": self.cache_hits},
            )

    def near(self, left: dict[str, _Accepted], plan: _Plan) -> list[DuplicateGroup]:
        items = [
            HashedItem(key, plan.phash[facts.sha256], facts.width, facts.height, facts.size_bytes)
            for key, facts in sorted(left.items())
            if facts.sha256 in plan.phash
        ]
        groups = self.units.near(items, max_distance=self.config.phash_max_distance)
        already = {key for key, mark in plan.marks.items() if mark.keeper != key}
        _check_groups(
            groups, {item.row_key for item in items}, unit="group_near_duplicates", already=already
        )
        for group in groups:
            gid = group_id("near", group.members)
            for member in group.members:
                # An exact-group keeper that is also in a near group joins the near group:
                # it is the near group that decides whether it stays accepted.
                plan.marks[member] = _Mark(gid, "near", group.keeper)
        self.sink.emit(
            stage=STAGE,
            substage="compare",
            message=f"Comparing hashes: {len(groups):,} near-duplicate groups found",
            metrics={"near_groups": len(groups), "max_distance": self.config.phash_max_distance},
        )
        return groups


def _apply(rows: Iterator[Row], accepted: dict[str, _Accepted], plan: _Plan) -> Iterator[Row]:
    """Pass 2: the parent's rows with this stage's decisions applied."""
    for row in rows:
        key = row["row_key"]
        facts = accepted.get(key)
        if facts is None:
            yield row
            continue
        if key in plan.unreadable:
            row.update(
                status=STATUS_REJECTED,
                decided_at_stage=STAGE,
                reason_code="corrupt_image",
                reason_detail=plan.unreadable[key],
            )
            yield row
            continue
        row["phash"] = plan.phash.get(facts.sha256)
        mark = plan.marks.get(key)
        if mark is not None:
            row.update(dup_kind=mark.kind, dup_group_id=mark.group_id)
            if mark.keeper != key:
                row.update(
                    status=STATUS_DUPLICATE,
                    decided_at_stage=STAGE,
                    reason_code=f"{mark.kind}_duplicate",
                    reason_detail=f"{mark.kind} duplicate of {mark.keeper}",
                    dup_of=mark.keeper,
                )
        yield row


def run_clean(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: CleanConfig,
    derive: VersionDeriver,
    sink: EventSink,
    cache: DerivationCache,
    units: DedupeUnits = OWNER_UNITS,
    checkpoint: Callable[[], None] = lambda: None,
) -> CleanResult:
    """Mark duplicates in the parent version and commit the result as a ``clean`` version."""
    started = time.monotonic()
    cleaner = _Cleaner(
        data_dir=data_dir, config=config, sink=sink, cache=cache, units=units, checkpoint=checkpoint
    )
    total, accepted = _collect(parent_manifest)
    plan = _Plan()

    exact_groups = cleaner.exact(accepted, plan)
    left = cleaner.remaining(accepted, plan)
    # Exact copies share their keeper's content, so they share its pHash too.
    cleaner.hash_images(left, plan)
    near_groups = cleaner.near({k: v for k, v in left.items() if k not in plan.unreadable}, plan)
    checkpoint()

    exact_marked = sum(1 for m in plan.marks.values() if m.kind == "exact")
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), accepted, plan),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    by_reason: Any = record.counts.get("by_reason") or {}
    seconds = round(time.monotonic() - started, 3)
    metrics: dict[str, MetricValue] = {
        "rows": total,
        "input_accepted": len(accepted),
        "duplicates_exact": int(by_reason.get("exact_duplicate", 0)),
        "duplicates_near": int(by_reason.get("near_duplicate", 0)),
        "duplicates_semantic": int(by_reason.get("semantic_duplicate", 0)),
        "groups_exact": len(exact_groups),
        "groups_near": len(near_groups),
        "exact_rows_in_groups": exact_marked,
        "rejected_corrupt_image": len(plan.unreadable),
        "hashed": cleaner.hashed,
        "cache_hits": cleaner.cache_hits,
        "accepted": int(record.counts.get("accepted", 0)),
        "images_per_s": round(len(left) / max(seconds, 1e-9), 1),
    }
    return CleanResult(record, metrics, seconds)
