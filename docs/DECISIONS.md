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

**Event pipeline** — [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md) (accepted 2026-11-14)
- **What:** Jobs emit events to a Redis Stream; a collector writes them to Postgres and fans them out live.
- **How:** Non-blocking emitter with a local spool file; idempotent inserts; SSE with replay to the browser.
- **Why:** Events must survive a crash of any one component (MON-1.4), and monitoring must never block or crash a job (NFR-13).

**Telemetry sources** — [ADR-0005](decisions/ADR-0005-event-and-telemetry-pipeline.md)
- **What:** System-wide numbers come from a sampler; a job's own RSS and PyTorch allocator figures come from the job.
- **How:** psutil and NVML in `monitor`; a reporter thread in the job's runtime wrapper.
- **Why:** PyTorch's allocator can only be read from inside the process that owns it.

## M2: job and event spine

**State machine** (M2, RUN-1.1)
- **What:** One function, `transition`, is the only code that changes `jobs.state`.
- **How:** It checks the move against a table of allowed transitions, then writes the `job_transitions` row and a `job_state` event in the caller's transaction. A check constraint rejects unknown states.
- **Why:** A state can then never change without both records, and the timeline can be rebuilt from events alone (MON-1.1).

**Claiming a stage** (M2, ADR-0003)
- **What:** A supervisor takes a stage with a conditional update under a row lock; the job's `attempt` counts claims.
- **How:** A stage is taken only if its status is `queued`. Heartbeats and the final write carry the attempt number and do nothing if it no longer matches.
- **Why:** A task delivered twice starts the stage once, and a supervisor that was frozen and then replaced finds out and stands down instead of writing over its successor.

**Exit status as a report** (M2, BUG2)
- **What:** The supervisor turns the child's exit status into a typed error when the child could not report one.
- **How:** A negative status is a signal. SIGKILL together with a rise in the container's cgroup `oom_kill` counter is reported as `host_oom_killed` with the peak memory; any other signal as `stage_failed` naming the signal.
- **Why:** A kernel kill cannot be caught inside the process, so only the parent can say what happened.

**Stream entries carry a kind** (M2, ADR-0005)
- **What:** The Redis stream carries events and the job's own memory samples.
- **How:** Each entry has a `kind` (`event` or `process_sample`) and a JSON payload; the collector routes by kind.
- **Why:** The job child then needs no database writes of its own for monitoring, so a database or monitor problem cannot slow or stop it (NFR-13).

**SSE reads PostgreSQL; Redis only rings the bell** (M2, MON-1.4, OC-2)
- **What:** A live stream sends events by querying PostgreSQL for rows after the client's cursor.
- **How:** It subscribes to the job's pub/sub channel first, then queries; every later message on the channel triggers the same query. Samples are the exception and are forwarded directly.
- **Why:** Replay and live are one code path, so a reconnecting client cannot miss an event or see one twice, and order is always the stored order.

**ETA** (M2, MON-1.5)
- **What:** Throughput over the last 20 progress reports, and the remaining items divided by it.
- **How:** Computed in the job on the monotonic clock and sent inside the progress event's metrics (`items_per_s`, `eta_s`); the collector copies it to the stage row.
- **Why:** A window follows a change of pace without jumping on every slow batch, and the monotonic clock is not moved by a laptop sleep (ADR-0008).

**Sampler scheduling** (M2, MON-2.2)
- **What:** Samples are due at fixed deadlines on the monotonic clock.
- **How:** Each deadline is the previous one plus the interval, not "now" plus the interval. The active-job registry is checked once per active interval even while idle.
- **Why:** The spacing does not drift, and per-second sampling starts within a second of a job starting.

**Watchdog v1 and sleep** (M2, BUG4, ADR-0008)
- **What:** A running job with a stale heartbeat is requeued, up to `max_attempts`, then failed with `worker_lost`.
- **How:** Before judging, the watchdog compares the wall clock with the monotonic clock; a jump means the machine slept, and every job gets a grace period to send a fresh heartbeat.
- **Why:** After a sleep every heartbeat is stale by the length of the sleep, which is not a dead worker.

**Redis and untyped libraries behind one module each** (M2)
- **What:** `core/bus.py` (redis), `mon/probes.py` (psutil, NVML) and `runtime/celery_app.py` (Celery) are checked in pyright's basic mode.
- **How:** Each exposes typed functions or dataclasses; everything else stays strict.
- **Why:** These libraries ship incomplete or no type hints. Confining them keeps "strict everywhere" true for the code that holds logic.

**Tests use their own database** (M2)
- **What:** Integration tests run against database `stratal_test`, Redis database 15 and a temporary data folder.
- **How:** `tests/integration/conftest.py` sets the environment before anything reads settings and creates the database if needed.
- **Why:** Job tests commit rows and start processes. In the development database a running monitor would consume their events and its watchdog would act on their jobs.

**Dependencies added** (M2)
- **What:** `celery[redis]` (SPEC §7.7), `psutil` and `nvidia-ml-py` (SPEC MON tooling). `pytest` moved to its own `test` group, installed in the GPU image too.
- **How:** The `redis` extra brings what Celery's Redis transport imports. GPU-marked tests run with `.\scripts\check.ps1 -Gpu`.
- **Why:** Each is named by the spec for this purpose; GPU tests need a test runner where the GPU is.

## M3: cleaning and preprocessing

