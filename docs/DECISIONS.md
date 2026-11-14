# Stratal — Decisions Log

Every component and every non-trivial decision gets three lines: **What** was decided, **How** it is done, **Why**. Major decisions also have an ADR with the alternatives; this log links to it.

Newest entries go at the bottom of their section. All entries below date from the kickoff, 2026-11-13.

## Environment

**Runtime environment** — [ADR-0001](decisions/ADR-0001-docker-compose-on-wsl2.md)
- **What:** Everything runs in Docker Compose on WSL2, using the Docker Desktop that is already installed.
- **How:** Code is bind-mounted from `D:\Stratal`; data lives on a Linux-side volume; Docker's disk image is moved to D:.
- **Why:** Celery and Redis need Linux, Windows-mounted folders are slow from Linux, and C: is nearly full.

**Python version**
- **What:** Python 3.12, in containers only.
- **How:** uv manages the environment and the lockfile; the host's Python is not used.
- **Why:** The input allows 3.11 or 3.12; 3.12 is the newer of the two and every listed library supports it.

**Memory budget** — [ADR-0007](decisions/ADR-0007-memory-budget.md)
- **What:** The WSL VM is capped at 10 GB and all Stratal containers together stay under 9 GB.
- **How:** `.wslconfig` sets the cap; each container has a memory limit; the limits sum to 9 GB.
- **Why:** The laptop has 15.2 GB usable and Windows needs a share. The input's 12 GB was not attainable.

**Docker disk location** (M0) — [ADR-0001](decisions/ADR-0001-docker-compose-on-wsl2.md)
- **What:** Docker's disk image lives at `D:\Docker\wsl`.
- **How:** The image was copied with Docker stopped, then `CustomWslDistroDir` was set in Docker Desktop's settings file. The original on C: was left in place until the owner confirms deleting it.
- **Why:** C: was nearly full. Copy-then-switch means a wrong setting cannot lose the owner's other Docker data.

**Trusting the host's TLS inspection** (M0) — [ERRORS.md](ERRORS.md) E-002
- **What:** Images trust any extra root certificate placed in `docker/certs/`.
- **How:** The Dockerfile copies that folder into the system trust store and points uv, Python and Node at it. The files are ignored by git.
- **Why:** Norton Antivirus re-signs HTTPS on the laptop, and containers rejected it. Trusting the same root Windows already trusts keeps verification on; disabling verification was not acceptable.

**All tooling runs in containers** (M0)
- **What:** Lint, type checks and tests run in a `dev` image; nothing Python is installed on Windows.
- **How:** `scripts/check.ps1` calls `docker compose run dev …` for each gate; the repository is bind-mounted, so no rebuild is needed for source changes.
- **Why:** One environment, identical to what runs the code, and the host's Python 3.14 is too new for the stack.

**Test client dependency** (M0) — [ERRORS.md](ERRORS.md) E-004
- **What:** `httpx2` is the dev dependency for FastAPI's test client, not `httpx`.
- **How:** Swapped in `pyproject.toml` and re-locked.
- **Why:** The installed Starlette builds its test client on `httpx2`; with `httpx` the strict type check failed.

## Storage

**System of record** — [ADR-0002](decisions/ADR-0002-postgres-system-of-record.md)
- **What:** PostgreSQL holds all transactional state. SQLite is not used.
- **How:** SQLAlchemy 2.x typed mappings, Alembic migrations, JSONB for configs and metrics.
- **Why:** Several processes write at once, and the input named two different stores for the same data.

**ORM choice**
- **What:** SQLAlchemy 2.x directly, not SQLModel.
- **How:** Typed declarative models for tables; separate Pydantic models for API schemas.
- **Why:** The input offers either. Typed SQLAlchemy checks cleanly under strict typing, and it avoids one more dependency.

**Dataset storage** — [ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)
- **What:** Files are stored once under their SHA-256; a dataset version is an immutable Parquet manifest that points at them.
- **How:** Ingest copies each file into the blob store; every stage reads a version and writes a new one; writes are temp-file-then-rename.
- **Why:** It makes versions cheap (DATA-3.4), deduplicates for free, and removes the ways data went missing in BUG1.

**Rows are marked, never dropped**
- **What:** A manifest keeps every row from its parent, with a status, the deciding stage and a reason.
- **How:** Stages change `status` and add columns; downstream stages process only `accepted` rows.
- **Why:** Any version can report rejections per stage (EVAL-1.1), filtered rows stay inspectable (LABEL-1.4), and splits can use duplicate groups (DATA-4).

