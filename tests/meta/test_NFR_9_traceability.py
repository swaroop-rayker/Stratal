"""NFR-9: every P0 requirement has at least one automated test named with its ID.

Two checks (ADR-0010):
1. SPEC.md still has the line format this test (and people) rely on.
2. Every item ticked in MILESTONES.md that names a ``test_<ID>_*`` pattern has
   at least one real test function with that prefix. A requirement cannot be
   marked done without its test.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "SPEC.md"
MILESTONES = ROOT / "docs" / "MILESTONES.md"
TESTS = ROOT / "tests"

REQUIREMENT = re.compile(
    r"^- \*\*(?P<id>[A-Z]+-\d+\.\d+)\*\* `\[(?P<priority>P\d)\]\[(?P<phase>\d)\]`", re.M
)
TICKED_TEST = re.compile(r"^- \[x\] .*?`(?P<prefix>test_[A-Za-z0-9_]+?)_\*`", re.M)
TEST_FUNCTION = re.compile(r"^\s*def (test_\w+)\(", re.M)
FRONTEND = ROOT / "frontend" / "src"
FRONTEND_TEST = re.compile(r"""\btest\(\s*["'](test_\w+)["']""")


def _requirements() -> dict[str, tuple[str, str]]:
    text = SPEC.read_text(encoding="utf-8")
    return {m["id"]: (m["priority"], m["phase"]) for m in REQUIREMENT.finditer(text)}


def _test_names() -> set[str]:
    names: set[str] = set()
    for path in TESTS.rglob("test_*.py"):
        names.update(TEST_FUNCTION.findall(path.read_text(encoding="utf-8")))
    # Vitest tests (M6 on) are named the same way: test("test_MON_4_1_...", ...).
    for path in FRONTEND.rglob("*.test.ts*"):
        names.update(FRONTEND_TEST.findall(path.read_text(encoding="utf-8")))
    return names


def test_NFR_9_spec_requirements_are_machine_readable() -> None:
    requirements = _requirements()

    assert len(requirements) == 127, "111 from the input, DATA-6.1, DATA-4.1 to DATA-4.8, DATA-1.5, RUN-1.10, DATA-7.1 to DATA-7.5"
    assert requirements["DATA-4.1"] == ("P0", "1")
    assert requirements["DATA-4.8"] == ("P2", "1")
    assert requirements["DATA-1.1"] == ("P0", "1")
    assert requirements["REG-2.11"] == ("P1", "2")
    p0 = [rid for rid, (priority, _) in requirements.items() if priority == "P0"]
    assert len(p0) > 50


def test_NFR_9_ticked_milestone_items_have_named_tests() -> None:
    ticked = [m["prefix"] for m in TICKED_TEST.finditer(MILESTONES.read_text(encoding="utf-8"))]
    names = _test_names()

    missing = [
        prefix for prefix in ticked if not any(name.startswith(f"{prefix}_") for name in names)
    ]

    assert not missing, f"ticked in MILESTONES.md but no such test exists: {missing}"


def test_NFR_9_every_phase_1_p0_requirement_has_a_named_test() -> None:
    """M7's acceptance check: no phase-1 P0 requirement without a ``test_<ID>_*`` test.

    Whether those tests pass is checked by running the suite; this checks they exist.
    """
    requirements = _requirements()
    names = _test_names()
    phase_1_p0 = sorted(
        r for r, (priority, phase) in requirements.items() if (priority, phase) == ("P0", "1")
    )

    untested = [
        rid
        for rid in phase_1_p0
        if not any(
            name.startswith(f"test_{rid.replace('-', '_').replace('.', '_')}_") for name in names
        )
    ]

    assert len(phase_1_p0) == 31
    assert not untested, f"phase-1 P0 requirements with no named test: {untested}"


def test_NFR_9_test_ids_refer_to_real_requirements() -> None:
    requirements = _requirements()
    known = {rid.replace("-", "_").replace(".", "_") for rid in requirements}
    pattern = re.compile(
        r"^test_((?:DATA|LABEL|RUN|BUILD|ML|HPO|REG|EVAL|LLM|IMG|TRANS|UI|MON)_\d+_\d+)_"
    )

    unknown = sorted({m.group(1) for name in _test_names() if (m := pattern.match(name))} - known)

    assert not unknown, f"tests name requirement IDs that are not in SPEC.md: {unknown}"
