# Stratal — Kickoff

| | |
|---|---|
| Approved | 2026-11-13, by the owner ("Approved") |
| What was approved | The kickoff review: nine spec changes, five smaller items, the M0–M7 plan, and the three defaults below |
| Current milestone | M0 — Environment proof, skeleton, docs |
| Branch | `m0-environment` |

This file starts the implementation. It records what was agreed, where things stand, and who does what next.

## What was agreed

**Spec changes.** All are flagged in [SPEC.md](SPEC.md) and listed in its [change log](SPEC.md#appendix-a--change-log-v01--v02).

| # | Change | Recorded as |
|---|---|---|
| 1 | Each job runs in its own child process; the Celery task only supervises | CL-01, [ADR-0003](decisions/ADR-0003-job-execution-model.md) |
| 2 | PostgreSQL is the system of record; no SQLite | CL-02, [ADR-0002](decisions/ADR-0002-postgres-system-of-record.md) |
| 3 | WSL capped at 10 GB; NFR-2 becomes "under 9 GB across all containers" | CL-03, [ADR-0007](decisions/ADR-0007-memory-budget.md) |
| 4 | A thin job runtime and the app shell are pulled into phase 1 | CL-04 |
| 5 | New requirement DATA-6.1, preprocessing | CL-05 |
| 6 | `stalled` is the seventh job state | CL-06 |
| 7 | Services added: `monitor`, later `inference` and `ollama`; a Windows host script for sleep | CL-07, [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md), [ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md) |
| 8 | Florence-2 through the native implementation, no `trust_remote_code` | CL-08, [ADR-0009](decisions/ADR-0009-florence-2-native-transformers.md) |
| 9 | The input's module IDs are the requirement IDs; criteria are `DATA-2.2.AC1` | CL-09, [ADR-0010](decisions/ADR-0010-requirement-ids-and-traceability.md) |

Smaller items: the error shape (CL-10), where allocator stats come from (CL-11), data on a Linux volume with copy-at-ingest (CL-12), in-house CIDEr-D (CL-13), and the SPEC.md tidy-up (CL-14 to CL-16).

**Defaults accepted with the approval.**

1. **The owner implements the core logic**: dedupe, version-ID hashing, splits, batching and VRAM handling, CLIP scoring, evaluation statistics, and later the from-scratch layers. The agent builds everything around them, supplies typed stubs with failing ID-named tests, and reviews. Details in [ADR-0012](decisions/ADR-0012-working-model-and-git-workflow.md).
2. **DATA-4 stays the owner's exercise.** It is needed by M5.
3. **Git is local**, with one branch per milestone. No GitHub remote until the owner wants one.

## Where things stand

- The repository exists at `D:\Stratal`, on branch `m0-environment`. `main` has only the initial commit.
- The documentation set is written. No code exists.
- Verified on the laptop: GPU passthrough into WSL2 works, and GPU temperature, power and utilization are readable there.
- Not yet verified: anything inside a Docker container. Docker Desktop was not running, and the check needs downloads.

## Update, 2026-11-13 (later the same day)

The owner told the agent to do the four setup steps itself and approved the downloads. All four are done, and so is most of M0: the skeleton runs, and spikes S-01, S-02, S-04 and S-05 have results in [RISKS.md](RISKS.md). The steps below are kept as a record of what was done. What is still open for the owner:

- **Spike S-03 (sleep).** Start the probe, put the laptop to sleep, wake it, and report what it printed.
- **Confirm deleting the old Docker disk image on C:** (84.6 GB at `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`). Docker now runs from the copy on D:.
- **Norton's HTTPS scanning.** Containers now trust Norton's root certificate so installs work. Whether to exclude developer sites from that scanning instead is the owner's choice.
- The OK to merge M0 into `main`.

## Update, 2026-11-14

M0 is complete and merged into `main`. The sleep spike ran (a GPU job survived a 705-second sleep; see [RISKS.md](RISKS.md) S-03), the old Docker image on C: was deleted, and the D: image was compacted. Still open from the list above: the owner's choice about Norton's HTTPS scanning. Next are M1 and Gate 0.

## What the owner does next

These four steps unblock M0. They were originally reserved for the owner because they change system settings or need a download approval.

**1. Create the WSL memory config.** Make the file `C:\Users\<your user>\.wslconfig` with:

```ini
[wsl2]
memory=10GB
swap=4GB
```

Then quit Docker Desktop if it is open, and run this in PowerShell. It stops everything running in WSL, including any other containers:

```bash
wsl --shutdown
```

**2. Start Docker Desktop** and wait until it reports the engine running.

**3. Move Docker's disk image to D:.** In Docker Desktop: Settings → Resources → Advanced → Disk image location (the wording can differ between versions). Choose a folder such as `D:\Docker` and apply. It moves the existing 84.6 GB image, which takes a while. That image holds data from your other Docker work; nothing in it is deleted, and whether to prune it is your call.

**4. Approve the M0 downloads.** About 3.5 GB to download and about 10 GB on disk once unpacked, all into Docker's disk image:

| What | From | Approx. download |
|---|---|---|
| Base images: Python 3.12 slim, PostgreSQL, Redis, Ubuntu 24.04 (for the GPU check) | Docker Hub | 300 MB |
| `uv` | Astral's official release | 20 MB |
| PyTorch with its CUDA libraries | PyPI | 2.5–3 GB |
| FastAPI, SQLAlchemy, Celery, structlog and the test and lint tools | PyPI | 200 MB |

None of these needs an account, email or key. Model weights and the dataset are asked about separately in M4, and frontend packages in M6.

**Also yours, not blocking M0:**

| Item | Needed by | Notes |
|---|---|---|
| Review ADR-0005 and open clarifications OC-1, OC-2 | before M2 | Done 2026-11-14: all three accepted as proposed |
| Settle OC-4 (version ID length) | in M1 | Part of your DATA-3.2 implementation |
| Settle OC-3 (preprocess size) | before M3 | Proposed 768 px |
| Choose the phase-1 dataset | before M4 | **Done 2026-11-15:** a 10k subset of COCO 2017 |
| Write DATA-4 | before M5 | **Draft v1 by the agent at the owner's request, 2026-11-15, in review:** [drafts/DATA-4_v1.md](drafts/DATA-4_v1.md) |
| Review the design system (Gate 0) | when delivered, alongside M1 | Only an explicit "approved" passes |
| GitHub remote | whenever you want CI and PRs | **Done 2026-11-15:** `origin` is the owner's repository; `main` and tags pushed |
| Translation language pairs | phase 5 | |

## What the agent does next

In this order, once the four steps above are done:

1. Spike S-01: GPU inside a container. Nothing else starts until this passes.
2. Spikes S-02, S-04, S-05. Spike S-03 (sleep) needs you at the laptop.
3. The project skeleton, per the M0 checklist in [MILESTONES.md](MILESTONES.md#m0--environment-proof-skeleton-docs).
4. Record spike results in [RISKS.md](RISKS.md), update ADRs they affect, and ask for your OK to merge M0.
5. Start Gate 0 (design system) and M1.

## DATA-4 brief

Write the section in the same form as the rest of [SPEC.md](SPEC.md): requirement lines with IDs `DATA-4.1`, `DATA-4.2`, …, a priority and phase tag, and at least one testable acceptance criterion each. The input asks for stratification, reproducibility and leakage prevention. The review will check that the section answers:

1. What is stratified on when image–caption rows have no class label?
2. How do images that are near or semantic duplicates of each other stay on one side of the split? The cleaning stage will record a duplicate-group ID on every row so you can use it.
3. If 500 images are added in a later version, do existing rows keep their split?
4. Where does the split sit relative to the CLIP filter and the manual review?

Send the draft in chat. It goes into SPEC.md only after you approve the reviewed version.

## Document map

| File | What it is |
|---|---|
| [SPEC.md](SPEC.md) | What to build. Source of truth for requirements |
| [SPEC-INPUT-v0.1.txt](SPEC-INPUT-v0.1.txt) | Your original input, byte-for-byte |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data flow, cross-cutting concerns |
| [DATA_MODEL.md](DATA_MODEL.md) | Schema, manifests, invariants |
| [API.md](API.md) | HTTP interface, error shape, internal contracts |
| [decisions/](decisions/) | ADR-0001 to ADR-0012 |
| [DECISIONS.md](DECISIONS.md) | Three lines per component: What / How / Why |
| [MILESTONES.md](MILESTONES.md) | M0–M15 with checklists; the place to see status |
| [RISKS.md](RISKS.md) | Risks and spike plans |
| [ERRORS.md](ERRORS.md) | Bugs seen and fixed |
| [DESIGN-SYSTEM.md](DESIGN-SYSTEM.md) | Gate 0 deliverable. v1 approved 2026-11-15 |
| [SCREENS.md](SCREENS.md) | Gate 1 deliverable. v1 written 2026-11-15, awaiting review |
| `learning/` | Your local notes. Ignored by git, never committed |
| [../CLAUDE.md](../CLAUDE.md) | Instructions coding agents load automatically |

**Names that differ from the input's list.** The screen inventory will be `docs/SCREENS.md` (the input offers `SCREEN.md` or `PAGES.md`, and §9.4 says `docs/design/screens.md`). The second learning file is `docs/learning/sys-design.md` (the input has `docks/learning/sys design.md`).

## Picking this up in a new session

Open a session in `D:\Stratal`. `CLAUDE.md` loads by itself. The current state is the checklist of the in-progress milestone in [MILESTONES.md](MILESTONES.md).
