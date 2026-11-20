"""Clean for tables and texts: ``clean_table`` and ``clean_text`` (DATA-2.1, DATA-2.4, ADR-0019).

* ``clean_text`` first normalises every accepted text (Unicode NFKC,
  whitespace runs collapsed, ends trimmed, DATA-2.4), writes the normalised
  text back with its new content hash, and rejects a text that is then empty
  (``empty_text``) or longer than ``max_chars`` (``text_too_long``). Every
  rejection keeps its row and reason, and the stage reports how many it
  dropped and names the first of each (DATA-2.4.AC1).
* Both then mark exact duplicates by content hash with the same unit the image
  clean stage uses (:func:`stratal.data.dedupe.group_exact_duplicates`): a
  record or text twice in the data would otherwise sit on both sides of a split.

Rows are marked, never removed (CL-17). The table's schema is passed on.
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stratal.data import dedupe
from stratal.data.clean import ContractBroken, ExactGrouper, check_groups, group_id
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.dataset_types import MAX_TEXT_CHARS
from stratal.data.dedupe import ExactItem
from stratal.data.records import content_hash, inherited_metadata, normalise_text
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.manifest import (
    STATUS_ACCEPTED,
    STATUS_DUPLICATE,
    STATUS_REJECTED,
    Row,
    iter_rows,
)

_EXAMPLES = 5


class CleanTableConfig(BaseModel):
    """Settings for ``clean_table``. The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_version: int = Field(
        default=dedupe.ALGORITHM_VERSION,
        ge=1,
        json_schema_extra={"readOnly": True},
        description="Version of the exact-duplicate code; a new value gives a new version ID.",
    )


