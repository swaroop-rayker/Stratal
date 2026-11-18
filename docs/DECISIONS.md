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

**One lock order: the job row before its stage rows** (M2, fixed 2026-11-15, E-009)
- **What:** Any transaction that changes a job's stage rows locks the job's row first; a transaction touching several jobs locks them in ID order.
- **How:** The supervisor, watchdog and job service lock the job with `FOR UPDATE`; the collector's `write_batch` locks the batch's jobs with `FOR NO KEY UPDATE` before applying events.
- **Why:** Two writers taking the same rows in opposite orders deadlock. The first CI run showed it between the collector and `finalize`, at the moment a stage failed.

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
- **What:** `stratal.data.clean` feeds, caches, checks and records; `stratal.data.dedupe` (an owner unit, written by the agent at the owner's request on 2026-11-15) decides which rows are copies and which is kept.
- **How:** The functions are injected as `DedupeUnits`; the owner's three are the default, tests pass fakes or scripted groups. Rows are marked `duplicate` with `dup_kind`, `dup_group_id` (`dg_` + 12 hex of the kind and sorted members) and `dup_of`.
- **Why:** The owner writes only the decisions, in plain functions with no database or events, and can test them alone; the plumbing can be built and tested before they exist.

**The shell checks the owner's output** (M3, INV-05)
- **What:** Groups that break their contract (single members, unsorted, a keeper outside its group, unknown or repeated rows, exact groups of different content, a hash that is not fixed-length hex) fail the stage with `ContractBroken`. After exact marking, two accepted rows with the same content fail it too (INV-05).
- **How:** `_check_groups` and the INV-05 pass run before anything is written; the message names the function and the rule.
- **Why:** A wrong grouping would silently corrupt every later version. Failing loudly, with the rule that was broken, is a review aid as much as a guard.

**Owner code version is part of the config** (M3)
- **What:** `CleanConfig.algorithm_version` defaults to `dedupe.ALGORITHM_VERSION`.
- **How:** It goes into the version's config hash and the pHash cache key.
- **Why:** Without it, stage memoization would keep reusing versions made by an older implementation of DATA-2.1 and DATA-2.2.

**Near-duplicate threshold: 8 of 64 bits** (M3, OC-5; chosen by the owner 2026-11-15)
- **What:** `phash_hash_size = 8`, `phash_max_distance = 8` by default.
- **How:** Plain config fields; the DATA-2.2 tests read the default, so they follow it when the owner changes it. Measured 2026-11-15 on generated pictures (600 drawn for the test, and the 1,500 in `sources/demo/valid`), 64-bit hashes:
  - JPEG 90, 70 or 40, or a resize to half size: at most 4 bits from the original.
  - Brightness +15 %: at most 8 (95 % within 4).
  - A 3° rotation: median 8, at most 16.
  - A 5 % crop on each side: median 10, at most 22.
  - Unrelated pictures: the nearest other picture was 12 to 22 bits away. Nothing in the demo folder groups at 8 or 10; one pair groups at 12.
  - Distances are always even: each hash has exactly 32 one-bits, because each bit says above or below the median of 64 values. A threshold of 9 therefore behaves exactly like 8.
- **Why:** OC-5 gives the owner the choice from measured distances. The owner accepted 8 on 2026-11-15: it sits between re-encodings (≤ 4) and the nearest unrelated generated pictures (≥ 12). Generated pictures are not photographs, so the same two measurements are worth repeating on the phase-1 dataset; a change is a config change and gives new version IDs.
- **On real photographs (S-07 run, 2,000 COCO images):** one near-duplicate, at exactly 8 bits: two burst shots of the same table seconds apart (Flickr 9853575984 and 9853704863), the larger file kept. A correct catch, and the kind that would leak between train and test splits in M5.

**Exact duplicates keep the smallest row key** (M3, DATA-2.1)
- **What:** Files with the same SHA-256 form one group; the copy with the smallest `row_key` stays accepted.
- **How:** One pass into a dict keyed by hash; buckets of two or more keys are the groups.
- **Why:** Identical bytes means no copy is better than another. The smallest key is the simplest rule that does not depend on input order (NFR-4).

**pHash written out, without the imagehash library** (M3, DATA-2.2)
- **What:** The classic DCT pHash, in the same steps as `imagehash.phash` (SPEC §7.2 lists that library): greyscale, Lanczos down to 32×32, the 8×8 lowest DCT-II frequencies, one bit each for above or below their median.
- **How:** `dedupe.perceptual_hash`, about 40 lines of Python. A separable DCT computes only the 64 low frequencies (about 10,000 multiply-adds an image), from a cached cosine table. Transparent pixels are flattened onto white, as in preprocess. 16-bit and float images are rescaled from their own range, because Pillow's conversion clips them to white.
- **Why:** No requirement needs the dependency, and imagehash would bring numpy, scipy and PyWavelets into the CPU worker for one function. Hashing takes 2.3 ms on a decoded 512 px image; with opening and decoding, 9.2 ms per demo file. The bits should match `imagehash.phash` for RGB and greyscale images: the steps are the same, and the DCT differs from scipy's unnormalised one only by a factor shared by every coefficient, which cannot change a comparison with the median. This has not been checked against the library, because that would mean installing it. Swapping the library in later would change hashes only for unusual image modes.

**Near duplicates group transitively** (M3, DATA-2.2)
- **What:** If A is within the threshold of B, and B of C, then A, B and C are one group.
- **How:** A union-find (path halving, union by size) over the pairs found; each connected component of two or more rows is a group.
- **Why:** Copies of copies form chains, and every row then belongs to exactly one group, which a non-transitive rule cannot promise. The cost is that a long chain can join two pictures that are not alike. With 8 of 64 bits that is rare, and SCR-03 shows each group side by side for review.

**Near-duplicate keeper: most pixels, then most bytes, then smallest key** (M3, DATA-2.2)
- **What:** In a near-duplicate group the row kept is the largest image, then the largest file, then the smallest `row_key`.
- **How:** `_keeper_rank` sorts by `(-width × height, -size_bytes, row_key)`.
- **Why:** For one picture, more pixels and more bytes usually mean the better copy (JPEG 90 over 70). The key decides ties, so the result does not depend on input order.

**Hashes compared through a pigeonhole index** (M3, DATA-2.2)
- **What:** Only hashes that agree exactly on some part of their bits are compared.
- **How:** The 64 bits are split into `max_distance + 1` chunks, and rows are bucketed by each chunk's value. Two hashes at most `max_distance` bits apart cannot differ in every chunk, so every such pair shares at least one bucket.
- **Why:** All pairs of 20,000 rows are 200 million comparisons. The index needs about 13 million for random hashes at a threshold of 8, and it finds the same pairs. The unit test groups 20,000 hashes in about a second.

**The Pillow version is part of the pHash cache key** (M3, DATA-2.2, ADR-0004)
- **What:** Cached pHash values are keyed by `hash_size`, `algorithm_version` and the Pillow version.
- **How:** Added to the derivation cache's parameters in `clean.py`, as the preprocess key already did.
- **Why:** A Pillow upgrade can change Lanczos resampling and so flip a bit near the median. A new version recomputes the hashes instead of mixing old values with new ones.

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

## M4: labeling

**Phase-1 dataset: a 10k subset of COCO 2017** (M4, SPEC §6 open question; accepted by the owner 2026-11-15)
- **What:** 10,000 images from COCO 2017 with their five human captions each.
- **How:** A seeded random choice of image IDs, the list kept in the repository so the subset can be rebuilt; images in `sources/`, never in git.
- **Why:** Everyday photographs with reference captions, so captioning quality (CIDEr-D, CL-13) and CLIP scores can be checked against human text; 10k is a few hours of captioning at NFR-3's 2 img/s and about 1.6 GB on disk.

**COCO images chosen by licence** (M4, LABEL-1.1)
- **What:** The 10,000 images come only from the 30,836 COCO 2017 training images whose Flickr licence allows reuse, commercial reuse included: CC BY, CC BY-SA, or no known copyright.
- **How:** `scripts/datasets/coco10k.py select` draws them with seed 2026; the IDs are committed in `scripts/datasets/coco10k_ids.txt`, and each image's licence, Flickr link and five captions go to `sources/coco10k-meta/images.jsonl`. The annotations zip matched its published MD5 and SHA-1.
- **Why:** The repository is on GitHub, and later LoRA samples (IMG-1.2) may be shown. CC BY needs attribution, so the link is kept. COCO serves only plain HTTP and has no per-image checksums; ingest rejects anything that does not decode.

**Model allow-list with every file pinned** (M4, NFR-8, ARCHITECTURE §8)
- **What:** `stratal.pretrained.catalog` lists each allowed model by repository, full commit hash and every file it may fetch, with its size and hash. `python -m stratal.cli models pull` is the only code that contacts the hub.
- **How:** Large files are checked against the SHA-256 in their Git LFS pointer, small ones against git's blob SHA-1, while streaming to a temporary name; a mismatch is deleted and stops the pull. A stamp file is written last. The download uses the standard library, so pulling needs no new dependency; jobs run with `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` set (NFR-7).
- **Why:** Pinning a revision trusts whatever the hub serves for it; pinning hashes does not. Listing files means pickle weights (`pytorch_model.bin`, `tf_model.h5`) that repositories also carry can never be fetched.

**CLIP ViT-B/32 from LAION, BLIP-large for S-06** (M4, LABEL-1.3, spike S-06)
- **What:** CLIP scoring uses `laion/CLIP-ViT-B-32-laion2B-s34B-b79K`; the comparison captioner is `Salesforce/blip-image-captioning-large`. Florence-2-base stays the default (ADR-0009).
- **How:** Each pinned in the catalog; 605 MB and 1.88 GB.
- **Why:** OpenAI's ViT-B/32 and BLIP-base carry safetensors only in unreviewed community pull requests, which anyone can upload; their main branches have pickle weights only. These two publish safetensors on their main branch. LAION's model is the same architecture, so scores are comparable in kind but not in value with published OpenAI-CLIP scores.

**Default captioner: Florence-2-base, `<CAPTION>`** (M4, LABEL-1.1, spike S-06; chosen by the owner 2026-11-15)
- **What:** The label stage captions with Florence-2-base's `<CAPTION>` task, fp16, three beams, at most 40 new tokens.
- **How:** The defaults of the label stage's config; BLIP-large stays on the allow-list as the alternative.
- **Why:** In S-06 its captions scored level with COCO's human captions on CLIP (0.300 against 0.302) at 10 img/s. The detailed task runs past CLIP's 77 tokens and takes twice as long; BLIP-large showed a fixed "there is a ..." opening and more mistakes.

**transformers in the GPU worker only** (M4, LABEL-1.1, LABEL-1.3)
- **What:** `transformers` (SPEC §7.2) is in the `gpu` extra, with the 13 packages it brings, numpy among them.
- **How:** Re-locked with uv; the API, CPU worker and dev images are unchanged.
- **Why:** Only GPU stages load models, and the API image must stay free of ML libraries.

**Label stage: the GPU only for what is missing** (M4, LABEL-1.1 to 1.3, ADR-0006)
- **What:** The label stage looks every image up in the derivation cache first and takes the GPU lease, sets the VRAM cap and loads a model only if something is missing: once for captions, once for CLIP scores, one model at a time.
- **How:** Captions are keyed by the preprocessed copy's hash and every setting that can change the text, including library versions; scores by image and caption together. The caption loop commits per batch; the shell commits CLIP scores per batch. A second pass writes the manifest from the cache.
- **Why:** A killed or repeated run resumes from the cache without reloading models (LABEL-1.2), identical copies are captioned once, and the lease is never held while nothing uses the GPU (BUG6).

**VRAM estimates from measurements** (M4, ADR-0006 rule 4; revised 2026-11-15 after the demo)
- **What:** Before loading, the card must have free the weights file's size plus 3.9 GB for the captioner (4.3 GB for Florence-2) or 1.2 GB for CLIP.
- **How:** `caption_vram_estimate_mb` and `clip_vram_estimate_mb` in the label config; the VRAM manager compares the total with the driver's free memory. The weights file's size is used as is, an upper bound at either precision: Florence-2-base is stored in fp16 (452 MB on the card), CLIP and BLIP in fp32.
- **Why:** Florence-2 at batch 16 peaks at 4.05 GB reserved (S-06; 3.78 GB in the NFR-1 run), almost all of it activations. The first estimate asked for 4.7 GB of the 5.07 GB that was free in the demo, leaving 0.3 GB for anything else on the card; 4.3 GB still covers the peak with a quarter of a gigabyte to spare.

**CLIP threshold: 0.2 on the cosine scale** (M4, LABEL-1.4, OC-5; chosen by the owner 2026-11-15)
- **What:** `FilterConfig.clip_threshold = 0.2` by default.
- **How:** A config field; a new value reruns only `filter` and later stages.
- **Why:** OC-5 gives the owner the choice from the measured score distribution; the owner kept 0.2 after seeing it. Measured in the M4 demo, 994 COCO images captioned by Florence-2 and scored as plain cosine:
  - Median 0.313, middle half 0.286 to 0.340, lowest 0.165, highest 0.455. One peak; no separate cluster of bad captions.
  - Below 0.18: 0.3 %; below 0.20: 0.9 %; below 0.22: 2.3 %; below 0.25: 6.9 %.
  - The nine rows under 0.20 are mostly plausible captions of busy, generic street scenes, with a few partly wrong ("a painting of a man pushing a cart", "mushroom" for a bagel). CLIP scores busy scenes lower whatever the caption, so a threshold trims a tail more than it catches wrong captions; the manual review (LABEL-1.5, M5) will show how many of the trimmed rows were bad.

**CLIP similarity is plain cosine** (M4, LABEL-1.3; written by the agent at the owner's request)
- **What:** `clip_score` is the cosine between CLIP's image and text embeddings, in [-1, 1], neither rescaled nor clipped.
- **How:** `math.sumprod` for the dot product, one square root of `|a|²·|b|²` (so a vector against itself gives exactly 1.0), clamped to [-1, 1]; zero, NaN or infinite vectors are refused.
- **Why:** It is the quantity CLIP was trained on, and the scale S-06 and the demo measured. CLIPScore's 2.5 · max(cos, 0) can be computed from it at report time; clipping here would erase the difference between unrelated and contradictory.

**Filter rule refuses what would make it order- or NaN-dependent** (M4, LABEL-1.4)
- **What:** Missing or blank captions are marked first; then scores below the threshold. A row key given twice, a captioned row without a score, and a score that is not finite are all errors.
- **How:** One pass with a set of keys seen; the result is sorted by row key.
- **Why:** A NaN compares false with every threshold and would silently keep its row; duplicate keys would make the answer depend on order (rule 5).

**Caption loop halves on out-of-memory and never grows back** (M4, LABEL-1.1, LABEL-1.2, BUG2)
- **What:** On `CudaOutOfMemory` the batch is halved, down to 1, with a WARN naming the new size and the config key to set; the same images are retried; the size stays reduced for the rest of the run.
- **How:** Each batch is committed before the next starts; the progress total is recomputed as batches done plus remaining images at the current size; a failed batch's images are reopened, not kept.
- **Why:** Halving finds a fitting size in at most log2(batch) retries. Every image is a preprocessed copy of at most 768 px, so a size that failed once would fail again; growing back would cost one out-of-memory error each time for little speed.

**VRAM manager measures after emptying the cache, with a 1 MB tolerance** (M4, RUN-1.4, BUG5)
- **What:** Before a load it collects garbage, synchronises and empties PyTorch's cache, then reads the baseline and the driver's free memory; after an unload it does the same and compares allocated memory with the baseline.
- **How:** `TOLERANCE_MB = 1.0`. A leak is a WARN with the megabytes still held, not an exception; a loader that fails part-way is cleaned up before the error propagates.
- **Why:** Cached but unused blocks of this process are free for the next model, and an honest baseline needs them gone. Measured on the card: after Florence-2 and CLIP had run, allocated memory came back to exactly its baseline, cuBLAS workspaces included, so any real leak (one layer's weights is many megabytes) stands out.

## M5: splits, review, evaluation report

**DATA-4 approved as drafted** (M5, DATA-4; approved by the owner 2026-11-15)
- **What:** The split requirements DATA-4.1 to 4.8, drafted by the agent at the owner's request and approved as v1 without changes; with them, the review decisions taken as proposed (below).
- **How:** Requirement text in SPEC.md §DATA-4; reasoning and measurements in `docs/drafts/DATA-4_v1.md`; new manifest columns `split_group` and `split_stratum` in DATA_MODEL.md.
- **Why:** M5 could not start without them (KICKOFF.md). The draft answers the four questions the input set, from measurements on 2,958 labelled COCO rows.

**Split proportions 80/10/10, leakage distance 12 bits** (M5, DATA-4.1, DATA-4.2)
- **What:** `train` 0.8, `val` 0.1, `test` 0.1 by default; accepted rows within 12 bits of each other by pHash join one split group.
- **How:** Config fields of the split stage, recorded in each split version.
- **Why:** About 1,000 test images from the 10k set. Above the dedupe threshold, pHash mixes same-event shots with lookalikes; grouping a false match costs a little randomness while a missed pair inflates test scores. At 12 bits 2 % of rows group, at most 4 to a group.

**Stratified on CLIP quartile, orientation and source folder; carried over, shortfalls filled** (M5, DATA-4.3 to 4.5)
- **What:** New split groups fill each stratum's shortfall towards the proportions; rows of the dataset's previous split with the same proportions and seed keep their split.
- **How:** The previous split's version ID and the seed go into the split version's config (DATA-4.4); moves happen only when a new row joins two groups, and are reported.
- **Why:** Exact proportions per stratum and stable assignments together. Pure hashing would be stable without history but right only on average, several points off in a 100-row stratum.

**Review measures and changes nothing in phase 1** (M5, DATA-4.7, LABEL-1.5)
- **What:** The review samples a split version; its decisions change no split and no status.
- **How:** Each review item records its row's split; the phase-1 report gives acceptance overall and for `test`.
- **Why:** The review estimates label quality with a Wilson interval (EVAL-1.1); editing the data from a sample would bias the very estimate it produces.

**DATA-4.8 moves to M7, with DATA-2.3** (M5, DATA-4.8; the owner's decision, 2026-11-16)
- **What:** The P2 criterion that rows of one semantic-duplicate cluster join one split group is tested in M7, not M5. The requirement itself is unchanged.
- **How:** MILESTONES.md lists it in M7's scope and checklist; M5 closed with DATA-4.1 to DATA-4.7 done.
- **Why:** It cannot be tested before semantic clusters exist (DATA-2.3, "M7 if time"). `group_for_split` already joins rows by any shared group ID, so the clusters will plug into the same mechanism.

**Split units cut into grouping, strata and assignment** (M5, DATA-4.1 to 4.5; agent, stubs for the owner)
- **What:** Three owner functions in `stratal.data.splitting`: `group_for_split` (DATA-4.2), `strata_for` (DATA-4.3), `assign_splits` (DATA-4.1, 4.4, 4.5). The shell does the carry-over lookup and lists moved rows.
- **How:** A group counts in its anchor's stratum. "Within one group" means less than the largest group's size, per stratum and for the totals together. A merge goes to the split with more members, ties to the larger proportion, then train, val, test. A root-level file's source folder is `.` (the owner's choice, 2026-11-16, over the source's name).
- **Why:** Each function then has one idea to learn and test. The lookup needs the database and earlier manifests, which owner units never touch. Moved rows can be checked independently of the function that moved them. The tie rule is an interpretation DATA-4 leaves open. `.` says what is true of the row (it sits at the root) and needs nothing beyond `row_key`; a flat source then simply adds no information to the stratum.

**DATA-4 units: all-pairs grouping, controlled rounding, seeded packing** (M5, DATA-4.1 to 4.5; written by the agent at the owner's request, 2026-11-16)
- **What:** `group_for_split`, `strata_for` and `assign_splits` in `stratal.data.splitting`, meeting the contracts written with the stubs.
- **How:** Grouping: a union-find over a shared duplicate group plus an all-pairs XOR and popcount (10k rows in 4 s, 20k in 18 s). Strata: `statistics.quantiles(..., method="inclusive")`, with a tie going to the lower quartile. Assignment, in four steps:
  1. Known groups go first; a merge goes to the side with more members.
  2. Multi-row groups go, largest first, to a split whose room in their stratum fits them, at random weighted by that room. A group that fits nowhere overshoots the split with the most room overall, among those where its stratum stays within its bound.
  3. Single rows fill quotas from a simplex projection and a controlled rounding of the strata-by-splits matrix (a small bipartite matching).
  4. A repair pass moves new groups from an over-full split to an under-full one, within the stratum bounds, until every total is within one group.

  All randomness comes from `Random(f"{seed}|{stratum}")`.
- **Why:** The pigeonhole index of dedupe saves little at 12 bits. Rounding each stratum on its own lets the totals drift by up to one row per stratum, so splits would miss DATA-4.1.AC1 with many strata. Random placement keeps file names and order from deciding a split (rule 7). Steps 2 and 4 were added after the owner asked for a test of the overshoot case: ten strata that are one group each sent every group to train. A fuzz of random mixes then showed totals missing in 366 of 1,000 when 30 % of strata are mostly groups. With the repair pass, all of 3,000 random mixes pass. That is measured, not proven (a greedy local search), and 300 seeded mixes are now a test. On coco1k and coco2k the totals and all 12 strata are within one group, with no leaks.

**EVAL-1.1 statistics written; the filter rate is the filter stage's** (M5, EVAL-1.1; written by the agent at the owner's request, 2026-11-16)
- **What:** The five functions in `stratal.eval.report_stats`, meeting the contracts written with the stubs.
- **How:** Counts are kept in one pass, holding only the counters. Quantiles are type 7, read from the sorted list. Bins are found by `bisect_right`, which makes them half-open, with the last one closed. Wilson uses `NormalDist().inv_cdf` for z, the closed form of the score interval, and exact 0 and 1 at the extremes. Acceptance is reported overall and per split, in the order train, val, test.
- **Why:** These are the standard definitions, and the tests check them against published values. The filter rate counts every row the filter stage took out, schema rejections (DATA-5.1) included, because the contract defines it by stage. The report keeps the reasons apart, so the CLIP-only rate can still be read from `rejections.by_stage.filter`.

**Report statistics as five pure functions** (M5, EVAL-1.1; agent, stubs for the owner)
- **What:** `rejection_counts`, `filter_rate`, `score_distribution`, `wilson_interval` and `review_acceptance` in `stratal.eval.report_stats`; the assembly and the evaluate stage are the agent's.
- **How:** Fixed histogram edges chosen by the caller; type-7 quantiles; sample standard deviation; the filter rate is over the rows that reached the filter; z is computed from the normal quantile, not rounded to 1.96. The Wilson tests use Newcombe (1998), table I.
- **Why:** API.md §5 lists all four report parts as the owner's statistics. Fixed edges let reports of different versions be compared bin by bin, and published values make the interval checkable without trusting our own arithmetic.

**The split continues the newest earlier split, never one of its own parent** (M5, DATA-4.4, DATA-4.5; agent)
- **What:** The runner picks the dataset's newest `split` version with the same proportions and seed whose parent is a different version, and records it as `continues_version_id` before the memo lookup.
- **How:** `registry.find_previous_split`; the ID goes into the config, so it is part of both the version ID and the memo key. A kept copy that replaced another inherits that copy's split through `dup_of`.
- **Why:** Without the exclusion, a second split of the same parent would continue the first, get a different config and so a different version ID, which would break DATA-4.4.AC1 and NFR-4.

**Review samples are simple random samples by seed** (M5, LABEL-1.5, DATA-4.7; agent)
- **What:** A session draws `sample_size` accepted rows uniformly with a recorded seed and stores each one with its split. It does not stratify by split.
- **How:** The keys are sorted, then sampled with `random.Random(seed).sample`. A server-drawn seed is stored. Close counts the decisions and leaves the rate and the interval to the owner's `review_acceptance`. Until that unit exists, close still succeeds and the summary says what is missing.
- **Why:** A uniform sample estimates acceptance for the whole version without reweighting. With 80/10/10 a 50-row sample holds about 5 test rows, so the test-split interval will be wide. A larger sample, or sampling the test split on its own, can be added if the owner wants a tighter test figure.

**The filter stage validates the schema; a missing file rejects the row** (M5, DATA-5.1; the owner's choice, 2026-11-16)
- **What:** After the CLIP rule, each row that is still accepted is checked against the image–caption schema. A row with no caption, or whose original or preprocessed file is missing, is marked `rejected` (`missing_caption`, `missing_file`) at stage `filter`. The version gets `dtype = image_caption`, and the split keeps it.
- **How:** The M1 validator, `validate_image_caption`, now also checks `derived_sha256`; `apply_report` marks the rows. The type is the new config field `validate_as` (`image_caption`), so filter versions made before M5 have a different config and are not reused by memoization. The report is stored as `counts.validation`. Rejections come with a WARN event that has a reason and an action.
- **Why:** DATA-5.1.AC1 asks for exactly this, rejection with a report. Asked whether a lost file should instead fail the stage, as in clean and preprocess (M3, BUG1), the owner chose rejection. The WARN and the listed rows keep the loss visible.

**Evaluate stores a report and makes no version** (M5, EVAL-1.1; agent)
- **What:** The evaluate stage ends the phase-1 pipeline. It writes one `eval_reports` row and passes its input version on as its output. `POST /versions/{id}/reports` runs it alone as a job of kind `report`.
- **How:** `stratal.eval.report.assemble_phase1_report` reads the manifest twice (statuses, then scores) and calls the owner's five statistics. Without a named review it uses the version's newest closed review. The histograms share fixed edges, 0 to 0.5 in steps of 0.025.
- **Why:** A report describes a version and changes no data, so it gets no version of its own. Because the output is passed on, a pipeline's output stays the version the report is about. A new review after the pipeline only needs a new report, never a rerun. A consequence: a pipeline started from the final version now runs only `evaluate`, where before M5 it was refused.

**The split report lives in the version; a leak warns rather than fails** (M5, DATA-4.6, DATA-4.2.AC2; agent)
- **What:** The report is stored as `counts.split` of the split version. The stage's own all-pairs check compares every cross-split pair. If a pair leaks, the stage emits a WARN with a reason and an action, and the report lists examples.
- **How:** The check compares pHash values bit by bit and duplicate-group IDs, without using the owner's groups. On 10k rows it compares about 17 million cross-split pairs in plain Python.
- **Why:** The report then travels with the version, and a reused version keeps it. A failing check is a defect in the grouping, and the version is still inspectable, so the leaks are reported but the version is kept. The fakes join no pairs, so they would leak on folders that have near pairs, and the stage still has to run there.

## M6: phase-1 screens

**Rows are filtered by reading the whole manifest with PyArrow** (M6, DATA-3.1, UI-2.1; agent)
- **What:** `GET /versions/{id}/rows` reads the version's Parquet file, filters it with PyArrow compute expressions, and returns one page in `row_key` order, with the count of matching rows.
- **How:** `storage.manifest.select_rows`; the cursor is the last row key of a page, which is stable because manifests are sorted by `row_key` and never change (INV-01).
- **Why:** A 10k-row manifest is 1–2 MB, read and filtered in milliseconds. DuckDB or an index would add a dependency for no measurable gain at phase-1 sizes; streaming in batches would make `matched` cost a second pass.

**Thumbnails are made per request and cached by the browser** (M6, DATA-3.4, UI-2.1; agent)
- **What:** `GET /blobs/{sha256}?w=` decodes, turns upright and shrinks the image on each request, and marks the response immutable.
- **How:** Pillow's JPEG draft mode, then a Lanczos resize to the width; no thumbnail is written to the data volume.
- **Why:** Blobs are content-addressed, so the URL never serves different bytes and the browser cache does the work. A thumbnail store would be a second copy of the data with its own garbage collection.

**The monitor describes the machine for `GET /system`** (M6, MON-2.1; agent)
- **What:** At start the monitor writes the GPU name, VRAM and RAM totals and CPU count to the Redis key `stratal:system`; the API reads it, falling back to the latest resource sample.
- **How:** `Bus.set_system` / `get_system`; `GpuProbe.name()` through NVML.
- **Why:** The API container has no GPU and must not get one (API image has no ML libraries). The monitor already holds NVML open.

**Response models describe the API for the generated client** (M6, SPEC §7.3, UI-2.1; agent)
- **What:** Pydantic response models in `stratal.api.schemas` for the endpoints each screen reads (jobs, events, samples, system so far), and an SSE "messages" model on the two stream routes.
- **How:** `response_model=` on the routes, which still build dicts; FastAPI validates every response against its model. openapi-typescript turns the document into `frontend/src/api/schema.d.ts`; openapi-fetch calls it. Models are added screen by screen.
- **Why:** Routes returning `dict[str, Any]` gave the frontend untyped bodies, so the types could drift, which §7.3 rules out. Validation also catches a route that strays from its contract in the existing integration tests.

**Design-system CSS is copied, not rewritten** (M6, UI-2.1, SPEC §9.6; agent)
- **What:** The frontend uses the approved preview's token block and component CSS unchanged, and the SCR-01 mockup's shell CSS with marked "live:" changes. React components apply those classes; Tailwind adds only layout utilities, mapped onto the same tokens.
- **How:** `tokens.css`, `components.css`, `shell.css`, `theme.css` (Tailwind without preflight and without its palette); `scripts/no-raw-hex.mjs` in `npm run lint`.
- **Why:** G3 asks for a visual match with approved mockups; the same rules give the same pixels, and a reimplementation in utility classes would drift. Without its palette, Tailwind has no class for a one-off colour.

**Node in two services, TypeScript 5.9** (M6, SPEC §7.4; agent)
- **What:** `frontend` runs the Vite dev server (0.4 GB, as budgeted); `frontend-tools` runs typecheck, lint, tests and build on demand (1.5 GB, profile `dev`). TypeScript is pinned at 5.9.3.
- **How:** One image (`docker/frontend.Dockerfile`, Norton's root through `NODE_EXTRA_CA_CERTS`), the source bind-mounted, `node_modules` in a volume, file watching by polling.
- **Why:** Type-aware linting ran out of heap at the dev server's limit. Keeping the checks in an on-demand container leaves the always-on stack within NFR-2. openapi-typescript 7 and typescript-eslint 8 do not yet accept TypeScript 6.

**The lineage view is HTML, not React Flow** (M6, DATA-3.3, UI-2.1; the owner's approval at SCR-03's G3, 2026-11-16)
- **What:** SCR-03's lineage is a strip of linked nodes in plain HTML that forks into rows. SPEC §7.4 lists React Flow for "model builder, lineage graph, router view"; React Flow is kept for the model builder (SCR-08).
- **How:** `frontend/src/screens/version/LineageStrip.tsx`: ancestors in order, this version, descendants grouped by parent; the strip scrolls sideways and keeps this version in view.
- **Why:** The approved mockup draws a strip and its accessibility notes ask for "a list of links in order". A React Flow canvas is pannable and its nodes are not links. Nothing in phase 1 needs to drag or connect lineage nodes.

**Event lists can return their newest page** (M6, MON-4.3; agent)
- **What:** `GET /events` and `GET /jobs/{id}/events` take `tail` (the newest `limit` matching events, still oldest first) and `min_level` (that level and above).
- **How:** `events_after(..., tail=, min_level=)` orders by ID descending for a tail and reverses; levels are compared by their order DEBUG < INFO < WARN < ERROR.
- **Why:** SCR-04's log opens on its latest lines and filters "WARN and above". Paging forward from the oldest event would read a job's whole history first; filtering levels on the client would miss older warnings beyond the page.

**ECharts is not pre-bundled by the dev server** (M6, MON-4.2; agent)
- **What:** `optimizeDeps.exclude` lists `echarts` and `zrender` in `vite.config.ts`.
- **How:** Vite serves their ES modules as they are in development; the production build bundles them as usual.
- **Why:** Pre-bundling ECharts pushed the Vite container past its 0.4 GB limit and the kernel killed it. Raising the limit would break the 9 GB budget (ARCHITECTURE.md §2).

**The live stream reopens itself after an HTTP error** (M6, MON-1.4; agent)
- **What:** The UI's single global EventSource is reopened with backoff (1 s doubling to 15 s) when the browser gives up, resuming with `?after=<last event ID>`; when it comes back, every query is read again.
- **How:** `Feed` in `frontend/src/api/stream.ts`; tests `test_MON_1_4_*`.
- **Why:** Browsers retry a dropped SSE connection but give up for good on an HTTP error, which is what the dev proxy returns while the API restarts. A new EventSource cannot send `Last-Event-ID`, so the cursor goes in the query, which API.md §4 already allows.

**A report is built where it is read** (M6, EVAL-1.1; agent)
- **What:** "Build the eval report" on SCR-03 and SCR-05 opens SCR-06 with `?job=<id>`, which follows the evaluate job on the global stream and shows the new report when it completes. A new `GET /versions/{id}/reviews` lets SCR-06 name the review a build will use and notice a closed review newer than the report's.
- **How:** `frontend/src/screens/report/ReportScreen.tsx`; `version_reviews` in `src/stratal/api/reviews.py`; tests `test_EVAL_1_1_*`, `test_LABEL_1_5_a_version_lists_its_reviews_newest_first`.
- **Why:** A build takes seconds; leaving the user on the page that started it means a second trip to find the result. The report stores which review it used, so only the list of reviews was missing to say when it is out of date.

**The jobs list keeps active jobs whole and pages the rest** (M6, RUN-1.1 slice; agent)
- **What:** SCR-07 asks `GET /jobs` twice: every active job (queued, running, stalled, paused) for the pinned Active section, and finished jobs a page at a time. A `job` message on the global stream patches a row in place; a new job or a change of state reloads both lists and is announced once.
- **How:** `frontend/src/screens/jobs/JobsScreen.tsx`; `q` (job-ID substring) added to `GET /jobs`; tests `test_RUN_1_1_*`, `test_RUN_1_2_*`.
- **Why:** Active jobs are few and must never fall onto a later page; finished jobs grow without bound. Reloading on a change of state is simpler than moving rows between cached pages, and changes of state are rare.

## M7: phase-1 acceptance

**The owner's M7 decisions** (2026-11-17)
- **What:** NFR-5's N is 10 minutes (OC-3's proposal). The NFR-2 profile runs on 20,000 COCO 2017 images, 10,000 more fetched with the coco10k script. The agent writes DATA-2.3 and DATA-4.8 at the owner's request, and fixes E-011.
- **How:** SPEC NFR-5 and OC-3 updated; the kill tests check every stage against 10 minutes.
- **Why:** Kill tests need a number to assert. A 20k run is what NFR-2's criterion names; changing it to 10k would have changed a requirement.

**Registering a version is idempotent under concurrency** (M7, BUG1, DATA-3.3; agent, at the owner's request)
- **What:** Two jobs that make the same version at once both succeed; the version is recorded once. A different version under an existing ID is refused as a collision.
- **How:** `INSERT … ON CONFLICT (version_id) DO NOTHING`, then the row is read back and its content and config hashes compared, in `registry.register_version`; tests `test_BUG1_two_jobs_registering_the_same_version_at_once_both_succeed`, `test_BUG1_a_different_version_under_the_same_id_is_refused`; E-011.
- **Why:** Content addressing means identical work gives identical IDs, so a second insert of the same ID is the same version, not an error. A lock or a "one job per input" rule would serialise work that is already safe to repeat.

**Events are backfilled from the spool by the supervisor** (M7, MON-1.4, ADR-0005 decision 2; agent)
- **What:** After each stage, the job's spooled events missing from PostgreSQL are inserted; `stratal.cli events backfill` does it by hand.
- **How:** `stratal.mon.backfill.backfill_job`, called from `supervisor.run_stage` after `finalize`, in its own transaction, never changing the outcome; tests `test_MON_1_4_*` in `test_M7_event_backfill.py`. `test_NFR_13_kill_the_monitor_mid_job_and_the_job_continues` now proves the monitor's death while the job still runs, since the backfill fills the gap at the stage's end.
- **Why:** The worker has the data volume and knows when a spool is complete (its child has exited). `monitor` has neither, and giving it the volume only for this would widen its access.

**NFR-5 kill tests use a test-only crash point** (M7, NFR-5; agent)
- **What:** `STRATAL_TEST_CRASH_AT=<stage>:step:<k>` (or `:event:<n>`) makes a job's stage process SIGKILL itself just after that event is spooled, on the first attempt only. One test per phase-1 stage kills it, retries the job, and checks the version IDs against an uninterrupted run and the cache reuse.
- **How:** `JobContext.event` in `runtime/context.py`; `tests/integration/test_M7_kill_tests.py`, `test_NFR_5_*`.
- **Why:** On test data a stage finishes in about a second, so killing it from outside lands at a random point or after the end. Stages report progress right after a batch is cached, so killing just after a progress event is the realistic worst case: a crash between batches. Ingest, filter, split and evaluate have no per-item cache and restart whole; their bound is their duration on the 10k set.

**NFR-11 is measured as a worst case, with monitoring switchable off** (M7, NFR-11; agent)
- **What:** `STRATAL_MONITORING_ENABLED=false` gives a job a no-op emitter and no memory reporter; with no `monitor` process that is "monitoring off". The benchmark runs a CPU-bound synthetic stage with one event per item (about 50 a second) three times each way.
- **How:** `tests/integration/test_M7_monitoring_overhead.py`, marked `benchmark` and run by `check.ps1 -Bench`, not by the normal integration run, since a 1% timing difference needs a quiet machine.
- **Why:** NFR-11 needs an "off" to compare with. 50 events a second is far above any real stage, so a pass covers them. **Decided by the owner on 2026-11-17 (SPEC OC-6):** "2% CPU" is 2% of the machine (16 logical CPUs). The first run (during a download) measured 1.42% of the machine, 22.7% of one core; on a quiet machine, 1.58% of the machine, 25.2% of one core, with runtime +0.02%. Read per core, NFR-11 fails at this event rate.

**The owner's decisions at M7's close** (2026-11-17)
- **What:** ADR-0013 accepted, with DATA-2.3 and DATA-4.8 built in M11; NFR-11's "2 % CPU" read as a share of the machine (SPEC OC-6); M7's BUG item reworded to BUG1 to BUG6, with BUG7 tested in M8; events kept at every level, DEBUG included.
- **How:** MILESTONES.md (M7 and M11), ADR-0013's status, SPEC Appendix B, DATA_MODEL §10.
- **Why:** Phase 1 closes without its two P2 extras; NFR-11's test needed a fixed meaning; BUG7 needs training to test; the events table is 1.6 MB after 26 jobs, so pruning would buy nothing.

**Telemetry downsampling in tables, run by a one-process `beat`** (M7, NFR-12; agent)
- **What:** Raw samples older than 7 days become one-minute averages in `resource_samples_1m` and `process_samples_1m` (not Parquet files under `telemetry/1m/`, the other option DATA_MODEL listed). `beat` is a Celery worker on its own `maintenance` queue with the scheduler embedded (`-B`) and the solo pool, running the job hourly.
- **How:** `stratal.mon.retention.downsample`, one transaction per six hours of raw data (insert the averages, delete the raw rows); task `stratal.downsample_telemetry`; `GET /jobs/{id}/telemetry` returns both; tests `test_NFR_12_*`.
- **Why:** In Postgres, the telemetry endpoint serves an old job's charts with the same query and no new reader. Insert and delete in one transaction make a crash or a rerun harmless. A queue of its own keeps maintenance from waiting behind an hour-long stage on `worker-cpu`; the solo pool halved `beat`'s memory (144 to 100 MB of its 200 MB) by not keeping a child process.

## M8: training runtime and registry

**The owner's M8 kickoff decisions** (2026-11-17)
- **What:** CIFAR-10 for the CNN (download approved), scikit-learn's bundled tables, COCO captions as labelled text; `/predict` falls back to the CPU while training holds the GPU; a `worker-cpu` image with an `ml-cpu` extra; the proposed defaults (stall 300 s, checkpoint every epoch and every 5 minutes, 83 °C / 75 °C, alerts at 90 % VRAM and 85 % RAM); pause for pipelines too; retention settings in the database; classical-ML trials grouped. M8 is not split.
- **How:** MILESTONES.md (phase-2 decisions table), SPEC Appendix B OC-3 and OC-7 to OC-10.
- **Why:** The phase-2 plan listed these as open until M8's kickoff; each was put to the owner with a recommendation.

**Labelled images: a pipeline per dataset type** (M8, DATA-5.2, ADR-0014 rule 3)
- **What:** A pipeline request names its `dataset_type`; `labelled_images` runs ingest, clean, preprocess and split, with labels from class folders and the split stratified by class.
- **How:** `label_from: folder` in ingest fills a new nullable `label` column and sets `dtype`; per-type stage orders and default overrides in the stage registry; derived stages keep their parent's `dtype`; the split takes a preprocess version for this type. Ingest's unset options are left out of the recorded config.
- **Why:** CIFAR-10 has no captions; one runtime and one split (with its leakage check) for every type. Leaving unset options out, and the hash treating null as absent, keeps every existing version ID unchanged.

**pHash thresholds for small images: clean 4 bits, split 6 bits** (M8, DATA-2.2, DATA-4.2, OC-5; the owner's choice, 2026-11-18)
- **What:** Labelled-images pipelines default to near duplicates within 4 bits and split groups within 6, instead of COCO's 8 and 12.
- **How:** Type defaults in the stage registry; a request can still override them. Measured on CIFAR-10's 60,000 pHash values by comparing every pair.
- **Why:** At 8 and 12 bits, 11 % and 53 % of matched 32-px pairs crossed classes, and single linkage chained 8,814 accepted rows of every class into one split group, all in train: `automobile` ended with 56 % in train against 81–86 % for the others. At 4 and 6 bits the cross-class shares are 1.0 % and 2.8 % and the largest group has 27 rows.

**CIFAR-10's training split: seed 1, `ds_f8e67211dd94`** (M8, DATA-4.5; the owner's choice, 2026-11-18)
- **What:** M8 trains on the seed-1 split of `cifar10`: 59,630 accepted rows, every class exactly 80 / 10 / 10, largest split group 6 rows, no leaks (job_0029).
- **How:** Split again from the 4-bit preprocess version `ds_bc4811eceb36` with `seed: 1`. No earlier split had that seed, so nothing was carried over. The two seed-0 splits (`ds_83371232f9df` from job_0027, `ds_fc9e7e4ee7a6` from job_0028) are kept, not deleted.
- **Why:** The seed-0 splits carry job_0027's assignment, made while one 8,814-row group spanned every class: `automobile` had 57 % in train against 80–83 % for the others. Carrying over is right (DATA-4.5 keeps test rows out of train across versions), so the fix is a new lineage, not moving rows. A later seed-0 split of `cifar10` would continue the skewed one.

**The train stage** (M8, RUN-1.1, RUN-1.3, RUN-1.8, ADR-0014)
- **What:** Training is a `train` job with one `train` stage, on the GPU or CPU queue by `device`; it checkpoints after every epoch and every 5 minutes, resumes from its checkpoint on retry, and reports live metrics at most once a second.
- **How:** A torch-free request and config (`stratal.train.config`) validated by the API; the stage imports PyTorch lazily. A checkpoint is a pair named by global step, `ckpt-NNNNNNNNN.safetensors` and `.json`, each written under a temporary name and renamed, the JSON last as the completion mark, then older pairs removed. Data loaders get their own generator, so making one never draws from the random state a checkpoint restores. The trained weights go to `jobs/<id>/weights/` until the registry exists. `/config/schemas` lists only pipeline stages.
- **Why:** One runtime for pipelines and training (ADR-0014 rule 1). Two files with a completion mark keep a crash from leaving a half-written pair as the newest. A loader drawing from the global random state on creation would make a mid-epoch resume diverge. Listing `train` would put it in the Start pipeline drawer.

**Unwritten VRAM manager v2 only warns** (M8, RUN-1.4; the owner left the choice to the agent, 2026-11-18)
- **What:** If `TrainingState.close` is still the stub when a GPU run ends, the stage emits a WARN and completes, instead of failing.
- **How:** The stage catches the stub's `NotImplementedError` from `VramManager.unload`. Out-of-memory handling still fails with the "owner unit not written" error, as the label stage does.
- **Why:** The run's work is done and saved by then, and the child process exits right after, which frees the card (ADR-0006 rule 1). Failing a finished run would only hide its result; the unload check proves itself once the owner writes `close()`. Long-lived processes are different: the inference service (ADR-0016) keeps one model between requests, so there an unwritten `close()` will stop it from serving rather than warn.

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
- **How:** Conventional commits with requirement IDs. Since 2026-11-15 the owner's GitHub repository is `origin`; only `main` and milestone tags are pushed, after the owner's OK.
- **Why:** "One PR per milestone" without yet needing a GitHub account, which the owner asked to be consulted about.

**Verbatim input kept**
- **What:** The owner's original spec is stored unmodified beside SPEC.md.
- **How:** Copied from the session transcript by script, not retyped; a second script compares SPEC.md against it.
- **Why:** "Nothing from my input may be dropped; flag anything you reworded" has to be checkable.
