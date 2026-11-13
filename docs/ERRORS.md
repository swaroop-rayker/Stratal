# Stratal — Errors and Bugs

A running log of errors and bugs: what was seen, what caused it, how it was fixed and what proves it stays fixed.

## How to add an entry

Add entries to [Log](#log), newest first, numbered `E-nnn`.

```markdown
### E-nnn — Short title
- **Date:**
- **Seen:** what happened, with the exact error text
- **Where:** milestone, component, requirement ID
- **Cause:**
- **Fix:** what changed, with the commit
- **Guard:** the test that now fails if it comes back
- **Status:** open | fixed | verified
```

An entry is `fixed` when the change is in, and `verified` only when its guard test passes.

## Inherited bugs

These seven come from the owner's input (SPEC §8.2). They describe failures the design must prevent. Nothing is built yet, so none can be verified: each is **prevention designed, not yet verified** until its guard test passes.

| Bug | As written in the input | Design answer | Guard test | Verified in |
|---|---|---|---|---|
| BUG1 | Data after preprocessing and cleaning not being stored, handled and retrieved properly | Immutable manifests, atomic writes, one read path ([ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)) | `test_BUG1_*` | M3 |
| BUG2 | OOM error due to which program crashes | Own process per job, VRAM cap, supervisor reports kernel kills ([ADR-0003](decisions/ADR-0003-job-execution-model.md), [ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG2_*` | M2, M4 |
| BUG3 | RAM contention during data loading, data processing phases | Streaming, bounded queues, per-container limits ([ADR-0007](decisions/ADR-0007-memory-budget.md)) | `test_NFR_2_*` | M7 |
| BUG4 | Training interrupted when laptop goes into sleep mode and improper handling of this situation | Keep-awake, sleep events, per-batch commits, resume in a fresh process ([ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md)) | `test_BUG4_*` | M2 |
| BUG5 | Stale weights persist in the VRAM after training completion, tests, evals, model transfers or changes during inferencing | Process exit is the unload; verified unload for long-lived processes ([ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG5_*` | M4 |
| BUG6 | Improper handling of VRAM occupancy, bloated by stale weights | One GPU lease; free-VRAM check before every load ([ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | `test_BUG6_*` | M4 |
| BUG7 | Data bloat during model training runs, pauses or checkpoints and poor versioning issues | Retention policy (REG-2), one rolling checkpoint, content-addressed files | NFR-14 disk audit | Phase 2 |

## Log

### E-001 — The specified GPU worker settings would never release VRAM

- **Date:** 2026-11-13
- **Seen:** Found in review, before any code. The input configured the GPU queue as `--pool=solo, concurrency=1, worker_max_tasks_per_child=1` and expected a fresh process per job.
- **Where:** kickoff review; SPEC §7.2 RUN; relates to BUG5 and BUG6
- **Cause:** The solo pool runs tasks inside the worker's own process. `max_tasks_per_child` only recycles prefork pool children, and Celery ignores it in other pools without warning. Every job would have shared one process, so weights from one could stay in VRAM for the next.
- **Fix:** The task became a supervisor that starts one child process per job (SPEC CL-01, [ADR-0003](decisions/ADR-0003-job-execution-model.md)).
- **Guard:** `test_BUG5_*` in M4
- **Status:** fixed in design; not verified until the guard test exists and passes
