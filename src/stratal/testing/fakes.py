"""Stand-ins for owner-implemented units, so plumbing can be built and tested.

These are deliberately NOT correct implementations. Each says which part of
its contract it ignores.
"""

import hashlib
import json
from collections.abc import Iterator, Mapping
from typing import Any

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
