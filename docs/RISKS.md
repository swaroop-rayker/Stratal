# Stratal — Risks and Spike Plans

| | |
|---|---|
| Status | Kickoff baseline, 2026-11-13 |
| Use | Reviewed at every milestone. A spike's result is written here, and any ADR it affects is updated in the same change |

A **spike** is a short, throwaway experiment that answers one question before design or code depends on the answer.

## Risks

Likelihood and impact are High, Medium or Low.

| ID | Risk | L | I | Plan | Spike |
|---|---|---|---|---|---|
| R-01 | The GPU is visible in WSL but not usable from Docker containers | L | H | Verify on day 1. Fallback: Docker Engine inside Ubuntu with the NVIDIA Container Toolkit | S-01 |
| R-02 | At the 6 GB limit, WSL2 spills into system RAM and crawls instead of raising an error | M | H | Cap PyTorch's allocator below the limit so its own error fires first ([ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md)) | S-02 |
| R-03 | Sleep leaves a GPU job dead or the GPU unreachable until WSL restarts | M | H | Keep-awake, sleep events, per-batch commits, resume in a fresh process ([ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md)) | S-03 |
| R-04 | Memory: 15.2 GB total, and the owner's usual applications already use most of it | H | H | 10 GB VM cap, 9 GB budget, per-container limits ([ADR-0007](decisions/ADR-0007-memory-budget.md)). Heavy jobs need other applications closed | S-07 |
| R-05 | Disk: C: is nearly full, and Docker's disk image grows but does not shrink when data is deleted | H | M | Move the image to D: in M0. Retention and garbage collection limit growth. Document how to compact the image | – |
| R-06 | Reading the source folder from a Windows drive is slow | M | L | Read it once, at ingest, and copy into Linux storage ([ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)) | S-04 |
| R-07 | Some metrics are unavailable inside containers (per-process VRAM, disk counters for Windows mounts) | M | L | Nullable columns; the UI shows "unavailable"; PyTorch figures come from the job itself | S-05 |
| R-08 | The native Florence-2 checkpoints caption worse than the originals, or need a library version that conflicts with something else | L | M | Compare before any large run; BLIP is the alternative ([ADR-0009](decisions/ADR-0009-florence-2-native-transformers.md)) | S-06 |
| R-09 | CLIP reads at most 77 tokens of text, so long captions are scored on their beginning only | M | M | The owner chooses the caption style and scoring approach in M4 with this in view | S-06 |
| R-10 | Scope: phase 2 holds 58 of 111 requirements, with one developer who also implements the core logic | H | H | P0 first; phase 2 split into four milestones; each phase re-planned at its kickoff; P2 items are the first to cut | – |
| R-11 | The owner's implementation pace blocks the agent's work | M | M | Stubs, tests and fakes prepared ahead; milestones list owner work up front ([ADR-0012](decisions/ADR-0012-working-model-and-git-workflow.md)) | – |
| R-12 | Design gates stall the UI milestone while waiting for approvals | M | M | Start Gate 0 alongside M1; the backend does not depend on the UI | – |
| R-13 | A long task is delivered twice by the Redis broker, or a queued task is lost | M | M | Acknowledge on receipt, idempotent claim, watchdog recovery ([ADR-0003](decisions/ADR-0003-job-execution-model.md)) | – |
| R-14 | Monitoring costs more than NFR-11 allows, or its tables grow without bound | L | M | Measure in M7; downsample after 7 days (NFR-12) | – |
| R-15 | A library has no working build for the chosen Python or CUDA version | L | M | Everything runs in Linux containers; versions are locked; each phase adds its dependencies behind a check | – |
| R-16 | The phase-1 dataset's license or size turns out unsuitable | L | M | Decide before M4. Tests use synthetic images and need no download | – |
| R-17 | Sustained load overheats the laptop and throttles the GPU | M | L | Temperature is recorded from M2. Pausing on temperature is RUN-1.6 in phase 2 | – |
| R-18 | Ollama manages its own VRAM and ignores the GPU lease | M | M | An adapter takes the lease on Ollama's behalf and forces an unload before releasing it. Designed in phase 3 | – |
| R-19 | Keep-awake cannot stop a lid-close or power-button sleep | H | L | Accepted. Recovery is the path that must always work | S-03 |
| R-20 | A model or dataset needs an account, key or license acceptance | L | L | Checked before each first download; the owner is asked before anything requires one | – |

