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

- The remote is `origin`, https://github.com/swaroop-rayker/Stratal.git, set up by the owner on 2026-11-15. Only `main` and the milestone tags are pushed, after the owner's OK to merge. Milestone branches stay local unless the owner asks. Never force-push.
- `main` holds reviewed milestones only.
- Each milestone has one branch, e.g. `m0-environment`, `m1-ingest-versioning`.
- The agent commits its own work on the milestone branch, using conventional commits with requirement IDs.
- A milestone branch is merged into `main` only with the owner's OK, after its done-checklist passes. This is the local stand-in for "one PR per milestone".
- `docs/learning/` is ignored by git and never committed.
- The CI workflow (`.github/workflows/ci.yml`) runs on every push to `main` and on pull requests.

## Exceptions made by the owner

The split above is the default. The owner can hand any unit to the agent, and each time that happens it is recorded here.

| Date | Unit | What happened |
|---|---|---|
| 2026-11-14 | Version-ID derivation (DATA-3.2) | After the stub and tests were delivered, the owner asked the agent to implement it. The agent did, and explained the algorithm in `docs/learning/`. |
| 2026-11-15 | Exact and near duplicate detection (DATA-2.1, DATA-2.2) | After the stubs, strict expected-failure tests and fakes were delivered, the owner asked the agent to write them ("you write it"). The agent did, made the open design choices itself and recorded them in DECISIONS.md, and explained the algorithms in `docs/learning/`. The near-duplicate threshold stays the owner's (OC-5). |
| 2026-11-15 | Caption batching loop (LABEL-1.1, LABEL-1.2), VRAM manager v1 (RUN-1.4, RUN-1.5), CLIP similarity (LABEL-1.3), filter rule (LABEL-1.4) | After the stubs, strict expected-failure tests and fakes were delivered, the owner asked the agent to write all four ("you write the 4 units"). The agent did, recorded the open design choices in DECISIONS.md, and explained them in `docs/learning/`. The CLIP threshold stays the owner's (OC-5). |
| 2026-11-19 | From-scratch attention, positional encoding and LSTM cell (BUILD-1.8) | At M9's kickoff the owner chose "You write them". The agent delivers the stubs, contracts and strict expected-failure tests first, as for any owner unit, then writes them and explains them in `docs/learning/`. |
| 2026-11-19 | VRAM manager v2 for training (RUN-1.4: `TrainingState.close`) and training out-of-memory handling (RUN-1.5: `suggest_batch_size`, `training_oom_error`) | After the stubs, contracts and strict expected-failure tests were delivered in M8, the owner asked the agent to write them ("write RUN-1.4 and RUN-1.5 yourself"). The agent wrote them to the contracts unchanged, added the missing GPU test for RUN-1.5.AC1, and explained them in `docs/learning/`. |
| 2026-11-15 | The DATA-4 requirements section (train/val/test splits) | The owner asked the agent to draft it ("You draft it"). The draft is a proposal in `docs/drafts/`; the owner approved v1 the same day and it moved into SPEC.md. Its split-assignment and leakage-grouping units stay the owner's unless the owner says otherwise. |

The remaining owner units are unchanged unless the owner says otherwise.

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
