# ADR-0010 — Requirement IDs and traceability

**Status:** Accepted, 2026-11-13 (SPEC CL-09).

## Context

- The input's documentation list asks for "F-IDs and AC-IDs (F1.AC1...)".
- The same input already gives every requirement a module ID such as `DATA-2.2`, and its conventions use those IDs in commit messages and test names.
- NFR-9: every P0 requirement has at least one automated test named with its ID.
- §9.1: a requirement is never marked done without a passing `test_<ID>_*`.

## Decision

1. **The module IDs are the F-IDs.** No second numbering is introduced.
2. **Acceptance criteria** are `<ID>.AC<n>`, for example `DATA-2.2.AC1`.
3. **Commits** use conventional-commit form with the ID as scope: `feat(DATA-2.2): pHash near-dup removal`. Work that serves no single requirement uses a plain scope, e.g. `chore(m0): …` or `docs: …`.
4. **Tests** are named `test_<ID with underscores>_<what>`, e.g. `test_DATA_2_2_near_duplicates`. Regression tests for the known bugs are named `test_BUG4_…`.
5. **SPEC.md keeps a fixed line format** for requirements, so a script can list every ID with its priority and phase.
6. **A meta-test** reads SPEC.md and the list of requirements marked done in MILESTONES.md, and fails if a done P0 has no matching test.
7. **Status lives in one place:** the checklists in [MILESTONES.md](../MILESTONES.md).

## Alternatives

| Option | Why not |
|---|---|
| Renumber everything F1…F111 | Breaks the input's own commit and test conventions and loses the module meaning |
| A separate machine-readable requirements file as the source of truth | Two sources that can disagree |

## Consequences

- One ID links a requirement, its criteria, its commits and its tests.
- SPEC.md's formatting becomes part of the contract. Changing it means updating the meta-test.
- Requirements added later (such as DATA-6.1) follow the same scheme and are flagged in SPEC.md's change log.
