# ADR-0014 — Training jobs: checkpoints, pause and resume, device choice

**Status:** Accepted by the owner on 2026-11-17, as proposed (M8). Addendum A (fine-tuning, RUN-1.10) proposed 2026-11-20.

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

## As built (2026-11-18)

- Rule 8: the move to the CPU queue happens before any training step, so it needs no checkpoint: the stage asks for a handoff (exit code 5) and the supervisor requeues it on the CPU queue. The benchmark result is cached in `cache/device_choice/` on the data volume.
- Rule 11: the `worker-cpu` image is 1.97 GB, not about 700 MB more than `api`'s 650 MB: PyTorch's CPU build also brings sympy and networkx. The download itself was 200 MB.

## Addendum A — Fine-tuning a trained version (RUN-1.10), proposed 2026-11-20

**Status:** Proposed (M11, kickoff K3 and K9). Its retention half is ADR-0015's Addendum A. Nothing here is built before the owner approves both.

### Context

- RUN-1.10: a run may start from a registered trained version (`init_from`), on the same or another split of the same dataset type, optionally with chosen layers frozen. AC1: it starts at that version's exact weights (its first validation loss equals the parent's on the same data) and registers with it as parent. AC2: frozen layers are unchanged, the others change. AC3: an output or input mismatch is refused before the run, naming it, unless `reset_head: true` re-initialises only the locked head.
- The owner's K9 (M11 kickoff): the lineage follows its key; a frozen layer stays in eval mode; the optimizer starts fresh; one validation pass before step 1 (epoch 0); tables reuse the parent's fitted preprocessing; texts need the identical vocabulary; classical models are refused; in a study `init_from` is fixed. SCR-09 v2 passed Gate 2 with this design (APPROVALS, 2026-11-20); its mockup calls `POST /jobs/validate`.
- As built so far: every run loads its lineage's base right after building the model (`_attach_lineage`); a lineage is keyed by spec identity, dataset slug, input and classes (ADR-0015 rule 1); compiled graphs name parameters `nodes.<id>.…` and `head.…` (ADR-0017 rule 8); M8's reference models are `nn.Sequential`s whose last `Linear` is the head; a table's fitted preprocessing is saved beside the weights (ADR-0019); registered weights are the last epoch's, and `metrics.val_loss` is that epoch's.

### Decision

1. **Three new fields in `TrainConfig`**, so they are part of the config and its hash (NFR-4):
   - `init_from: str | None = None`, a model version ID;
   - `reset_head: bool = False`;
   - `freeze: list[str] = []`, node IDs, stored sorted and without repeats.

   `reset_head` and `freeze` need `init_from` (freezing random weights has no use). None of the three can be tuned: a study refuses a marker on them (HPO-1.1), and a deep study with `init_from` applies the same three fields to every trial. `ClassicalConfig` gets none of them: a classical request naming `init_from` is refused with "classical models always start fresh".
2. **What can be a parent:** a `trained` version whose weights are `present`. A base, a partial version or a version whose weights are deleting or deleted is refused. Deleted weights get the existing `weights_deleted` code, with *Re-train* as the action (REG-2.7).
3. **The architecture is the parent's.** `config.model` must equal the parent's spec identity (label ignored, ADR-0017 rule 1). SCR-09 copies it; any other spec is refused.
4. **Compatibility with the chosen split** is checked by the API before a job exists (in `POST /jobs` and `POST /jobs/validate`) and again by the stage before step 1. Each rule refuses with `422 validation_error`, one entry in `errors[]` at `loc: ["config", "init_from"]` with its own `code` and a message naming both values:

   | Rule | Parent against split | `code` |
   |---|---|---|
   | Dataset type | `labelled_images`, `tabular` or `labelled_text`, equal | `init_from_type` |
   | Task | classification or regression, equal; not fixed by a reset | `init_from_task` |
   | Input | images: C×H×W equal; tables: the same columns by name, kind and role; texts: the same vocabulary (`stratal.vocab` sha256) and maximum length | `init_from_input` |
   | Output | classes equal by name and order, unless `reset_head` | `init_from_output` |
   | Spec | rule 3 | `init_from_spec` |

   A different input cannot be fixed by `reset_head`, so its message says a new model is needed.
5. **Loading.** The model is built from the spec and the seed as now; then the parent's weights are loaded with every key and shape checked. With `reset_head`, every key except the head's is loaded and the head keeps its seeded initialisation. For a graph the head is `head.*`; for a reference model, the last `Linear`. For a table, the parent's fitted preprocessing is loaded instead of fitted on this split's train rows (K9, a deliberate exception to ML-1.3's "fitted on the train split", recorded on the version). Categories this split has and the parent never saw go to the encoder's existing unknown slot.
6. **Lineage.** The key is computed as now.
   - **Same key** (same spec, dataset, input and classes): the version joins the parent's lineage and its base is untouched.
   - **Another key** (another dataset, or other classes with `reset_head`): a new lineage. Its base is the run's starting weights (the parent's, head reset if asked), saved before step 1 as any base is. Its note names the parent; its own `parent_version_id` stays null, as for every base.

   Either way the run's version records `init_from` as its parent, which may be in another lineage (REG-1.1). Without `init_from`, nothing changes: the parent is the lineage's base.
