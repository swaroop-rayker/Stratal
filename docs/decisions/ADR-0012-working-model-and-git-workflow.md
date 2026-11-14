# ADR-0012 — Working model and git workflow

**Status:** Accepted, 2026-11-13 (kickoff decisions 1 and 3).

## Context

- §9.1 of the spec: the human owns core logic (dedupe, versioning, batching/VRAM handling, scoring, evaluation, from-scratch layers). The agent may write plumbing and must explain any code it writes on request.
- The owner is building this to learn as well as to ship.
- The engineering standards ask for conventional commits and one PR per milestone. There is no GitHub remote yet, and creating one needs an account, which the owner asked to be consulted about.

## Decision

### Who writes what

| The owner implements | The agent implements |
|---|---|
| Duplicate detection and grouping (DATA-2) | Ingestion I/O, blob store, manifest reading and writing |
| Version-ID derivation (DATA-3.2) | Database models and migrations |
| Splits (DATA-4), including writing the requirement | Job runtime: supervisor, child, state machine |
| Caption batching and VRAM handling (LABEL-1.1/1.2, RUN-1.4/1.5) | Events, collector, sampler, watchdog |
| CLIP scoring and the filter rule (LABEL-1.3/1.4) | API, configuration, Docker, CI |
| Evaluation statistics (EVAL-1.1, EVAL-1.2) | Review storage and endpoints, report assembly, plots |
| From-scratch layers (BUILD-1.8) | UI wiring, after the design gates |

For each owner-implemented unit the agent provides, ahead of time:

- a typed stub that raises `NotImplementedError`, with a docstring stating the contract;
- failing tests named with the requirement ID, marked as strict expected-failures so the suite stays green and the outstanding work stays visible. When the owner's implementation passes, the marker is removed and the requirement can be ticked;
- a simple fake where other plumbing needs something to call (for example a fake captioner), so work is never blocked on the owner.

The agent reviews what the owner writes and does not rewrite it without being asked.

### Git

- The repository is local. There is no remote and nothing is pushed until the owner sets one up.
- `main` holds reviewed milestones only.
- Each milestone has one branch, e.g. `m0-environment`, `m1-ingest-versioning`.
- The agent commits its own work on the milestone branch, using conventional commits with requirement IDs.
- A milestone branch is merged into `main` only with the owner's OK, after its done-checklist passes. This is the local stand-in for "one PR per milestone".
- `docs/learning/` is ignored by git and never committed.
- The CI workflow file is kept in the repository and starts running when a GitHub remote exists.

## Alternatives

| Option | Why not |
|---|---|
| The agent writes everything; the owner reviews | Faster, but contradicts §9.1 and the owner's learning goal. The owner chose otherwise |
| The owner writes everything | Slow, and plumbing is not where the learning is |
| Commit straight to `main` | Leaves no review point per milestone |

## Consequences

- The schedule depends on the owner's implementation pace. Stubs, tests and fakes are prepared early so the agent always has plumbing to do.
- Each milestone in [MILESTONES.md](../MILESTONES.md) lists its owner-implemented units up front.
- If the owner later prefers to approve each commit, or to move to GitHub pull requests, this ADR is superseded by a new one.
