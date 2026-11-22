"""Resource alerts: VRAM, RAM and GPU temperature (MON-2.6, MON-2.7; ADR-0020 part A).

The state machine is a pure function, :func:`step`. :class:`Alerts` runs it on every
machine sample the sampler takes (no database read), stores each firing and each
clearing as an ``alert`` event, and for ``gpu_hot`` asks the watchdog to pause the
running GPU job, the action recording the alert in ``caused_by``. The fourth alert,
``no_progress``, is raised by the watchdog's stall check, which already measures
silence.

An alert fires once the condition has held from the first sample over the threshold
to one at least :data:`HOLD_S` later, once per crossing; it re-arms below its re-arm
point (5 points below for VRAM and RAM, the resume limit for temperature).
"""

import contextlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from stratal.core.logging import get_logger
from stratal.db.models import Event as EventRow
from stratal.mon.events import Event, MetricValue
from stratal.mon.store import insert_events

log = get_logger(__name__)

AlertCode = Literal["vram_high", "ram_high", "gpu_hot"]
CODES: tuple[AlertCode, ...] = ("vram_high", "ram_high", "gpu_hot")
HOLD_S = 5.0
"""Seconds a condition must hold before its alert fires (K4)."""
REARM_POINTS = 5.0
"""VRAM and RAM re-arm this many percentage points below their threshold (K4)."""


@dataclass(frozen=True)
class Limits:
    vram_percent: float = 90.0
    ram_percent: float = 85.0
    pause_c: float = 83.0
    resume_c: float = 75.0


@dataclass(frozen=True)
class State:
    phase: Literal["armed", "over", "fired"] = "armed"
    since: float | None = None
    """When the condition first held, in seconds (``over`` only)."""
    uid: uuid.UUID | None = None
    """The firing's ``event_uid`` (``fired`` only)."""


@dataclass(frozen=True)
class Transition:
    code: AlertCode
    kind: Literal["fired", "cleared"]
    value: float
    threshold: float
    rearm_below: float
    uid: uuid.UUID
    """The alert's ``event_uid``: new when fired; the firing's when cleared."""


def bounds(code: AlertCode, limits: Limits) -> tuple[float, float]:
    """``(threshold, rearm_below)``. VRAM and RAM fire strictly above their threshold,
    temperature at it (RUN-1.6's pause)."""
    if code == "vram_high":
        return limits.vram_percent, limits.vram_percent - REARM_POINTS
    if code == "ram_high":
        return limits.ram_percent, limits.ram_percent - REARM_POINTS
    return limits.pause_c, limits.resume_c


def _over(code: AlertCode, value: float, threshold: float) -> bool:
    return value >= threshold if code == "gpu_hot" else value > threshold


def step(
    state: State, code: AlertCode, value: float | None, at: float, limits: Limits
) -> tuple[State, Transition | None]:
    """One sample's effect on one alert. ``value`` ``None`` (a figure not available)
    leaves the state unchanged."""
    if value is None:
        return state, None
    threshold, rearm = bounds(code, limits)
    if state.phase == "fired":
        if value < rearm:
            assert state.uid is not None
            return State(), Transition(code, "cleared", value, threshold, rearm, state.uid)
        return state, None
    if not _over(code, value, threshold):
        return State(), None
    if state.phase == "armed" or state.since is None:
        state = State("over", at)
    if at - (state.since if state.since is not None else at) >= HOLD_S:
        uid = uuid.uuid4()
        return State("fired", None, uid), Transition(code, "fired", value, threshold, rearm, uid)
    return state, None


def values_of(sample: Mapping[str, Any]) -> dict[AlertCode, float | None]:
    """Each alert's figure in a sampler row: VRAM and RAM as a percentage of their total
    (OC-7: the card's NVML total, the VM's RAM), temperature in °C."""

    def share(used: float | None, total: float | None) -> float | None:
        if used is None or not total:
            return None
        return used / total * 100.0

    ram_total = sample.get("ram_total_mb")
    ram_available = sample.get("ram_available_mb")
    ram_used = None if ram_total is None or ram_available is None else ram_total - ram_available
    return {
        "vram_high": share(sample.get("vram_used_mb"), sample.get("vram_total_mb")),
        "ram_high": share(ram_used, ram_total),
        "gpu_hot": sample.get("gpu_temp_c"),
    }


_UNIT = {"vram_high": "%", "ram_high": "%", "gpu_hot": " °C"}
_NAME = {"vram_high": "VRAM", "ram_high": "RAM", "gpu_hot": "GPU"}


