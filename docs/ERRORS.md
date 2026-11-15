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

### E-008 — The MON-2.4 GPU test was flaky: NVML counts new VRAM late on WSL

- **Date:** 2026-11-15
- **Seen:** `test_MON_2_4_nvml_and_pytorch_figures_are_recorded_side_by_side` failed 3 times in 4 runs with `assert (565.8046875 - 228.6171875) >= (512 * 0.9)`; it had passed in the M2 check.
- **Where:** `tests/gpu/test_MON_2_gpu.py`; MON-2.4
- **Cause:** On WSL, NVML reports memory from an allocation that has never been written about 100 ms late. Measured straight after `torch.empty` of 512 MB: +337 MB, then the full amount 0.1 s later. `torch.cuda.synchronize()` alone does not change this. Writing the memory does, but only once the write has run: `torch.zeros` without a sync still failed 3 times in 10, because the fill kernel is asynchronous. The test read NVML in the same instant it allocated. The sampler itself is not affected in any way that matters: it reads once a second, so it is at most one sample behind.
- **Fix:** The test allocates with `torch.zeros` and then calls `torch.cuda.synchronize()`.
- **Guard:** The test itself: 20 passes in 20 runs after the fix, and the full `check.ps1 -Gpu`.
- **Status:** verified

### E-007 — A unit test leaked a bound version ID into another test

- **Date:** 2026-11-14
- **Seen:** `test_NFR_10_every_line_is_json_with_job_and_version_id` failed in the full run with `assert 'ds_7f3a9c21b04e' is None`, though it passed alone.
- **Where:** `tests/unit/test_MON_1_events.py`; `JobContext.set_version`
- **Cause:** `set_version` binds the version ID to the logging context for the rest of the process, which is right for a job's child (one job per process) and wrong in a test process that runs many tests. A new M2 test called it and did not unbind.
- **Fix:** The test clears the logging context in a `finally`.
- **Guard:** The NFR-10 test itself: it asserts the IDs are null before any job binds them.
- **Status:** verified

### E-006 — The CPU worker would not start: `No module named 'packaging'`

- **Date:** 2026-11-14
- **Seen:** `worker-cpu` restarted in a loop on its first start; the traceback ended in `kombu/transport/redis.py: from packaging.version import Version`.
- **Where:** `pyproject.toml`; the `api` image, which `worker-cpu` and `monitor` share
- **Cause:** Celery was declared as plain `celery`. Its Redis transport needs extra packages that only the `celery[redis]` extra declares. The dev image happened to contain `packaging` through a test tool, so every test passed and only the production image failed.
- **Fix:** Depend on `celery[redis]`.
- **Guard:** None automated yet. The integration tests run in the dev image, not the image the workers use; the M2 demo on the real stack is what caught it. A smoke test that starts the stack belongs in M7's acceptance.
- **Status:** verified on the running stack
### E-005 — Exceeding VRAM does not fail; it silently spills and crawls

- **Date:** 2026-11-13
- **Seen:** In spike S-02, a process allocated 8,192 MB on the 6,140 MB card with no error. Each 256 MB step went from 0.01 s to as much as 1.15 s. NVML reported 5,792 MB used throughout.
- **Where:** M0, spike S-02; relates to BUG2, BUG3 and BUG6
- **Cause:** Under WSL2 the Windows driver satisfies requests beyond dedicated VRAM from shared system memory instead of refusing them. NVML reports only dedicated memory, so the overflow is invisible there. This is a likely contributor to the owner's original symptoms: "VRAM bloat" that monitoring did not explain, RAM contention, and jobs that degrade instead of failing.
- **Fix:** Every GPU process caps PyTorch's allocator at 0.85 of the card, which makes PyTorch raise a catchable `OutOfMemoryError` at 5.1 GB (ADR-0006, items 8 to 10). Verified in the same spike.
- **Guard:** `test_NFR_1_*` and `test_BUG2_*` in M4, when the owner implements the VRAM manager. Until then the cap exists only in the spike script.
- **Status:** cause confirmed; fix demonstrated in a spike; not yet in product code

### E-004 — Type checker could not resolve the test client

- **Date:** 2026-11-13
- **Seen:** `pyright` reported 48 errors of the form `Type of "response" is unknown` in the API tests, and pytest warned: `Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
- **Where:** M0, test dependencies
- **Cause:** The installed Starlette builds its test client on `httpx2`. With only `httpx` present it falls back at run time, but the type checker follows the `httpx2` import and finds nothing.
- **Fix:** Replaced the dev dependency `httpx` with `httpx2` (commit `fa80872`).
- **Guard:** `pyright` in strict mode is part of `scripts/check.ps1`.
- **Status:** verified

### E-003 — Docker kept using C: after the disk location was changed

- **Date:** 2026-11-13
- **Seen:** After setting `DataFolder` in Docker Desktop's `settings-store.json`, Docker started normally but still wrote to `C:\Users\…\AppData\Local\Docker\wsl\disk\docker_data.vhdx`.
- **Where:** M0, environment setup, ADR-0001
- **Cause:** `DataFolder` is the location for the Hyper-V backend's disk. For the WSL2 backend the key is `CustomWslDistroDir`. Docker's own log showed the two as `vm.resources.dataFolder` and `vm.resources.wslDataFolder`.
- **Fix:** Removed `DataFolder`, set `CustomWslDistroDir` to `D:\Docker\wsl`. The disk image had been copied there first with Docker stopped, so nothing was lost while the wrong key was in place.
- **Guard:** None automated. Verified by file timestamps (only the D: image changes) and by comparing the image and volume lists before and after: identical, 93 images and 13 volumes.
- **Status:** verified

### E-002 — Containers reject every HTTPS certificate

- **Date:** 2026-11-13
- **Seen:** `uv lock` in a container failed with `invalid peer certificate: UnknownIssuer` for `https://pypi.org/simple/alembic/`. Docker image pulls worked.
- **Where:** M0, dependency installation
- **Cause:** Norton Antivirus inspects HTTPS on the laptop. It re-signs each connection with its own root ("Norton Web/Mail Shield Root"), which Windows trusts and a Linux container does not. Image pulls go through Docker Desktop on the Windows side, which is why they were unaffected.
- **Fix:** The Dockerfile adds any certificate found in `docker/certs/` to the image's trust store and points uv, Python and Node at it. The Norton root, a public certificate exported from the Windows store, is placed there on this machine and ignored by git. Verification is never disabled.
- **Guard:** None automated; the build fails loudly if the certificate is missing on this machine. Model downloads in M4 will go through the same trust store.
- **Status:** verified. The alternative, excluding these sites from Norton's HTTPS scanning, is the owner's decision.

### E-001 — The specified GPU worker settings would never release VRAM

- **Date:** 2026-11-13
- **Seen:** Found in review, before any code. The input configured the GPU queue as `--pool=solo, concurrency=1, worker_max_tasks_per_child=1` and expected a fresh process per job.
- **Where:** kickoff review; SPEC §7.2 RUN; relates to BUG5 and BUG6
- **Cause:** The solo pool runs tasks inside the worker's own process. `max_tasks_per_child` only recycles prefork pool children, and Celery ignores it in other pools without warning. Every job would have shared one process, so weights from one could stay in VRAM for the next.
- **Fix:** The task became a supervisor that starts one child process per job (SPEC CL-01, [ADR-0003](decisions/ADR-0003-job-execution-model.md)).
- **Guard:** `test_BUG5_*` in M4
- **Status:** fixed in design; not verified until the guard test exists and passes