**Clean stage is a shell around three owner functions** (M3, DATA-2.1, DATA-2.2, ADR-0012)
- **What:** `stratal.data.clean` feeds, caches, checks and records; `stratal.data.dedupe` (owner) decides which rows are copies and which is kept.
- **How:** The functions are injected as `DedupeUnits`; the owner's three are the default, tests pass fakes or scripted groups. Rows are marked `duplicate` with `dup_kind`, `dup_group_id` (`dg_` + 12 hex of the kind and sorted members) and `dup_of`.
- **Why:** The owner writes only the decisions, in plain functions with no database or events, and can test them alone; the plumbing can be built and tested before they exist.

**The shell checks the owner's output** (M3, INV-05)
- **What:** Groups that break their contract (single members, unsorted, a keeper outside its group, unknown or repeated rows, exact groups of different content, a hash that is not fixed-length hex) fail the stage with `ContractBroken`. After exact marking, two accepted rows with the same content fail it too (INV-05).
- **How:** `_check_groups` and the INV-05 pass run before anything is written; the message names the function and the rule.
- **Why:** A wrong grouping would silently corrupt every later version. Failing loudly, with the rule that was broken, is a review aid as much as a guard.

**Owner code version is part of the config** (M3)
- **What:** `CleanConfig.algorithm_version` defaults to `dedupe.ALGORITHM_VERSION`.
- **How:** It goes into the version's config hash and the pHash cache key.
- **Why:** Without it, stage memoization would keep reusing versions made by an older implementation while the owner iterates on DATA-2.1 and DATA-2.2.

**Near-duplicate threshold is provisional** (M3, OC-5)
- **What:** `phash_hash_size = 8`, `phash_max_distance = 8` by default.
- **How:** Plain config fields; the DATA-2.2 tests read the default, so they follow it when the owner changes it.
- **Why:** OC-5 gives the owner the choice from measured distances; the stage needs some value to run.

**A missing blob fails the stage** (M3, BUG1)
- **What:** If a file a row points at is not in the blob store, clean and preprocess stop with an error instead of marking the row rejected.
- **How:** An existence check before decoding; only decode errors mark a row `corrupt_image`.
- **Why:** A lost blob is data loss, not a bad image. Recording it as a rejection would hide exactly the failure BUG1 describes.

**Preprocess writes one normalised JPEG per accepted image** (M3, DATA-6.1, OC-3)
- **What:** EXIF orientation applied, RGB with transparency flattened onto white, longer side at most 768 px (never enlarged), JPEG quality 95, Lanczos.
- **How:** Every one of those settings is a field of `PreprocessConfig`, so all are in the version's config. JPEG decoding uses Pillow's draft mode to skip work. Copies go into the blob store by content.
- **Why:** One fixed format for every later stage; JPEG keeps 10k copies near 1 GB where PNG would be about ten times that; identical input gives identical bytes, so a rerun adds no files.

**Stages do the work first, then commit** (M3, MON-3.1)
- **What:** Clean and preprocess read the parent manifest twice: once to do the work, once to write the new manifest.
- **How:** Pass 1 stores results (decisions in memory, copies through the cache); pass 2 streams the parent's rows with the results applied into the commit step.
- **Why:** Progress messages come before "Writing manifest" (the M1 ingest still shows them the other way round), and only one small record per row is held in memory (NFR-2).

**Derivation cache built** (M3, ADR-0004, INV-11)
- **What:** The `derivations` table with kinds `phash` and `preprocess`, behind a `DerivationCache` protocol (PostgreSQL in jobs, a dict in unit tests).
- **How:** `get_many` and `put_many` per batch; inserts use `ON CONFLICT DO NOTHING`. The preprocess key includes the Pillow version, which changes the encoded bytes.
- **Why:** A killed stage resumes without redoing finished items, and a rerun with the same parameters is cheap.

**Stage memoization built** (M3, ARCHITECTURE §3)
- **What:** Before running, a derived stage looks for a version with the same stage, parent and config hash, and reuses it (`cache_hit`).
- **How:** `registry.find_memoized` derives the config hash through the same function that names versions (it depends only on the config), and skips versions whose manifest is missing.
- **Why:** Reruns cost nothing, and a changed setting reruns only the stages after it.

**`from_version` starts a pipeline from an existing version** (M3, API.md §3.3)
- **What:** A pipeline request gives exactly one of `source` and `from_version`. With `from_version`, the stages after the one that made the version run, and the dataset comes from the version.
- **How:** Validated in `create_pipeline_job`; the first stage's `input_version_id` is the given version. Overrides or `until_stage` naming a stage that would not run are rejected.
- **Why:** Changing one setting without re-ingesting, and the "continue from this version" action planned for SCR-03.

**Fonts bundled in the repository** (2026-11-15, design system, NFR-7)
- **What:** IBM Plex Sans 400/500/600 and JetBrains Mono 400/500 as WOFF2, with their SIL OFL 1.1 licences, in `assets/fonts/`.
- **How:** Five files fetched individually from each project's release tag (`@ibm/plex-sans@1.1.0`, `v2.304`) rather than the release zips, checked for the WOFF2 signature and the listed sizes, hashes recorded in the folder's README. The design preview declares them with `@font-face`; the frontend will use the same files in M6.
- **Why:** The approved pairing was only visible on machines with the fonts installed, and run time must not depend on the network. Only the weights the design system uses are kept.

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