**Stage memoization and the derivation cache** — [ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)
- **What:** A stage with the same parent and config reuses its earlier result; per-item results such as captions are cached by content.
- **How:** A lookup on `(stage, parent version, config hash)`; a `derivations` table keyed by content hash and parameter hash, committed per batch.
- **Why:** Crashes resume without redoing work (LABEL-1.2, NFR-5), and changing one setting reruns only what depends on it.

**Content hash function**
- **What:** SHA-256 for file identity.
- **How:** Computed in the same pass that copies the file into the blob store.
- **Why:** "Same hash means same file" must be safe to rely on, and SHA-256 is fast enough at this scale. The input's alternative, xxhash, would add a dependency for speed that is not needed.

**Ingest decodes every image** (M1)
- **What:** Ingest fully decodes each file to decide whether it is corrupt, not just its header.
- **How:** Pillow opens the file, asks for a reduced-size decode where the format allows it (JPEG), and loads it; any decoder error rejects the row with the error text as the reason.
- **Why:** A truncated JPEG has a valid header. Only reading the whole stream catches it (DATA-1.2), and the reduced-size decode keeps that cheap.

**Every file gets a row** (M1)
- **What:** Non-image files, unreadable files and undersized images are rejected with a reason, never skipped.
- **How:** Reason codes `unsupported_format`, `unreadable`, `corrupt_image`, `below_min_size`; "size" for DATA-1.3 is the shorter side.
- **Why:** DATA-1.1 asks for a row per file, and a file that silently vanishes is the kind of loss BUG1 describes.

**The version identity is injected** (M1)
- **What:** The commit step takes the identity function as an argument.
- **How:** Production passes the owner's `derive_version_identity`; tests of the plumbing pass a deliberately wrong stand-in from `stratal.testing.fakes`.
- **Why:** The plumbing can be built and tested before the owner's unit exists, without the agent writing that unit.

**Version identity** (M1, DATA-3.2)
- **What:** `content_hash` from the rows, `config_hash` from the config, and `version_id` = `ds_` + 12 hex characters from both.
- **How:** Each row's non-null columns are encoded in name order with type tags and length prefixes, hashed with SHA-256, and the row digests are added modulo 2^256 together with the row count. The config is hashed as canonical JSON with sorted keys.
- **Why:** Addition makes the result independent of row order and needs only one running total, so any manifest size streams. Skipping nulls means adding an empty column to the schema later does not change existing IDs. Twelve hex characters settle OC-4.
- **Who:** This unit was reserved for the owner (ADR-0012). The owner asked the agent to write it on 2026-11-14.
- **Limit:** Summing digests resists accidents, not a deliberate attacker. Acceptable for a single-user local tool.

**Manifest is written before it is named** (M1)
- **What:** A stage writes its manifest to a temporary file, derives the identity from that file, then renames it to `<version_id>.parquet`.
- **How:** One streaming pass to write, one to hash, one rename. If that version already exists, the temporary file is discarded.
- **Why:** The name depends on the content, and neither pass needs the dataset in memory (NFR-2).

**PyArrow type checks relaxed in one file** (M1)
- **What:** Three "unknown type" checks are off in `stratal/storage/manifest.py` only.
- **How:** A file-level pyright comment; the module's own functions are fully typed and PyArrow objects do not leave it.
- **Why:** PyArrow ships no type information. The alternative, a third-party stubs package, is a dependency no requirement asks for.

## Job runtime

**Job execution** — [ADR-0003](decisions/ADR-0003-job-execution-model.md)
- **What:** The Celery task supervises; each job stage runs in its own child process.
- **How:** Conditional claim in Postgres, spawn in a fresh interpreter, heartbeats, exit status mapped to an outcome.
- **Why:** The input's worker settings never recycled the process, which is how weights stayed in VRAM (BUG5). Process exit is the only unload that cannot fail.

**Same model for CPU jobs**
- **What:** CPU stages also run as supervised child processes.
- **How:** One runtime for both queues.
- **Why:** One code path to test, per-job memory accounting, and cancel works the same everywhere. The cost is a process start per stage.

**Job state** — [ADR-0003](decisions/ADR-0003-job-execution-model.md)
- **What:** Postgres is the truth for job state; Redis only delivers messages and carries fast-path flags.
- **How:** A seven-state machine with a logged transition table and a separate `desired_state` for pending pause or cancel.
- **Why:** Redis can lose or redeliver a message; a job's state must survive both.

