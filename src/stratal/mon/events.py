"""The event model (MON-1.1 to MON-1.3, DATA_MODEL.md §4.6).

One shape for everything a job says about itself. An event is built and
validated where it is emitted, travels as JSON, and is stored unchanged.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Level = Literal["DEBUG", "INFO", "WARN", "ERROR"]
EventType = Literal[
    "stage_started",
    "stage_progress",
    "stage_completed",
    "stage_failed",
    "activity",
    "alert",
    "action",
    "model_loaded",
    "model_unloaded",
    "job_state",
    "system",
]
MetricValue = float | int | str


def utc_now() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """One structured event. The fields are the ones in SPEC MON-1 plus an ID,
    a type, and the reason and suggested action that MON-1.3 requires."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_uid: uuid.UUID = Field(default_factory=uuid.uuid4)
    ts: datetime = Field(default_factory=utc_now)
    job_id: str | None = None
    stage: str | None = None
    substage: str | None = None
    version_id: str | None = None
    type: EventType = "activity"
    level: Level = "INFO"
    step: int | None = None
    total: int | None = None
    message: str = Field(min_length=1)
    metrics: dict[str, MetricValue] | None = None
    reason: str | None = None
    suggested_action: str | None = None
    attempt: int | None = None

    @model_validator(mode="after")
    def _warnings_explain_themselves(self) -> "Event":
        """MON-1.3: a WARN or ERROR without a reason and an action is rejected."""
        if self.level in ("WARN", "ERROR") and not (self.reason and self.suggested_action):
            raise ValueError(
                f"a {self.level} event needs both 'reason' and 'suggested_action' (MON-1.3)"
            )
        return self

    def to_json(self) -> str:
        return self.model_dump_json()

    def to_row(self) -> dict[str, Any]:
        """Column values for the ``events`` table."""
        return self.model_dump()


def oom_event(
    *,
    job_id: str,
    stage: str,
    batch_size: int,
    attempt: int | None = None,
    detail: str = "PyTorch ran out of VRAM",
) -> Event:
    """The event for a GPU out-of-memory error, with a concrete smaller batch (MON-1.3.AC1)."""
    smaller = max(1, batch_size // 2)
    return Event(
        job_id=job_id,
        stage=stage,
        attempt=attempt,
        type="alert",
        level="ERROR" if batch_size <= 1 else "WARN",
        message=f"Out of GPU memory at batch size {batch_size}",
        reason=detail,
        suggested_action=(
            "the batch size is already 1; use a smaller model or lower resolution"
            if batch_size <= 1
            else f"reduce batch size from {batch_size} → {smaller}"
        ),
        metrics={"batch_size": batch_size, "suggested_batch_size": smaller},
    )
