# ADR-0007 — Memory budget

**Status:** Accepted, 2026-11-13 (SPEC CL-03).

## Context

- NFR-2 in the input: RAM peak under 12 GB.
- Measured on 2026-11-13: the laptop has 15.2 GB usable. With the owner's usual applications open, 3.3 GB was free. With no `.wslconfig`, the WSL VM sees 7.4 GB and 2 GB of swap.
- Everything Stratal runs is inside that VM. Twelve gigabytes there would leave about 3 GB for Windows, the browser showing the dashboard and Docker Desktop itself.
- BUG3: RAM contention during data loading and processing. BUG2: out-of-memory crashes.

## Decision

1. **Cap the WSL VM at 10 GB**, with 4 GB of swap, in `%USERPROFILE%\.wslconfig`. The owner creates this file; the agent does not change system settings.

   ```ini
   [wsl2]
   memory=10GB
   swap=4GB
   ```

2. **Restate NFR-2** as: peak under 9 GB across all Stratal containers.
3. **Enforce it with per-container memory limits** that sum to 9 GB. Starting values are in [ARCHITECTURE.md §2](../ARCHITECTURE.md#2-components).
4. **Budget inside jobs from the limit.** Loader worker counts and queue sizes are derived from the container's memory limit, not from CPU count.
5. **Label honestly.** The dashboard's RAM figures are the VM's, and say so.

## Alternatives

| Option | Why not |
|---|---|
| Keep 12 GB | Windows would be left with about 3 GB and would swap, slowing everything including the jobs |
| Leave the 7.4 GB default | Enough for phase 1, too tight for loading Stable Diffusion in phase 4 |
| No per-container limits | One runaway job could take the whole VM, and the kernel might kill Postgres instead of the job |

## Consequences

- Heavy jobs need other Windows applications closed. The limit only caps Stratal; it does not reserve memory for it.
- A job that exceeds its container limit is killed by the kernel. The supervisor reports this as `host_oom_killed` with the peak reached ([ADR-0003](ADR-0003-job-execution-model.md)).
- Phase 4 will probably rebalance the limits toward the GPU worker.
- Swap is a safety net for short peaks. A job that lives in swap is a bug.
- The numbers are confirmed or changed by spike S-07.
