# ADR-0016 — Inference service and the GPU it shares with training

**Status:** Accepted by the owner on 2026-11-17, as proposed (M8).

## Context

- REG-1.4: a FastAPI inference endpoint for every registered model. SPEC CL-07 added an `inference` service for it, because a per-job GPU worker cannot serve requests.
- ADR-0006 left one consequence open: while a training job holds the GPU lease, GPU inference is unavailable. The owner's decision 2: fall back to the CPU for models small enough, and fail fast for the rest.
- ADR-0006 rule 6: a long-lived process verifies every unload and restarts itself if memory did not return to baseline (BUG5).
- ADR-0007: the containers' limits sum to 9 GB. The running set already uses about 9,024 MiB of limits, so `inference` needs memory taken from another service.
- The `api` image has no ML libraries and must keep none.

## Decision

1. **A separate `inference` container** built from the `worker-gpu` image (which has PyTorch with CUDA, also usable on the CPU), running a small FastAPI app on the Compose network only. The `api` service forwards `POST /api/v1/models/{version_id}/predict` to it, so clients see one API and `api` loads nothing.
2. **One model at a time.** A request for a different version unloads the current one (verified, rule 3) before loading the next from the registry's safetensors and spec (ADR-0015).
3. **Every unload is verified.** After unloading, PyTorch's allocated memory must be back at the baseline measured at start; if not, the service logs what is left and exits, and Compose restarts it.
4. **GPU or CPU per request (decision 2).**
   - If the model is on the GPU, it is used.
   - Otherwise the service tries the GPU lease without waiting. If it gets it, the model loads on the GPU under the 0.85 cap.
   - If a job holds the lease, models under a size threshold (by default 50 M parameters, about 200 MB of fp32 weights) run on the CPU; larger ones are refused with `gpu_busy`, naming the job.
   - Each response says which device served it.
5. **The service gives the GPU back.** It releases the lease after 60 s without a GPU request, and at once when a GPU job is waiting: supervisors refused by the lease set a short-lived Redis flag that the service checks. A training job therefore waits at most one request's duration, never hours.
6. **It reports what it holds** (the loaded version and device), so retention never deletes weights in use (ADR-0015, REG-2.8), and so its load and unload events show VRAM before and after (MON-3.3).
7. **Memory comes from `worker-gpu`.** The proposal is 1 GB for `inference`, taken from `worker-gpu`'s 4.5 GB (it peaked at 2.2 GB while labelling). Spike S-08 measures training's peak first; ADR-0007's table is revised from that measurement, keeping the 9 GB sum.

## Alternatives

| Option | Why not |
|---|---|
| Serve `/predict` from `api` | Puts PyTorch in the API image and lets a request load a model in the process that serves everything else |
| A Celery task per request | A process start and model load per request; latency of seconds for a tiny model |
| Requests wait for the GPU | A training run can hold it for hours (rejected by the owner, decision 2) |
| Keep the lease while any model is loaded | A queued training job could wait indefinitely behind an idle service |

## Consequences

- `/predict` keeps working during training for small models, more slowly on the CPU, and says so.
- One more long-lived GPU process. Unloads are verified, and the lease is released whenever a job needs the card.
- In phase 3 Ollama takes the lease through the same rules; in phase 4 adapters load on top of a base held by this service.