## Spike plans

### S-01 — Is the GPU usable from a container? (M0, first)

- **Question:** Can a container see the RTX 4050, and does PyTorch report CUDA available?
- **Method:** Run `nvidia-smi` in a minimal container with GPU access. Then, in the worker image, check `torch.cuda.is_available()` and run a small matrix multiplication on the GPU.
- **Record:** driver and CUDA versions as seen in the container; time to first CUDA call.
- **If it fails:** check Docker Desktop's WSL integration and GPU support; fall back to Docker Engine inside Ubuntu.
- **Result (2026-11-13): passed.** `nvidia-smi` in a plain `ubuntu:24.04` container reports the RTX 4050 with driver 621.24 and 6,141 MiB. In the worker image, PyTorch 2.15.0 (CUDA 13.2 build) reports CUDA available and completes a 4096×4096 matrix multiplication on the GPU. The first CUDA operation takes about 4 seconds after process start. Docker Desktop ships the NVIDIA runtime, so no toolkit install was needed. Risk R-01 is closed.

### S-02 — What happens at the VRAM limit? (M0)

- **Question:** When a process asks for more than 6 GB, does it get an out-of-memory error or a silent slowdown? How much VRAM does the CUDA context itself use?
- **Method:** Allocate tensors in 256 MB steps until failure, timing each step and reading NVML and system RAM. Repeat with PyTorch's per-process memory fraction set.
- **Record:** the size at which an error is raised without a cap; whether allocation continues past 6 GB and how slow it gets; the gap between NVML "used" and PyTorch "reserved".
- **Decides:** the cap fraction that keeps the driver-reported peak under 5.5 GB.
- **Result (2026-11-13): the risk is real, and the cap fixes it.** Run with `scripts/spikes/s02_vram_limit.py`.

  | | No cap | Cap at 0.85 |
  |---|---|---|
  | Outcome | No error. Reached the script's 8 GB stop | `OutOfMemoryError` raised by PyTorch |
  | Held when it stopped | 8,192 MB on a 6,140 MB card | 5,120 MB |
  | NVML "used" at peak | 5,792 MB, flat from there on | 5,216 MB |
  | Time per 256 MB step | up to 1.15 s once past the card's size | 0.01 s |
  | RAM available inside the VM | unchanged | unchanged |

  What this shows:

  1. **Without a cap there is no out-of-memory error under WSL2.** PyTorch was given 2 GB more than the card has. The driver put the excess in shared system memory on the Windows side, and each step became 30 to 100 times slower. A real job would not crash; it would crawl, and take Windows' RAM while doing so.
  2. **NVML cannot see the spill.** Its "used" figure stops at about 5.8 GB while PyTorch's reserved figure keeps climbing. A VRAM chart based on NVML alone would look healthy. The spill is visible only as PyTorch reserved exceeding the card's size.
  3. **The spilled memory is not the Linux VM's.** RAM available inside the VM did not move, so the 10 GB WSL cap does not contain it.
  4. **With the cap, the failure is a normal exception** at a known point, with every step still fast.
  5. The CUDA context costs about 85 to 95 MB by itself.

  **Decisions:** the default cap is 0.85 of the card (about 5.2 GB for PyTorch, about 5.2 GiB as NVML sees it), which keeps NFR-1 with roughly 400 MB of room for library workspaces. Every GPU process must set it before its first allocation. NFR-1 must be checked from PyTorch's reserved figure plus the context, not from NVML alone. The watchdog should alert when reserved exceeds the card's size. These are recorded in [ADR-0006](decisions/ADR-0006-gpu-ownership-and-vram-policy.md). Risk R-02 is confirmed and mitigated; the mitigation is owner-implemented in M4.

### S-03 — What does sleep do to a GPU job? (M0, needs the owner at the laptop)

