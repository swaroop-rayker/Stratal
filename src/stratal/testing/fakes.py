"""Stand-ins for owner-implemented units, so plumbing can be built and tested.

These are deliberately NOT correct implementations. Each says which part of
its contract it ignores.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from PIL import Image

from stratal.data.dedupe import DuplicateGroup, ExactItem, HashedItem
from stratal.data.splitting import (
    NO_STRATUM,
    SPLITS,
    GroupToAssign,
    LeakageItem,
    Proportions,
    SplitGroup,
    StratumItem,
    StratumKey,
)
from stratal.data.versioning import VersionIdentity
from stratal.eval.report_stats import (
    Acceptance,
    AcceptanceRate,
    Interval,
    RejectionCounts,
    ReviewedItem,
    RowStatus,
    ScoreDistribution,
)
from stratal.mon.sink import Level, Metrics, SinkType
from stratal.storage.manifest import Row


def fake_version_identity(rows: Iterator[Row], config: Mapping[str, Any]) -> VersionIdentity:
    """Stand-in for DATA-3.2. It looks only at the row count and the config.

    It breaks contract rule 2 on purpose: two manifests with the same number of
    rows get the same content hash even if their rows differ. Good enough to
    name files and exercise the commit path; useless as a real identity.
    """
    count = sum(1 for _ in rows)
    content_hash = hashlib.sha256(f"FAKE rows={count}".encode()).hexdigest()
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    combined = hashlib.sha256(f"{content_hash}:{config_hash}".encode()).hexdigest()
    return VersionIdentity(content_hash, config_hash, f"ds_fake{combined[:8]}")


def no_exact_duplicates(items: Sequence[ExactItem]) -> list[DuplicateGroup]:
    """Stand-in for DATA-2.1 that finds nothing.

    It breaks contract rule 2 whenever two items share a hash, and the clean
    stage then refuses its output (INV-05). Use it only on folders without
    planted copies, or to test that refusal.
    """
    del items
    return []


def fake_perceptual_hash(image: Image.Image, *, hash_size: int) -> str:
    """Stand-in for DATA-2.2's hash: a digest of the exact pixels, not a perceptual hash.

    It keeps the shape of the contract (fixed-length lowercase hex of
    ``hash_size * hash_size`` bits) and breaks rule 3: images that differ in a
    single pixel get unrelated values.
    """
    digest = hashlib.sha256(f"{image.mode}:{image.size}:".encode() + image.tobytes()).hexdigest()
    return digest[: max(1, hash_size * hash_size // 4)]


def no_near_duplicates(items: Sequence[HashedItem], *, max_distance: int) -> list[DuplicateGroup]:
    """Stand-in for DATA-2.2's grouping that finds nothing and ignores the threshold."""
    del items, max_distance
    return []


class ScriptedGroups:
    """Returns groups a test wrote down in advance, to test what the clean stage does with them.

    Usable as either grouping function. It records the items it was called with.
    """

    def __init__(self, groups: Sequence[DuplicateGroup]) -> None:
        self.groups = list(groups)
        self.calls: list[list[str]] = []

    def __call__(
        self, items: Sequence[ExactItem] | Sequence[HashedItem], **_: Any
    ) -> list[DuplicateGroup]:
        self.calls.append(sorted(item.row_key for item in items))
        return list(self.groups)