7. **Freezing.**
   - **Graph models only.** `freeze` names nodes that have parameters, plus `head`. An unknown ID, a node without parameters, `head` together with `reset_head` ("a reset head must train") or every parameterised node and the head ("nothing would train") is refused with `code: freeze_invalid` at `loc: ["config", "freeze", i]`.
   - **Reference models are not frozen.** They have no node IDs, only numbered layers, so their `freeze` must be empty (`freeze_unsupported`). They can still be fine-tuned and have their head reset. SCR-09 shows no freeze list for them. (The low-fi left this "to confirm"; this is the proposal.)
   - **What frozen means.** A frozen node's parameters have `requires_grad = False` and are left out of the optimizer, and the module is held in eval mode even while the rest trains. Its BatchNorm statistics and its dropout are therefore fixed too (AC2 checks its parameters and its buffers).
8. **The optimizer and schedule start fresh.** The registry keeps no optimizer state, and none is wanted (K9). The optimizer is built over trainable parameters only, so weight decay and Adam's moments touch only those. The schedule runs over this run's epochs.
9. **Epoch 0 (AC1).** With `init_from`, the stage evaluates the validation split once before step 1. It records the result in the history as epoch 0 and emits "Epoch 0, before training: val loss 0.7004 (the parent's on its own split: 0.7004)". With `reset_head` or another split there is nothing to compare, and the message says so. `test_RUN_1_10_*` checks AC1 on the parent's own split with no reset and no freeze: equal within 1e-4, since evaluation runs in eval mode and the parent's figure is rounded to 5 decimals.
10. **Resume.** A paused or recovered run resumes from its checkpoint, not from the parent. The parent is read only before step 1; ADR-0015's Addendum A keeps it on disk until then.
11. **Memory (ADR-0017 rule 7).** `POST /model-specs/validate` and `POST /jobs/validate` take the freeze list. Weights count for every parameter; gradients and optimizer state count only for trainable ones. Activations are counted as before. That over-counts a frozen prefix, whose activations autograd does not keep, so the estimate stays on the safe side.
12. **`POST /jobs/validate`** takes the body of `POST /jobs` and creates nothing. It returns:
    - `checks`: one entry per rule 4 row, `{rule, ok, parent, here, message}`;
    - `lineage`: `{id, name, joins}`, where `joins` is false when the run founds a lineage;
    - `memory`;
    - `trainable_parameters` and `frozen_parameters`.

    Problems come back in `checks`, not as a 422, so SCR-09 can show every line at once. `POST /jobs` still refuses with the 422 of rule 4.
13. **Studies (ADR-0018).** A deep study's base run may carry `init_from`, `reset_head` and `freeze`. Each trial loads the parent instead of its lineage's base (`_attach_trial`), and the study's best trial registers by rule 6 with the parent recorded. A classical study cannot carry them (rule 1).

### Alternatives

| Option | Why not |
|---|---|
| `init_from` on the request, outside `TrainConfig` | It changes the result, so it belongs in the config and its hash (NFR-4), like the seed |
| Take the spec from the parent and omit `model` | `POST /jobs` would accept two shapes of request; copying it keeps one shape and still lets the API refuse a changed spec |
| Refit a table's preprocessing on the new split | The weights were learnt for the parent's scaling and encoding; refitting would shift every input under them (K9) |
| Load the parent's optimizer state | Not stored (ADR-0015 keeps weights only); fresh state is the usual fine-tuning practice |
| Freeze reference models by layer index | Indices are not stable names and appear nowhere in the UI; reference models are M8's fixtures, and graph models cover the use |
| Allow a task change with `reset_head` | Regression and classification differ in loss, metrics and head width; a new model is clearer |

### Consequences

- No migration: the three fields live in the configs already stored as JSON (`job_stages.config`, `model_versions.config`). DATA_MODEL.md's note on `model_versions.parent_version_id` changes, and the base's note gains the fine-tune case.
- API.md gains the three fields, `POST /jobs/validate`, the codes `init_from_type`, `init_from_task`, `init_from_input`, `init_from_output`, `init_from_spec`, `freeze_invalid` and `freeze_unsupported`, and the epoch-0 history entry.
- `test_RUN_1_10_*`: AC1 (epoch 0 equals the parent's val loss; the parent recorded); AC2 (frozen parameters and BatchNorm buffers unchanged, the others changed); AC3 (each mismatch refused naming both values; `reset_head` re-initialises only the head); plus rule 4's table and text cases, a study with `init_from`, and a resume that does not reload the parent.
- SCR-09 v2's fine-tuning part is built and checked at Gate 3; SCR-11 v2 shows the parent and *Fine-tune…*.