- **Question:** After the laptop sleeps and wakes, does a running CUDA process continue, fail, or hang? Can a new process use the GPU?
- **Method:** Run a loop that does a small GPU operation every second and logs wall-clock and monotonic time. Sleep the laptop for 2 minutes and for 20 minutes, by lid and from the Start menu, on mains power. Repeat once with hibernate.
- **Record:** whether the loop continues; any error text; whether the clocks jump; whether a fresh container sees the GPU; whether `wsl --shutdown` is needed.
- **Decides:** the recovery details in ADR-0008.
- **Result (2026-11-14): a running GPU job survived sleep.** Run once: Modern Standby entered through the Windows API, on mains power, asleep for 705 seconds, with `scripts/spikes/s03_sleep_probe.py` doing a GPU operation every second.

  | Observation | Result |
  |---|---|
  | The running process after wake | Continued on its next tick. No error; GPU operations still correct |
  | Wall clock across the sleep | Jumped by 704.6 s |
  | Monotonic clock across the sleep | Advanced by 1.0 s: it does not count the time asleep |
  | A new process after wake | CUDA available and working |
  | `nvidia-smi` in a new container | Working |
  | `wsl --shutdown` needed | No |
  | A wake timer set for 120 s | Did not wake the laptop. It stayed asleep until woken by hand |

  **Conclusions:**

  1. The feared failure (a dead CUDA context after resume) did not happen in this run. The job simply paused and resumed.
  2. **A sleep is detectable from inside the job** as wall-clock time jumping while monotonic time does not. That gap is the detection rule for [ADR-0008](decisions/ADR-0008-sleep-and-suspend-handling.md).
  3. Anything timed with the wall clock (step time, throughput, ETA, heartbeat age) would be badly wrong across a sleep. Durations must use the monotonic clock, and the watchdog must not treat a stale heartbeat as a dead job when the whole VM was asleep.
  4. Wake timers cannot be relied on, so nothing in Stratal may assume the machine wakes itself.

  **Not covered:** hibernate, battery power, lid-close, a sleep of hours, and a sleep during a real training step with large allocations. One clean run does not prove it always survives, so the recovery path in ADR-0008 stays. Risk R-03 is reduced from "likely fatal" to "survivable in the tested case".

### S-04 — How slow is the Windows mount? (M0)

- **Question:** How fast can 10,000 image files be read from D: inside a container, compared with the Linux volume?
- **Method:** Generate 10,000 synthetic JPEGs on D:. Time reading and hashing them through the mount, copying them into the volume, and reading and hashing them from the volume.
- **Record:** files per second and MB per second for each; whether psutil's disk counters reflect the reads.
- **Decides:** confirms copy-at-ingest; sets a realistic ingestion throughput for ETA.
- **Result (2026-11-13):** run with 10,000 files of 100 KB (`scripts/spikes/s04_io_speed.py`).

  | Operation | Files/s | MB/s |
  |---|---|---|
  | Write through the Windows mount | 161 | 15.7 |
  | Read and hash through the mount | 104 | 10.2 |
  | Copy from the mount into the volume | 121 | 11.8 |
  | Read and hash from the volume | 7,886 | 770 |

  The volume figure is warm-cache, and a large image build was downloading at the same time, so treat these as rough. The gap is about 75 times, far beyond that noise, and the mount's cost is per file, not per byte. Norton's real-time scanning on the Windows side probably adds to it.

  **Conclusion:** copy-at-ingest ([ADR-0004](decisions/ADR-0004-immutable-content-addressed-storage.md)) is confirmed. Ingesting 10,000 images should take about a minute and a half to two minutes, a one-time cost, and ingestion's ETA should assume roughly 100 files per second. Nothing after ingest may read from the mount.

### S-05 — Which metrics are available inside containers? (M0)

