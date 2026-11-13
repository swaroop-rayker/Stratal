# ADR-0001 — Docker Compose on WSL2, with data on Linux storage

**Status:** Accepted, 2026-11-13. The environment was decided in the input (SPEC §6). The storage location was approved at kickoff (SPEC CL-12).

## Context

- The input fixes the environment: Docker Compose on WSL2, because Celery and Redis need Linux.
- Measured on the laptop on 2026-11-13:
  - WSL 2.6.3 with Ubuntu 24.04. The RTX 4050 is visible inside WSL, and temperature, power draw and utilization all read correctly.
  - Docker Desktop is installed with the WSL2 backend. It was not running.
  - C: has 47 GB free and already holds Docker's 84.6 GB data disk. D: has 288 GB free.
  - The project lives at `D:\Stratal`.
- Linux reaches Windows drives through a file-sharing bridge that is slow for many small files, and whose reads do not show up in Linux disk statistics.

## Decision

1. Use **Docker Desktop** with the WSL2 backend. It is already installed, free for personal use, and needs no account.
2. **Source code** stays at `D:\Stratal` and is bind-mounted into containers during development.
3. **All data Stratal owns** lives on a named Docker volume, `stratal-data`, on Linux storage. Source folders on Windows are mounted read-only and read once, at ingest.
4. **Docker Desktop's disk image moves to D:.** The owner changes this setting; the agent does not change system settings.
5. **Python runs only in containers** (3.12). The Windows host needs Docker and git.
6. Dependency folders (`.venv`, `node_modules`) live in images or volumes, never on the bind mount.

## Alternatives

| Option | Why not |
|---|---|
| Docker Engine installed inside the Ubuntu distro, no Docker Desktop | Uses less memory, but needs manual NVIDIA Container Toolkit setup. Worth revisiting if memory proves too tight |
| Keep the repository inside the WSL filesystem | Fastest bind mounts, but the owner's tools work on `D:\Stratal` |
| Run natively on Windows | Celery has no official Windows support |
| Bind-mount data from D: | Easy to browse in Explorer, but slow, and unsuitable for Postgres |

## Consequences

- Data-heavy stages run at Linux-native speed, and C: stops filling up.
- Stratal's data is not directly browsable in Windows Explorer. It is reached through the dashboard, the API or the CLI.
- Hot reload across the bind mount needs polling-based file watching.
- The Docker disk image grows as data is written and does not shrink by itself when data is deleted (risk R-05).
- Verified by spikes S-01 (GPU inside a container) and S-04 (I/O speed).
