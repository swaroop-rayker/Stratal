# ADR-0014 — Training jobs: checkpoints, pause and resume, device choice

**Status:** Accepted by the owner on 2026-11-17, as proposed (M8).

## Context

- RUN-1.1 to RUN-1.9 (phase 2), NFR-4, NFR-5 (N = 10 minutes), NFR-8. The owner's M8 kickoff decisions: CIFAR-10 for the CNN; checkpoints every epoch and at least every 5 minutes; pause at 83 °C, resume below 75 °C; stall after 300 s; pause for pipeline jobs too (OC-8); CPU training in a `worker-cpu` image with an `ml-cpu` extra.
- Phase 1 already has: jobs with validated state transitions (`running → paused`, `paused → queued` and `stalled` exist but are unused), one child process per stage under a supervisor (ADR-0003), a Redis control flag that the child checks (used for cancel), heartbeats and a watchdog, the GPU lease and the 0.85 cap (ADR-0006), per-stage events, telemetry and backfill (ADR-0005).
- `api`, `worker-cpu`, `monitor` and `beat` share one image without PyTorch. Only `worker-gpu` has it.
- A training run is long, stateful and not content-addressed like a pipeline stage: resuming it needs more than a cache.

## Decision

1. **Training is a job kind, `train`, with one stage, `train`.** It reuses everything phase 1 built: claim, child process, heartbeats, events, telemetry, cancel, retry, backfill. The request names a labelled dataset version (a split one), a model, a training config (epochs, batch size, optimizer, learning rate and schedule, seed) and a device (`auto`, `cpu` or `gpu`).
2. **Models come from a factory.** `build_model(spec, input_shape, num_classes) -> torch.nn.Module`. In M8 the specs name reference models in code (`mlp`, `small_cnn`); from M9 the builder's specs compile through the same function.
3. **Labelled images come from class folders.** Ingest gains `label_from: folder`, which records the first path component as the row's `label` (CIFAR-10's `airplane/…`). Pipelines become type-specific: a labelled-images dataset runs `ingest → clean → preprocess → split`, with no caption or CLIP stages, and the split stratifies by the existing `source_folder` key, which is the class. CIFAR-10's own train/test split, kept in each file name, is recorded but not used: Stratal's split, with its leakage check, is the one every phase uses. (This is the labelled-images part of DATA-5.2, delivered early.)
4. **The input pipeline streams.** A PyTorch `DataLoader` reads the split's rows from the manifest and the preprocessed images from the blob store, decoding in worker processes whose number is derived from the container's memory limit (ADR-0007 rule 4). No tensor cache is built unless spike S-08 shows decoding is the bottleneck.
5. **Checkpoints are complete and safe.**
   - **Contents:** model, optimizer and scheduler state; epoch and position within it; the random states (Python, PyTorch CPU and CUDA); metrics so far. Tensors go in a `safetensors` file, everything else in a JSON file beside it; nothing is pickled (NFR-8).
   - **Cadence:** at the end of every epoch, and between steps whenever 5 minutes have passed since the last one (NFR-5's 10 minutes, with margin).
   - **Writing:** to a temporary name, then renamed, so a crash leaves the previous checkpoint intact. Only the latest is kept, in `jobs/<job_id>/checkpoints/` (REG-2.6).
   - **Exact resume:** each epoch's data order is a permutation seeded by (seed, epoch), so a resume replays the remaining batches in the same order. cuDNN's fastest algorithms are not deterministic, so a resumed run matches an uninterrupted one within NFR-4's tolerance, not bit for bit; deterministic mode is a config option, off by default.
6. **Pause is cooperative, for every job.** `POST /jobs/{id}/pause` sets the control flag to `pause`. A training child checks it between steps, writes a checkpoint and exits with a new exit code; the supervisor moves the job to `paused`. A pipeline stage checks it at its existing safe points (between batches) and exits the same way; its caches hold the work done (OC-8). `POST /jobs/{id}/resume` moves the job `paused → queued` and requeues the stage, which starts from the checkpoint or the caches.
7. **The watchdog takes two new actions.**
   - **Heat (RUN-1.6):** when the GPU temperature reaches 83 °C during a GPU job, it requests a pause with the reason `thermal`; when it falls below 75 °C, it resumes jobs paused for that reason, and only those.
   - **Stall (RUN-1.7):** a running stage whose last progress is older than 300 s, while its heartbeat is fresh, becomes `stalled`; progress returning moves it back to `running`. Long silent phases (model loading, hashing a version) emit progress or activity at least every minute so they are not mistaken for a hang.
8. **Device choice (RUN-1.9).** `cpu` and `gpu` are used as given. `auto` uses the GPU unless the model is small (under 1 M parameters by default): then the stage, which starts on the GPU worker, times 20 steps on each device, records both timings on the run, and continues on the faster one. If that is the CPU, the job hands itself to the CPU queue through a checkpoint. The result is cached by (model spec, input shape, batch size), so the next identical run skips the benchmark.
9. **Live metrics (RUN-1.8)** are progress events at most once a second: loss, accuracy, learning rate, gradient norm, step time, samples per second; and one event per epoch with validation loss and accuracy. VRAM and RAM come from the existing telemetry. EVAL-1.2's full metric sets replace accuracy when the owner writes them.
10. **Owner units.** VRAM manager v2 (RUN-1.4): load a model and its optimizer under the cap, unload back to baseline. Training out-of-memory (RUN-1.5): fail cleanly with a suggested batch size; training never halves its own batch, because that would change the run's results (ADR-0006 rule 5). Stubs, strict expected-failure tests and fakes come first.
11. **Images and queues (decision 3).** A new `worker-cpu` build target installs the `ml-cpu` extra: PyTorch's CPU build in M8; scikit-learn, skops and Optuna join it in M10. `api`, `monitor` and `beat` keep the image without ML libraries. The lockfile resolves the CPU and CUDA builds of PyTorch as conflicting extras, each from its own index.

## Alternatives

| Option | Why not |
|---|---|
| Pickled `torch.save` checkpoints | Unsafe to load (NFR-8); safetensors plus JSON loads no code |
| Keep the last N checkpoints | REG-2.6 keeps only the latest; NFR-14 bounds disk use to one in-progress checkpoint |
| Halve the batch on out-of-memory, as captioning does | Changes the optimisation and so the result; the owner should choose the new size |
| Decide `auto` in the API | The API has no PyTorch; a benchmark needs both devices |
| Use CIFAR-10's own train/test split | Bypasses Stratal's split, its stratification and its leakage check; comparisons with published accuracy are approximate either way |

## Consequences

- One runtime for pipelines and training: what phase 1 proved (kill tests, backfill, sleep handling) applies to training unchanged.
- A crash loses at most 5 minutes of training plus a restart.
- `worker-cpu`'s image grows by about 700 MB installed (the CPU wheel is about 200 MB).
- The `stalled` state is used for the first time; the M6 screens already have its colour and icon.
- Spike S-08 checks the numbers this ADR assumes: step time on each device, peak VRAM and RAM, checkpoint size and save time.
