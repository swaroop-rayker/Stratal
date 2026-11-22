"""MON-2.6 (ADR-0020 part A, K4): the alert state machine on simulated samples.

Each alert fires once its condition has held for 5 s, once per crossing, and re-arms
below its re-arm point; a missing figure changes nothing.
"""

import pytest

from stratal.mon.alerts import HOLD_S, Limits, State, Transition, step, values_of

LIMITS = Limits(vram_percent=90, ram_percent=85, pause_c=83, resume_c=75)


def _run(code: str, series: list[tuple[float, float | None]]) -> list[Transition]:
    state, out = State(), list[Transition]()
    for at, value in series:
        state, t = step(state, code, value, at, LIMITS)  # type: ignore[arg-type]
        if t is not None:
            out.append(t)
    return out


@pytest.mark.parametrize(
    ("code", "over", "under"),
    [("vram_high", 93.0, 80.0), ("ram_high", 87.0, 70.0), ("gpu_hot", 84.0, 72.0)],
)
def test_MON_2_6_each_alert_fires_after_its_hold_and_clears(
    code: str, over: float, under: float
) -> None:
    fired = _run(code, [(0, over), (1, over), (2, over), (3, over), (4, over)])
    assert fired == [], "held for 4 s only"
    events = _run(code, [(0, over), (1, over), (5, over), (6, over), (9, over), (10, under)])
    assert [e.kind for e in events] == ["fired", "cleared"], "once per crossing, then cleared"
    assert events[0].value == over and events[1].uid == events[0].uid


def test_MON_2_6_a_dip_below_the_threshold_restarts_the_hold() -> None:
    assert _run("vram_high", [(0, 95), (3, 89), (4, 95), (8, 95)]) == []
    assert len(_run("vram_high", [(0, 95), (3, 89), (4, 95), (4 + HOLD_S, 95)])) == 1


def test_MON_2_6_vram_and_ram_rearm_five_points_below() -> None:
    # 90 % threshold: 86 % is below the threshold but not below 85, so no re-arm.
    events = _run("vram_high", [(0, 95), (5, 95), (6, 86), (7, 95), (12, 95), (13, 84)])
    assert [e.kind for e in events] == ["fired", "cleared"]
    assert events[0].rearm_below == 85


def test_MON_2_6_temperature_fires_at_the_limit_and_rearms_at_the_resume_limit() -> None:
    assert len(_run("gpu_hot", [(0, 83), (5, 83)])) == 1, "at 83 °C, not above it"
    events = _run("gpu_hot", [(0, 84), (5, 84), (6, 78), (7, 76), (8, 74)])
    assert [(e.kind, e.value) for e in events] == [("fired", 84), ("cleared", 74)]
    assert events[0].rearm_below == 75, "owner's choice, ADR-0020 point 1"


def test_MON_2_6_vram_and_ram_fire_only_above_the_threshold() -> None:
    assert _run("vram_high", [(0, 90), (6, 90)]) == []
    assert len(_run("vram_high", [(0, 90.1), (6, 90.1)])) == 1


def test_MON_2_6_a_missing_figure_changes_nothing() -> None:
    events = _run("vram_high", [(0, 95), (2, None), (5, 95)])
    assert [e.kind for e in events] == ["fired"]
    fired = State("fired", None, events[0].uid)
    state, t = step(fired, "vram_high", None, 9, LIMITS)
    assert (state, t) == (fired, None)


def test_MON_2_6_figures_from_a_sample_row() -> None:
    row = {
        "vram_used_mb": 5645.0,
        "vram_total_mb": 6141.0,
        "ram_total_mb": 10000.0,
        "ram_available_mb": 1000.0,
        "gpu_temp_c": 84.0,
    }
    figures = values_of(row)
    assert figures["vram_high"] == pytest.approx(91.92, abs=0.01)
    assert figures["ram_high"] == pytest.approx(90.0)
    assert figures["gpu_hot"] == 84.0
    assert values_of({"ram_total_mb": 1.0})["vram_high"] is None, "no NVML: no figure"
