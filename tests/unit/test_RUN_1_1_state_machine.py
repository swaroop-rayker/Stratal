"""RUN-1.1 (phase-1 slice): the allowed job state transitions (ARCHITECTURE.md §4.2)."""

import pytest

from stratal.core.errors import InvalidStateTransition
from stratal.db.models import JOB_STATES
from stratal.runtime.states import TRANSITIONS, can_transition, check_transition

# The diagram in ARCHITECTURE.md §4.2, written out a second time on purpose.
ALLOWED = {
    ("queued", "running"),
    ("queued", "cancelled"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "cancelled"),
    ("running", "paused"),
    ("running", "stalled"),
    ("running", "queued"),
    ("paused", "queued"),
    ("paused", "cancelled"),
    ("stalled", "running"),
    ("stalled", "failed"),
    ("stalled", "cancelled"),
    ("stalled", "queued"),
    ("failed", "queued"),
}


def test_RUN_1_1_exactly_the_drawn_transitions_are_allowed() -> None:
    assert set(TRANSITIONS) == set(JOB_STATES)
    for source in JOB_STATES:
        for target in JOB_STATES:
            assert can_transition(source, target) == ((source, target) in ALLOWED), (source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("completed", "running"),
        ("cancelled", "queued"),
        ("queued", "completed"),
        ("failed", "running"),
    ],
)
def test_RUN_1_1_invalid_transitions_are_rejected_with_a_typed_error(
    source: str, target: str
) -> None:
    with pytest.raises(InvalidStateTransition) as raised:
        check_transition("job_0142", source, target)

    assert raised.value.code == "invalid_state_transition"
    assert "job_0142" in raised.value.detail
    assert raised.value.suggested_action


def test_RUN_1_1_unknown_states_allow_nothing() -> None:
    assert not can_transition("nonsense", "running")
    assert not can_transition("running", "nonsense")