**Where the watchdog lives**
- **What:** Dead-job and stalled-job detection run inside the `monitor` service.
- **How:** A loop that compares heartbeats and progress timestamps with their limits and applies the action.
- **Why:** `monitor` already has the freshest view, and a watchdog that depends on a busy worker could be starved. Celery Beat is kept for scheduled maintenance only and is added when first needed (M7).

## Monitoring

**Event pipeline** — [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md) (Proposed)
- **What:** Jobs emit events to a Redis Stream; a collector writes them to Postgres and fans them out live.
- **How:** Non-blocking emitter with a local spool file; idempotent inserts; SSE with replay to the browser.
- **Why:** Events must survive a crash of any one component (MON-1.4), and monitoring must never block or crash a job (NFR-13).

**Telemetry sources** — [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md)
- **What:** System-wide numbers come from a sampler; a job's own RSS and PyTorch allocator figures come from the job.
- **How:** psutil and NVML in `monitor`; a reporter thread in the job's runtime wrapper.
- **Why:** PyTorch's allocator can only be read from inside the process that owns it.

## GPU

**GPU ownership** — [ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)
- **What:** One lease decides who may use the GPU; every GPU process runs under a hard VRAM cap.
- **How:** A PostgreSQL advisory lock held by the user of the GPU; PyTorch's per-process memory fraction.
- **Why:** One 6 GB card shared by training, inference and later Ollama (BUG6), and unknown driver behaviour at the limit under WSL2.

**Sleep handling** — [ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md)
- **What:** A PowerShell script on Windows keeps the laptop awake during jobs and reports sleep and resume.
- **How:** It polls the API, holds a keep-awake request while jobs are active, and posts power events.
- **Why:** Containers cannot see or prevent Windows sleep, and the laptop has only Modern Standby (BUG4).

## Models

**Captioner** — [ADR-0009](decisions/ADR-0009-florence-2-native-transformers.md)
- **What:** Florence-2 through the native transformers implementation, with no remote code.
- **How:** `florence-community/Florence-2-base` at a pinned revision, loaded from safetensors.
- **Why:** `trust_remote_code` runs code from the model repository, which NFR-8 rules out.

**Model downloads**
- **What:** Models are downloaded only by an explicit pull command from an allow-list; jobs run with the hub offline.
- **How:** An allow-list in config with pinned revisions; offline mode set in worker environments.
- **Why:** NFR-7 (works offline after download) and NFR-8 (downloads only from configured sources).

## Interfaces

**Error model** — [ADR-0011](decisions/ADR-0011-error-model.md)
- **What:** Typed errors with stable codes, returned as RFC 9457 problem details.
- **How:** One exception hierarchy, one FastAPI handler, the same codes in job failures and events.
- **Why:** The input left the pattern unfilled, and the UI needs one way to show a reason and a suggested action (MON-1.3).

**API image has no ML libraries**
- **What:** The `api` service does not install or import PyTorch.
- **How:** Separate dependency groups for api, CPU worker and GPU worker.
- **Why:** It keeps the API small in memory and makes it impossible for the API to load a model by accident.

## Process

**Requirement IDs** — [ADR-0010](decisions/ADR-0010-requirement-ids-and-traceability.md)
- **What:** The input's module IDs are the requirement IDs; criteria are `<ID>.AC<n>`.
- **How:** IDs in commit scopes and test names; a meta-test checks done P0s have tests.
- **Why:** The input's own conventions already use them, and NFR-9 needs to be checkable.

**Working model** — [ADR-0012](decisions/ADR-0012-working-model-and-git-workflow.md)
- **What:** The owner implements the core logic; the agent builds the plumbing, supplies stubs and tests, and reviews.
- **How:** Typed stubs with strict expected-failure tests named by requirement ID; fakes so plumbing is never blocked.
- **Why:** SPEC §9.1, and the owner's goal of learning by building.

**Git workflow** — [ADR-0012](decisions/ADR-0012-working-model-and-git-workflow.md)
- **What:** A local repository with one branch per milestone; `main` receives a milestone only with the owner's OK.
- **How:** Conventional commits with requirement IDs; no remote and no push until the owner sets one up.
- **Why:** "One PR per milestone" without yet needing a GitHub account, which the owner asked to be consulted about.

**Verbatim input kept**
- **What:** The owner's original spec is stored unmodified beside SPEC.md.
- **How:** Copied from the session transcript by script, not retyped; a second script compares SPEC.md against it.
- **Why:** "Nothing from my input may be dropped; flag anything you reworded" has to be checkable.
