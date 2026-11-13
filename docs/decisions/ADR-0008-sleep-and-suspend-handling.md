# ADR-0008 — Sleep and suspend handling

**Status:** Accepted, 2026-11-13 (SPEC CL-07; kickoff answer to BUG4). What actually happens to a GPU job across sleep on this laptop is unverified until spike S-03.

## Context

- BUG4: training was interrupted when the laptop slept, and the situation was handled badly.
- The laptop supports only Modern Standby (S0 Low Power Idle) and Hibernate. There is no classic S3 sleep.
- Sleep suspends the whole WSL VM. Nothing inside it runs, and nothing inside it is told.
- There are reports, not confirmed for this machine, that a CUDA context under WSL2 can be unusable after resume until WSL is restarted.
- Containers cannot call Windows power APIs.

## Decision

Four layers, because no single one is sufficient.

1. **Prevent.** A small PowerShell script on the Windows host polls the API. While jobs are active it holds a Windows keep-awake request (`SetThreadExecutionState`), and releases it when they finish. It uses only what ships with Windows.
2. **Observe.** The same script reports suspend and resume to the API. They become events, so a gap on a job's timeline is explained.
3. **Recover.** After a resume the job's next GPU operation either works or raises. If it raises, the child reports `interrupted` and exits. The job is requeued and continues from its last committed work in a fresh process. If a fresh process cannot reach the GPU either, GPU jobs are held and an ERROR event tells the user to restart WSL.
4. **Limit the loss.** Pipeline stages commit per batch. Training (phase 2) checkpoints by elapsed time as well as by step count, so NFR-5's bound holds however slow a step is.

## Alternatives

| Option | Why not |
|---|---|
| Ask the owner to change Windows power settings | Easy to forget, applies even when no job is running, and the agent must not change system settings itself |
| Checkpoints only, no keep-awake | Recovers, but loses work on every idle timeout that could have been avoided |
| A Windows service | Heavier to install and maintain than a script started with the stack |

## Consequences

- One component runs outside Docker. It is started and stopped together with the stack.
- Keep-awake stops idle sleep. It cannot stop a lid-close or power-button sleep, and Windows may still suspend on battery. Recovery is therefore the part that must always work.
- A real sleep test is part of M0, and a freeze/unfreeze test (pausing the worker container) is part of the test suite.