def _fired_event(
    t: Transition, sample: Mapping[str, Any], ts: datetime, paused: list[str]
) -> Event:
    metrics: dict[str, MetricValue] = {
        "code": t.code,
        "state": "fired",
        "value": round(t.value, 1),
        "threshold": t.threshold,
        "rearm_below": t.rearm_below,
    }
    if t.code == "vram_high":
        message = (
            f"VRAM at {t.value:.0f}% of the card "
            f"({(sample.get('vram_used_mb') or 0) / 1024:.1f} of "
            f"{(sample.get('vram_total_mb') or 0) / 1024:.1f} GB) for {HOLD_S:.0f} s"
        )
        reason = f"Above the {t.threshold:.0f}% threshold (SCR-14)."
        action = (
            "None needed yet: the job continues. If it then fails out of memory, the "
            "failure is linked here; start it again with a smaller batch."
        )
    elif t.code == "ram_high":
        used = (sample.get("ram_total_mb") or 0) - (sample.get("ram_available_mb") or 0)
        message = (
            f"RAM at {t.value:.0f}% of the VM ({used / 1024:.1f} of "
            f"{(sample.get('ram_total_mb') or 0) / 1024:.1f} GB) for {HOLD_S:.0f} s"
        )
        reason = f"Above the {t.threshold:.0f}% threshold (SCR-14)."
        action = (
            "Close other programs or stop a job. A stage over its container's limit is "
            "killed, and that failure is linked here."
        )
    else:
        done = f"paused {', '.join(paused)}" if paused else "no GPU job to pause"
        message = f"GPU at {t.value:.0f} °C for {HOLD_S:.0f} s: {done}"
        reason = f"The GPU reached {t.value:.0f} °C; the limit is {t.threshold:.0f} °C."
        action = (
            f"None needed: paused GPU jobs resume by themselves below {t.rearm_below:.0f} °C. "
            "Check the laptop's airflow."
        )
    return Event(
        event_uid=t.uid,
        ts=ts,
        job_id=sample.get("job_id"),
        stage=sample.get("stage"),
        type="alert",
        level="WARN",
        message=message,
        reason=reason,
        suggested_action=action,
        metrics=metrics,
    )


def _cleared_event(t: Transition, sample: Mapping[str, Any], ts: datetime) -> Event:
    unit = _UNIT[t.code]
    return Event(
        ts=ts,
        job_id=sample.get("job_id"),
        stage=sample.get("stage"),
        type="alert",
        level="INFO",
        message=f"{_NAME[t.code]} back below {t.rearm_below:.0f}{unit} ({t.value:.0f}{unit})",
        metrics={"code": t.code, "state": "cleared", "value": round(t.value, 1)},
        caused_by=t.uid,
    )


class Alerts:
    """Runs :func:`step` for the three resource alerts on each machine sample.

    ``pause(temperature, caused_by)`` pauses the running GPU jobs and returns their IDs
    (the watchdog's :meth:`~stratal.mon.watchdog.Watchdog.pause_for_heat`).
    """

    def __init__(
        self,
        engine: Engine,
        *,
        limits: Callable[[], Limits],
        pause: Callable[[float, uuid.UUID], list[str]] | None = None,
    ) -> None:
        self._engine = engine
        self._limits = limits
        self._pause = pause
        self._last_limits = Limits()
        self.states: dict[AlertCode, State] = dict.fromkeys(CODES, State())
        self._restored = False

    def restore(self) -> None:
        """Pick up after a restart: an alert whose latest event is a firing is still
        fired, so it does not fire again for the same crossing."""
        with Session(self._engine) as session:
            for code in CODES:
                row = session.execute(
                    select(EventRow.event_uid, EventRow.metrics)
                    .where(EventRow.type == "alert", EventRow.metrics["code"].astext == code)
                    .order_by(EventRow.id.desc())
                    .limit(1)
                ).first()
                if row is not None and (row[1] or {}).get("state") == "fired":
                    self.states[code] = State("fired", None, row[0])
        self._restored = True

    def observe(self, sample: Mapping[str, Any]) -> list[Transition]:
        """Feed one sampler row; store what fired or cleared and act on it."""
        if not self._restored:
            with contextlib.suppress(Exception):
                self.restore()
        try:
            limits = self._last_limits = self._limits()
        except Exception as exc:  # keep the last limits read; a blip must not stop alerts
            log.warning("alert_limits_not_read", reason=str(exc)[:200])
            limits = self._last_limits
        ts: datetime = sample["ts"]
        at = ts.timestamp()
        figures = values_of(sample)
        transitions: list[Transition] = []
        for code in CODES:
            self.states[code], t = step(self.states[code], code, figures[code], at, limits)
            if t is not None:
                transitions.append(t)
        for t in transitions:
            try:
                self._record(t, sample, ts)
            except Exception as exc:
                log.warning("alert_not_recorded", code=t.code, reason=str(exc)[:300])
        return transitions

    def _record(self, t: Transition, sample: Mapping[str, Any], ts: datetime) -> None:
        if t.kind == "cleared":
            event = _cleared_event(t, sample, ts)
        else:
            paused: list[str] = []
            if t.code == "gpu_hot" and self._pause is not None:
                paused = self._pause(t.value, t.uid)
            event = _fired_event(t, sample, ts, paused)
        with Session(self._engine) as session, session.begin():
            insert_events(session, [event])
        log.info("alert", code=t.code, kind=t.kind, value=round(t.value, 1))
