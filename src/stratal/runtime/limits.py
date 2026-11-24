"""The training section of the settings: checkpoint interval and the watchdog's limits.

RUN-1.3, RUN-1.6, RUN-1.7; OC-9 (stored in the database, edited on SCR-14). Until a
section is saved, its values are the environment's (``STRATAL_THERMAL_PAUSE_C`` and
the rest), so a fresh install behaves as before. The watchdog reads them on every
check, so a saved change applies at once; the checkpoint interval is the default of
new training runs only, since a run's config is fixed when it starts (NFR-4).
"""

import hashlib
import json
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stratal.core.errors import ValidationFailed
from stratal.core.settings import Settings
from stratal.db.models import SettingsSection
from stratal.mon.events import utc_now

SECTION = "training"
RESUME_GAP_C = 5
"""``thermal_resume_c`` must be at least this far below ``thermal_pause_c`` (SCR-14)."""


class TrainingValues(BaseModel):
    """The training section (SCR-14 frame A)."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_interval_min: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Longest time between checkpoints, in minutes. 1–60, default 5.",
        json_schema_extra={"x-applies": "new jobs only"},
    )
    thermal_pause_c: int = Field(
        default=83,
        ge=60,
        le=100,
        description="Pause GPU jobs at this temperature, °C. 60 to the card's slowdown "
        "point, default 83.",
        json_schema_extra={"x-applies": "at once"},
    )
    thermal_resume_c: int = Field(
        default=75,
        ge=40,
        le=95,
        description="Resume below this temperature, °C. At least 5 below thermal_pause_c, "
        "default 75.",
        json_schema_extra={"x-applies": "at once"},
    )
    stall_timeout_s: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Seconds without progress before a job is marked stalled. 60–3600, "
        "default 300.",
        json_schema_extra={"x-applies": "at once"},
    )

    @model_validator(mode="after")
    def _resume_below_pause(self) -> Self:
        if self.thermal_resume_c > self.thermal_pause_c - RESUME_GAP_C:
            raise ValueError(
                f"thermal_resume_c must be at most {self.thermal_pause_c - RESUME_GAP_C}: "
                f"{RESUME_GAP_C} °C below thermal_pause_c ({self.thermal_pause_c})"
            )
        return self


def defaults(env: Settings) -> TrainingValues:
    """The environment's values, used until the section is saved. Not range-checked: a
    test may set a 1-second stall timeout through the environment."""
    return TrainingValues.model_construct(
        checkpoint_interval_min=5,
        thermal_pause_c=round(env.thermal_pause_c),
        thermal_resume_c=round(env.thermal_resume_c),
        stall_timeout_s=round(env.stall_timeout_s),
    )


def read(session: Session, env: Settings) -> TrainingValues:
    row = session.get(SettingsSection, SECTION)
    if row is None:
        return defaults(env)
    return TrainingValues.model_construct(**{**defaults(env).model_dump(), **row.values})


def version(values: TrainingValues) -> str:
    canonical = json.dumps(values.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def write(session: Session, values: TrainingValues, *, slowdown_c: float | None) -> None:
    """Save the section. ``thermal_pause_c`` may not exceed the card's slowdown point."""
    if slowdown_c is not None and values.thermal_pause_c > slowdown_c:
        raise ValidationFailed(
            f"thermal_pause_c must be at most the card's slowdown point, {slowdown_c:.0f} °C.",
            suggested_action="Pause the job before the card slows itself down.",
            errors=[
                {
                    "loc": ["values", "thermal_pause_c"],
                    "msg": f"at most {slowdown_c:.0f} (the slowdown point)",
                    "code": "less_than_equal",
                }
            ],
        )
    row: dict[str, Any] = {
        "section": SECTION,
        "values": values.model_dump(mode="json"),
        "updated_at": utc_now(),
    }
    session.execute(
        insert(SettingsSection)
        .values(**row)
        .on_conflict_do_update(index_elements=["section"], set_=row)
    )
    session.flush()


# ---------------------------------------------------------------------------- monitoring
MONITORING = "monitoring"


class MonitoringValues(BaseModel):
    """The monitoring section (SCR-14, M11): sampling intervals (MON-2.2), the alert
    thresholds (MON-2.6, OC-7) and the disk figure of the diagnostics (MON-3.2, ADR-0020)."""

    model_config = ConfigDict(extra="forbid")

    sample_interval_active_s: float = Field(
        default=1.0,
        ge=0.5,
        le=10.0,
        description="Seconds between samples while a job runs. 0.5–10, default 1.",
        json_schema_extra={"x-applies": "at once"},
    )
    sample_interval_idle_s: float = Field(
        default=10.0,
        ge=2.0,
        le=60.0,
        description="Seconds between samples while idle. 2–60, at least the active "
        "interval, default 10.",
        json_schema_extra={"x-applies": "at once"},
    )
    vram_alert_percent: int = Field(
        default=90,
        ge=50,
        le=99,
        description="Alert when the card's VRAM is above this share, %. 50–99, default 90.",
        json_schema_extra={"x-applies": "at once"},
    )
    ram_alert_percent: int = Field(
        default=85,
        ge=50,
        le=99,
        description="Alert when the VM's RAM is above this share, %. 50–99, default 85.",
        json_schema_extra={"x-applies": "at once"},
    )
    disk_bottleneck_mb_s: int = Field(
        default=50,
        ge=5,
        le=2000,
        description="Disk reads at least this fast, MB/s, count as the bottleneck of a GPU "
        "stage whose GPU is under 30 %. 5–2000, default 50.",
        json_schema_extra={"x-applies": "at once"},
    )

    @model_validator(mode="after")
    def _idle_not_faster(self) -> Self:
        if self.sample_interval_idle_s < self.sample_interval_active_s:
            raise ValueError(
                "sample_interval_idle_s must be at least sample_interval_active_s "
                f"({self.sample_interval_active_s:g})"
            )
        return self


def monitoring_defaults(env: Settings) -> MonitoringValues:
    """The environment's values, used until the section is saved (not range-checked)."""
    return MonitoringValues.model_construct(
        sample_interval_active_s=env.sample_interval_active_s,
        sample_interval_idle_s=env.sample_interval_idle_s,
        vram_alert_percent=round(env.vram_alert_percent),
        ram_alert_percent=round(env.ram_alert_percent),
        disk_bottleneck_mb_s=round(env.disk_bottleneck_mb_s),
    )


def read_monitoring(session: Session, env: Settings) -> MonitoringValues:
    row = session.get(SettingsSection, MONITORING)
    base = monitoring_defaults(env).model_dump()
    if row is None:
        return MonitoringValues.model_construct(**base)
    return MonitoringValues.model_construct(**{**base, **row.values})


def monitoring_version(values: MonitoringValues) -> str:
    canonical = json.dumps(values.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def write_monitoring(session: Session, values: MonitoringValues) -> None:
    row: dict[str, Any] = {
        "section": MONITORING,
        "values": values.model_dump(mode="json"),
        "updated_at": utc_now(),
    }
    session.execute(
        insert(SettingsSection)
        .values(**row)
        .on_conflict_do_update(index_elements=["section"], set_=row)
    )
    session.flush()