class ListSink:
    """An EventSink that keeps events in a list, for assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        *,
        stage: str,
        message: str,
        level: Level = "INFO",
        substage: str | None = None,
        step: int | None = None,
        total: int | None = None,
        metrics: Metrics | None = None,
        reason: str | None = None,
        suggested_action: str | None = None,
        event_type: SinkType = "activity",
    ) -> None:
        if level == "WARN" and not (reason and suggested_action):
            # The runtime's Event refuses this (MON-1.3); fail here too, not only on the stack.
            raise ValueError(f"a WARN event needs a reason and a suggested_action: {message!r}")
        self.events.append(
            {
                "stage": stage,
                "substage": substage,
                "level": level,
                "message": message,
                "step": step,
                "total": total,
                "metrics": dict(metrics) if metrics else {},
                "reason": reason,
                "suggested_action": suggested_action,
                "type": event_type,
            }
        )

    def messages(self) -> list[str]:
        return [event["message"] for event in self.events]


# --------------------------------------------------- M5: DATA-4 and EVAL-1.1


def singleton_split_groups(items: Sequence[LeakageItem], *, max_distance: int) -> list[SplitGroup]:
    """Stand-in for DATA-4.2 that puts every row in a group of its own.

    It keeps rules 1, 5 and 6 and breaks 2 to 4: copies and near pairs are not
    joined, so they can land on both sides of a split. The split stage's own
    leakage check reports exactly that; use folders without near pairs.
    """
    del max_distance
    groups = [SplitGroup(item.sha256, (item.row_key,)) for item in items]
    return sorted(groups, key=lambda group: group.anchor)


def one_stratum(items: Sequence[StratumItem], *, keys: Sequence[StratumKey]) -> dict[str, str]:
    """Stand-in for DATA-4.3 that ignores the keys: every row is in ``NO_STRATUM``."""
    del keys
    return {item.row_key: NO_STRATUM for item in items}


def hashed_splits(
    groups: Sequence[GroupToAssign], *, proportions: Proportions, seed: int
) -> dict[str, str]:
    """Stand-in for DATA-4.1/4.4/4.5: the "pure hashing" alternative of the DATA-4 draft.

    A group with a known member takes the first such member's split (breaking
    rule 3 when members disagree). Any other group goes where a hash of the
    seed and its anchor falls on the cumulative proportions, so the shares are
    right only on average (breaking rules 4 and 5). Deterministic, which keeps
    rule 6.
    """
    result: dict[str, str] = {}
    for group in groups:
        known = [member.previous for member in group.members if member.previous is not None]
        if known:
            result[group.anchor] = known[0]
            continue
        digest = hashlib.sha256(f"{seed}:{group.anchor}".encode()).digest()
        point = int.from_bytes(digest[:8], "big") / 2**64
        cumulative = 0.0
        result[group.anchor] = SPLITS[-1]
        for split in SPLITS:
            cumulative += proportions.of(split)
            if point < cumulative:
                result[group.anchor] = split
                break
    return result


def lumped_rejection_counts(rows: Iterable[RowStatus]) -> RejectionCounts:
    """Stand-in for EVAL-1.1's counts: right totals, but every non-accepted row is
    filed under a made-up stage ``fake`` by its status, not by stage and reason."""
    total = accepted = 0
    by_status: Counter[str] = Counter()
    for row in rows:
        total += 1
        if row.status == "accepted":
            accepted += 1
        else:
            by_status[row.status] += 1
    return RejectionCounts(total, accepted, {"fake": dict(by_status)} if by_status else {})


def no_filter_rate(counts: RejectionCounts) -> float | None:
    """Stand-in for EVAL-1.1's filter rate: always "not computed"."""
    del counts
    return None


def crude_score_distribution(
    scores: Sequence[float], *, edges: Sequence[float]
) -> ScoreDistribution:
    """Stand-in for EVAL-1.1's distribution: count, extremes and mean only.

    No spread, no quantiles, and every score is put in ``above`` instead of its bin.
    """
    count = len(scores)
    return ScoreDistribution(
        count=count,
        mean=sum(scores) / count if count else None,
        std=None,
        minimum=min(scores) if count else None,
        maximum=max(scores) if count else None,
        quantiles={},
        edges=tuple(edges),
        histogram=tuple(0 for _ in range(max(0, len(edges) - 1))),
        below=0,
        above=count,
    )


def vacuous_interval(successes: int, trials: int, *, confidence: float = 0.95) -> Interval:
    """Stand-in for EVAL-1.1's Wilson interval: always [0, 1], never wrong and never useful."""
    return Interval(successes / trials if trials else 0.0, 0.0, 1.0, confidence)


def overall_only_acceptance(
    items: Sequence[ReviewedItem], *, confidence: float = 0.95
) -> Acceptance:
    """Stand-in for EVAL-1.1's review acceptance: overall only, nothing per split,
    and :func:`vacuous_interval` instead of a real interval."""
    decided = [item for item in items if item.decision is not None]
    accepted = sum(1 for item in decided if item.decision == "accept")
    interval = vacuous_interval(accepted, len(decided), confidence=confidence) if decided else None
    return Acceptance(
        sampled=len(items),
        undecided=len(items) - len(decided),
        overall=AcceptanceRate(len(decided), accepted, interval),
        by_split={},
    )
