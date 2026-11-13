# ADR-0006 — GPU ownership and VRAM policy

**Status:** Accepted, 2026-11-13 (kickoff answers to BUG2, BUG5, BUG6). The VRAM manager itself is owner-implemented (RUN-1.4, RUN-1.5).

## Context

- One 6 GB card. NFR-1 limits any local job to 5.5 GB. RUN-1.4 allows one large model at a time.
- BUG5: stale weights stay in VRAM after training, tests, evals and model changes. BUG6: VRAM occupancy is not tracked.
- From phase 2 a long-lived inference service wants the GPU. From phase 3 so does Ollama, which manages its own memory outside PyTorch.
- Under WSL2 it is unverified whether exceeding 6 GB raises an out-of-memory error or silently spills into system RAM and slows to a crawl.

## Decision

1. **Process exit is the unload for jobs.** Every GPU job runs in its own process ([ADR-0003](ADR-0003-job-execution-model.md)).
2. **One GPU lease.** A single cross-process lock decides who may use the GPU: a job child, the inference service, or the Ollama adapter. It is a PostgreSQL advisory lock held on a dedicated connection, so it is released automatically when its holder dies.
3. **A hard cap.** Each GPU process limits PyTorch's allocator to a fraction of VRAM, chosen so the driver-reported peak stays under 5.5 GB. Exceeding it raises PyTorch's own out-of-memory error at a predictable point.
4. **Check before loading.** A load is refused, with a clear message, if free VRAM is below the model's estimate.
5. **Handle OOM at the batch boundary.** Inference-style stages halve the batch and retry, emitting a WARN that says so. Training fails cleanly with a suggested batch size.
6. **Verify every unload in long-lived processes.** After unloading, allocated VRAM must be back at baseline. If not, the process logs what is left and restarts itself.
7. **Report both views.** Load and unload events show VRAM before and after (MON-3.3), from NVML and from PyTorch.

## Alternatives

| Option | Why not |
|---|---|
| Unload in-process only (`del`, garbage collect, empty cache) | One surviving reference keeps the memory. This is the failure behind BUG5 |
| Trust the driver at the limit | Behaviour under WSL2 is unknown and may be a silent slowdown instead of an error |
| A Redis lock with a time-to-live | It can expire while its holder is merely slow or suspended, leaving two holders |
| Several models on the GPU at once | Needs exact accounting on a card where one model already leaves little room |

## Consequences

- VRAM behaviour is deterministic and explainable from the event log.
- Every job pays a model load. Models are small enough that this is seconds.
- While a training job holds the lease, GPU inference is unavailable. Phase 2 must decide whether requests wait, fail fast, or fall back to CPU.
- The cap fraction must be measured, because PyTorch's allocator does not account for the CUDA context's own memory (spike S-02).
