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

    assert len(requirements) == 120, "111 from the input, DATA-6.1 and DATA-4.1 to DATA-4.8"
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


def test_NFR_9_test_ids_refer_to_real_requirements() -> None:
    requirements = _requirements()
    known = {rid.replace("-", "_").replace(".", "_") for rid in requirements}
    pattern = re.compile(
        r"^test_((?:DATA|LABEL|RUN|BUILD|ML|HPO|REG|EVAL|LLM|IMG|TRANS|UI|MON)_\d+_\d+)_"
    )

    unknown = sorted({m.group(1) for name in _test_names() if (m := pattern.match(name))} - known)

    assert not unknown, f"tests name requirement IDs that are not in SPEC.md: {unknown}"
