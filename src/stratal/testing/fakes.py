"""Stand-ins for owner-implemented units, so plumbing can be built and tested.

These are deliberately NOT correct implementations. Each says which part of
its contract it ignores.
"""

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from PIL import Image

from stratal.data.dedupe import DuplicateGroup, ExactItem, HashedItem
from stratal.data.versioning import VersionIdentity
from stratal.mon.sink import Level, Metrics
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
    ) -> None:
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
            }
        )

    def messages(self) -> list[str]:
        return [event["message"] for event in self.events]