class CleanTextConfig(BaseModel):
    """Settings for ``clean_text`` (DATA-2.4). The resolved values are part of the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_chars: int = Field(
        default=MAX_TEXT_CHARS,
        ge=1,
        le=1_000_000,
        description="A text longer than this, after normalising, is dropped as text_too_long.",
    )
    unicode_form: Literal["NFKC"] = Field(
        default="NFKC", description="Unicode normal form applied before anything else."
    )
    algorithm_version: int = Field(
        default=dedupe.ALGORITHM_VERSION,
        ge=1,
        json_schema_extra={"readOnly": True},
        description="Version of the exact-duplicate code; a new value gives a new version ID.",
    )


@dataclass(frozen=True)
class _Mark:
    group_id: str
    keeper: str


@dataclass
class _Plan:
    rejected: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict[str, tuple[str, str]]()
    )
    """row_key → (reason code, detail) for texts dropped by DATA-2.4."""
    hashes: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """row_key → content hash, for the accepted rows that remain."""
    marks: dict[str, _Mark] = field(default_factory=lambda: dict[str, _Mark]())
    changed: int = 0
    """Texts that normalising changed."""


@dataclass(frozen=True)
class RecordCleanResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]
    seconds: float


def _text_decision(raw: str | None, config: CleanTextConfig) -> tuple[str, tuple[str, str] | None]:
    text = normalise_text(raw or "", form=config.unicode_form)
    if not text:
        return text, ("empty_text", "nothing left after normalising whitespace")
    if len(text) > config.max_chars:
        return text, ("text_too_long", f"{len(text):,} characters; at most {config.max_chars:,}")
    return text, None


def _plan(
    parent_manifest: Path,
    config: CleanTableConfig | CleanTextConfig,
    exact: ExactGrouper,
    checkpoint: Callable[[], None],
) -> tuple[int, _Plan]:
    """Pass 1: decide every rejection and duplicate; only keys and hashes are held."""
    plan = _Plan()
    total = 0
    is_text = isinstance(config, CleanTextConfig)
    columns = (
        ["row_key", "status", "sha256", "text"] if is_text else ["row_key", "status", "sha256"]
    )
    for row in iter_rows(parent_manifest, columns=columns):
        total += 1
        if row["status"] != STATUS_ACCEPTED:
            continue
        key = str(row["row_key"])
        if isinstance(config, CleanTextConfig):
            text, problem = _text_decision(row["text"], config)
            if problem is not None:
                plan.rejected[key] = problem
                continue
            if text != row["text"]:
                plan.changed += 1
            plan.hashes[key] = content_hash(text)
        else:
            plan.hashes[key] = str(row["sha256"])
    checkpoint()
    items = [ExactItem(key, sha) for key, sha in sorted(plan.hashes.items())]
    groups = exact(items)
    check_groups(groups, set(plan.hashes), unit="group_exact_duplicates", already=set())
    for group in groups:
        if len({plan.hashes[m] for m in group.members}) != 1:
            raise ContractBroken(
                f"group_exact_duplicates put different content in the group kept by "
                f"{group.keeper!r}.",
                suggested_action="Fix group_exact_duplicates (rule 2).",
            )
        gid = group_id("exact", group.members)
        for member in group.members:
            plan.marks[member] = _Mark(gid, group.keeper)
    return total, plan


def _apply(
    rows: Iterator[Row], plan: _Plan, config: CleanTableConfig | CleanTextConfig, stage: str
) -> Iterator[Row]:
    for row in rows:
        key = str(row["row_key"])
        if row["status"] != STATUS_ACCEPTED:
            yield row
            continue
        if isinstance(config, CleanTextConfig):
            row["text"], _ = _text_decision(row["text"], config)
            if key in plan.rejected:
                code, detail = plan.rejected[key]
                row.update(
                    status=STATUS_REJECTED,
                    decided_at_stage=stage,
                    reason_code=code,
                    reason_detail=detail,
                )
                yield row
                continue
            row["sha256"] = plan.hashes[key]
        mark = plan.marks.get(key)
        if mark is not None:
            row.update(dup_kind="exact", dup_group_id=mark.group_id)
            if mark.keeper != key:
                row.update(
                    status=STATUS_DUPLICATE,
                    decided_at_stage=stage,
                    reason_code="exact_duplicate",
                    reason_detail=f"exact duplicate of {mark.keeper}",
                    dup_of=mark.keeper,
                )
        yield row


def run_clean_records(
    *,
    stage: str,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: CleanTableConfig | CleanTextConfig,
    derive: VersionDeriver,
    sink: EventSink,
    exact: ExactGrouper = dedupe.group_exact_duplicates,
    checkpoint: Callable[[], None] = lambda: None,
) -> RecordCleanResult:
    """Clean a table's or texts' version and commit the result as a ``stage`` version."""
    started = time.monotonic()
    sink.emit(stage=stage, substage="scan", message="Checking records")
    total, plan = _plan(parent_manifest, config, exact, checkpoint)
    by_code: dict[str, list[str]] = {}
    for key, (code, _) in sorted(plan.rejected.items()):
        by_code.setdefault(code, []).append(key)
    for code, keys in sorted(by_code.items()):
        sink.emit(
            stage=stage,
            substage="normalise",
            level="WARN",
            message=f"Dropped {len(keys):,} rows as {code}, e.g. {', '.join(keys[:_EXAMPLES])}",
            reason="DATA-2.4: empty and over-length texts are dropped after normalising.",
            suggested_action="Nothing to do if expected; raise max_chars to keep longer texts. "
            "Each dropped row keeps its reason in the version.",
            metrics={f"rejected_{code}": len(keys)},
        )
    duplicates = sum(1 for key, mark in plan.marks.items() if mark.keeper != key)
    message = f"{duplicates:,} exact duplicates marked"
    if isinstance(config, CleanTextConfig):
        message += f"; {plan.changed:,} texts changed by normalising"
    sink.emit(
        stage=stage, substage="exact", message=message, metrics={"duplicates_exact": duplicates}
    )
    record = commit_version(
        data_dir=data_dir,
        stage=stage,
        parent_version_id=parent_version_id,
        rows=_apply(iter_rows(parent_manifest), plan, config, stage),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
        metadata=inherited_metadata(parent_manifest),
    )
    by_reason: Any = record.counts.get("by_reason") or {}
    metrics: dict[str, MetricValue] = {
        "rows": total,
        "input_accepted": len(plan.hashes) + len(plan.rejected),
        "duplicates_exact": int(by_reason.get("exact_duplicate", 0)),
        "rejected_empty_text": len(by_code.get("empty_text", [])),
        "rejected_text_too_long": len(by_code.get("text_too_long", [])),
        "normalised": plan.changed,
        "accepted": int(record.counts.get("accepted", 0)),
    }
    return RecordCleanResult(record, metrics, round(time.monotonic() - started, 3))
