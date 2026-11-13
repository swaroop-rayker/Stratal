# ADR-0003 — A supervisor task and one child process per job

**Status:** Accepted, 2026-11-13 (SPEC CL-01). Details to be validated in M2.

## Context

The input configures the GPU queue as `--pool=solo, concurrency=1, worker_max_tasks_per_child=1` and expects "fresh process per job, VRAM fully released". That does not happen:

- The solo pool runs each task inside the worker's own long-lived process.
- `max_tasks_per_child` recycles pool child processes. The solo pool has none, and Celery ignores the setting without warning.

So weights from one job stay in the same process as the next. That is BUG5 and BUG6 by construction.

Three more problems need the same answer:

- A crash in native code, or the kernel killing a job for using too much RAM, must not take the worker down, and must produce a readable reason (BUG2).
- With Redis as broker, a task that is not acknowledged within the visibility timeout is delivered again. A long training job could be started twice.
- Hard cancel has to work on a job that is stuck.

## Decision

1. **Workers.** The `gpu` queue uses the prefork pool with concurrency 1. The `cpu` queue uses prefork with a small concurrency.
2. **The Celery task is a supervisor.** It claims the work with a conditional update in Postgres, starts a **child process** in a fresh interpreter and its own process group, sends heartbeats, enforces cancel, turns the child's exit into an outcome, and enqueues the next stage.
3. **The child does the work** and exits. The operating system then reclaims all of its RAM and VRAM. Because the child is started fresh, no process with an initialized CUDA context is ever forked.
4. **Postgres is the truth for job state.** Tasks are acknowledged on receipt. A duplicate delivery fails the claim and exits. A lost job is recovered by the watchdog from heartbeats.
5. **Stopping.** Pause and cancel are cooperative: a flag checked at safe points. Hard cancel is the supervisor killing the child's process group. `revoke()` is not relied on.
6. **A job is a pipeline run with stages.** Each stage is its own child process on the queue that suits it.

## Alternatives

| Option | Why not |
|---|---|
| Keep the solo pool | Never recycles the process |
| Prefork with `max_tasks_per_child=1` and no supervisor | Does give a fresh process per task. But a kernel kill surfaces as a generic lost-worker error, and hard cancel depends on `revoke(terminate=True)` |
| Replace Celery with a Postgres-backed queue | Fewer parts, but the input chose Celery deliberately (§7.7) |
| Threads inside one worker | Cannot free VRAM and cannot be killed |

## Consequences

- VRAM and RAM are fully released at the end of every job, with nothing to trust.
- A job crash is contained and explained.
- CPU and GPU jobs work the same way.
- Each stage pays a process start and a model load. That is negligible for long jobs. Classical-ML HPO trials, which can be sub-second, will be grouped in phase 2 instead of paying it per trial.
- Celery's own retry and workflow features are not used for job logic; the state machine in Postgres does that.