- **Question:** Can every metric in MON-2.1 be read from the `monitor` container?
- **Method:** Query each NVML field, including per-process memory, and each psutil field, from a container with GPU access.
- **Record:** a table of metric, source, available or not.
- **Decides:** which columns are nullable and what the UI says for missing values.
- **Result (2026-11-13):** run with `scripts/spikes/s05_metrics.py` in a container with GPU access.

  | Metric | Source | In a container |
  |---|---|---|
  | CPU overall and per core (16) | psutil | available |
  | RAM used, available, total; swap | psutil | available. It is the WSL VM's view: 9,944 MB total |
  | Process RSS | psutil | available for the process itself |
  | Disk read and write counters | psutil | available. Reads through the Windows mount were not checked separately |
  | GPU utilization, memory-controller % | NVML | available |
  | VRAM used and total | NVML | available, but blind to spill into shared memory (see S-02) |
  | GPU temperature, power draw | NVML | available |
  | Fan speed | NVML | not supported on this laptop |
  | Per-process GPU memory | NVML | returned an empty list. No other GPU process was running, so this is not settled; it is treated as unavailable |

  **Conclusion:** every MON-2.1 metric is readable. The GPU columns stay nullable, fan speed is not collected, and per-process VRAM comes from each job's own PyTorch figures as planned.

### S-06 — Florence-2 or BLIP? (M4)

- **Question:** Which captioner is the default, and with which caption style?
- **Method:** Caption the same 50 images with both. Measure images per second at batch sizes 4, 8 and 16 in half precision, and peak VRAM. Produce a side-by-side sheet for the owner, with mean CLIP score, and CIDEr-D if the dataset has reference captions.
- **Decides:** the owner picks the default captioner. This closes the input's open question.
- **Result (2026-11-15):** run with `scripts/spikes/s06_captioner_compare.py` on 50 COCO 2017 images (seed 6), fp16, three beams, offline. Side-by-side sheet: [spikes/S-06-captions.md](spikes/S-06-captions.md); figures: [spikes/S-06-results.json](spikes/S-06-results.json).

  | Captioner | img/s at bs 4 / 8 / 16 | Peak reserved VRAM | Mean CLIP cosine | Mean words |
  |---|---|---|---|---|
  | Human (first COCO caption) | | | 0.302 | 10.4 |
  | Florence-2-base, `<CAPTION>` | 8.9 / 10.1 / 10.1 | 4.05 GB | 0.300 | 10.6 |
  | Florence-2-base, `<DETAILED_CAPTION>` | 4.4 / 5.6 / 6.0 | 4.05 GB | 0.309 | 44.3 |
  | BLIP-large | 7.7 / 11.9 / 13.1 | 2.61 GB | 0.295 | 12.7 |

  The CLIP cosine is measured with `clip-vit-b-32-laion2b` for this spike only; it is not the owner's LABEL-1.3 unit. CIDEr-D against the five references is not computed here, because it is the owner's evaluation code (CL-13).

  **Reading it:**
  - **Every option is far above NFR-3's 2 img/s.** At 10 img/s, Florence-2 `<CAPTION>` labels the 10k set in about 17 minutes.
  - **VRAM is within NFR-1.** Florence-2's peak at batch 16 is 4.05 GB reserved plus about 0.09 GB of context, against 5.5 GB. Most of it is the vision encoder's activations, not the 0.23 GB of fp16 weights.
  - **On CLIP, the short captions are level with the human ones** (0.295 to 0.300 against 0.302).
  - **The detailed captions score a little higher, but they run past CLIP's 77-token limit** (R-09): CLIP sees only their beginning. They also take twice as long.
  - **BLIP-large is a little faster, with a steady "there is a ..." opening and some visible mistakes** (image 4: "in the snow" for a rainy street).

  **Recommendation:** keep Florence-2-base with `<CAPTION>` as the default (SPEC LABEL-1.1, ADR-0009). Use `<DETAILED_CAPTION>` only where long captions are wanted and are scored some other way than CLIP. **Decision (owner, 2026-11-15):** Florence-2-base with `<CAPTION>`.

### S-07 — Does the memory budget hold? (M4)

- **Question:** Do the container limits in ARCHITECTURE.md §2 leave the label stage enough room?
- **Method:** Label 2,000 images under the budgeted limits. Record each container's peak.
- **Decides:** the final limits; whether NFR-2's 9 GB stands.
- **Result:** not run yet.
