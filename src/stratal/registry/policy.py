"""Which weights a lineage keeps (REG-2.1 to REG-2.5, REG-2.8; ADR-0015 rule 4).

A pure function over facts the caller gathers, so the rule is tested on its
own and the dry run, the confirmation and the real run all use the same list.

Per lineage it keeps:

- the base (REG-2.1);
- the latest trained version (REG-2.2);
- the ``keep_intermediate`` most recent trained versions before it, every one
  when it is ``None`` ("all", REG-2.3);
- pinned versions (REG-2.5).

Every other trained version whose weights are on disk is deleted, unless it is
in use or the parent of another version: then it is *blocked*, which retention
logs as a refusal (REG-2.8). Partial versions are never deleted here; their
weights are their run's checkpoint, which follows the run (REG-2.6).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class VersionFacts:
    id: str
    state: str
    """``base``, ``partial`` or ``trained``."""
    weights_status: str
    """``present``, ``deleting`` or ``weights_deleted``."""
    order: datetime
    """When it became what it is: ``trained_at`` for a trained version."""
    weights_bytes: int = 0
    pinned: bool = False
    in_use: str | None = None
    """Why it is in use, e.g. "loaded for inference"; ``None`` if it is not."""
    children: tuple[str, ...] = ()
    """Versions trained from it (REG-2.8b)."""


@dataclass(frozen=True)
class Decision:
    version_id: str
    reason: str
    weights_bytes: int = 0


@dataclass(frozen=True)
class LineagePlan:
    latest: str | None
    delete: list[Decision] = field(default_factory=lambda: list[Decision]())
    keep: list[Decision] = field(default_factory=lambda: list[Decision]())
    blocked: list[Decision] = field(default_factory=lambda: list[Decision]())

    @property
    def bytes_to_free(self) -> int:
        return sum(d.weights_bytes for d in self.delete)


def plan_lineage(versions: Sequence[VersionFacts], keep_intermediate: int | None) -> LineagePlan:
    """What retention does to one lineage under ``keep_intermediate`` (``None`` = all)."""
    if keep_intermediate is not None and keep_intermediate < 0:
        raise ValueError("keep_intermediate must be at least 0, or None for all")
    # A version left ``deleting`` by a crash is finished by recovery, not planned again.
    trained = sorted(
        (v for v in versions if v.state == "trained" and v.weights_status == "present"),
        key=lambda v: (v.order, v.id),
        reverse=True,
    )
    latest = trained[0].id if trained else None
    window = len(trained) if keep_intermediate is None else 1 + keep_intermediate
    plan = LineagePlan(latest=latest)
    for v in versions:
        if v.state == "base":
            plan.keep.append(Decision(v.id, "base: never deleted (REG-2.1)", v.weights_bytes))
        elif v.state == "partial":
            plan.keep.append(
                Decision(v.id, "partial: its weights are its run's checkpoint", v.weights_bytes)
            )
    for rank, v in enumerate(trained):
        if rank == 0:
            plan.keep.append(Decision(v.id, "latest (REG-2.2)", v.weights_bytes))
        elif rank < window:
            n = "all" if keep_intermediate is None else str(keep_intermediate)
            plan.keep.append(
                Decision(v.id, f"intermediate: keep_intermediate = {n} (REG-2.3)", v.weights_bytes)
            )
        elif v.pinned:
            plan.keep.append(Decision(v.id, "pinned (REG-2.5)", v.weights_bytes))
        elif v.in_use is not None:
            plan.blocked.append(Decision(v.id, f"in use: {v.in_use} (REG-2.8)", v.weights_bytes))
        elif v.children:
            plan.blocked.append(
                Decision(
                    v.id,
                    f"parent of {', '.join(sorted(v.children))} (REG-2.8)",
                    v.weights_bytes,
                )
            )
        else:
            plan.delete.append(Decision(v.id, "beyond keep_intermediate", v.weights_bytes))
    return plan
