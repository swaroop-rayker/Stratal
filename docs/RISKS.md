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
- **Result:** not run yet. WSL-level passthrough was verified on 2026-11-13.

### S-02 — What happens at the VRAM limit? (M0)

- **Question:** When a process asks for more than 6 GB, does it get an out-of-memory error or a silent slowdown? How much VRAM does the CUDA context itself use?
- **Method:** Allocate tensors in 256 MB steps until failure, timing each step and reading NVML and system RAM. Repeat with PyTorch's per-process memory fraction set.
- **Record:** the size at which an error is raised without a cap; whether allocation continues past 6 GB and how slow it gets; the gap between NVML "used" and PyTorch "reserved".
- **Decides:** the cap fraction that keeps the driver-reported peak under 5.5 GB.
- **Result:** not run yet.

### S-03 — What does sleep do to a GPU job? (M0, needs the owner at the laptop)

- **Question:** After the laptop sleeps and wakes, does a running CUDA process continue, fail, or hang? Can a new process use the GPU?
- **Method:** Run a loop that does a small GPU operation every second and logs wall-clock and monotonic time. Sleep the laptop for 2 minutes and for 20 minutes, by lid and from the Start menu, on mains power. Repeat once with hibernate.
- **Record:** whether the loop continues; any error text; whether the clocks jump; whether a fresh container sees the GPU; whether `wsl --shutdown` is needed.
- **Decides:** the recovery details in ADR-0008.
- **Result:** not run yet.

### S-04 — How slow is the Windows mount? (M0)

- **Question:** How fast can 10,000 image files be read from D: inside a container, compared with the Linux volume?
- **Method:** Generate 10,000 synthetic JPEGs on D:. Time reading and hashing them through the mount, copying them into the volume, and reading and hashing them from the volume.
- **Record:** files per second and MB per second for each; whether psutil's disk counters reflect the reads.
- **Decides:** confirms copy-at-ingest; sets a realistic ingestion throughput for ETA.
- **Result:** not run yet.

### S-05 — Which metrics are available inside containers? (M0)

- **Question:** Can every metric in MON-2.1 be read from the `monitor` container?
- **Method:** Query each NVML field, including per-process memory, and each psutil field, from a container with GPU access.
- **Record:** a table of metric, source, available or not.
- **Decides:** which columns are nullable and what the UI says for missing values.
- **Result:** not run yet. At WSL level, temperature, power, utilization and memory were readable and fan speed was not.

### S-06 — Florence-2 or BLIP? (M4)

- **Question:** Which captioner is the default, and with which caption style?
- **Method:** Caption the same 50 images with both. Measure images per second at batch sizes 4, 8 and 16 in half precision, and peak VRAM. Produce a side-by-side sheet for the owner, with mean CLIP score, and CIDEr-D if the dataset has reference captions.
- **Decides:** the owner picks the default captioner. This closes the input's open question.
- **Result:** not run yet.

### S-07 — Does the memory budget hold? (M4)

- **Question:** Do the container limits in ARCHITECTURE.md §2 leave the label stage enough room?
- **Method:** Label 2,000 images under the budgeted limits. Record each container's peak.
- **Decides:** the final limits; whether NFR-2's 9 GB stands.
- **Result:** not run yet.
