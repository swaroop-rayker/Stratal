# ADR-0021 — LLM serving

**Status:** Accepted by the owner on 2026-11-27 ("Approve both"), as proposed (M12). Builds on [ADR-0006](ADR-0006-gpu-ownership-and-vram-policy.md) (the GPU lease), [ADR-0007](ADR-0007-memory-budget.md) (the memory budget, revised here) and [ADR-0016](ADR-0016-inference-service.md) (the inference service). It applies M12's decisions 1 to 4, 10 and 11, taken on 2026-11-27, and spike [S-11](../spikes/S-11-llm.md)'s measurements.

## Context

- **What is required:**
  - LLM-1.1: a 1–3 B model served locally, 4-bit, through Ollama.
  - NFR-6: the first token in under 2 s; a RAG answer in under 8 s.
  - NFR-7: no network at run time. NFR-8: models only from the allow-list, verified.
- **The model:** Qwen3-1.7B, Q4_K_M (decision 1), pinned by SHA-256 in the allow-list and pulled by `models pull` (decision 2).
- **What S-11 measured on this laptop:**
  - On the card: 1,657 MiB of VRAM loaded; first token in 25 ms; 124 tokens/s.
  - Loading: the first GPU load in a new container takes about 60 s, because CUDA compiles its kernels. With its cache kept on a volume the next container loads in 5.4 s, and a reload in a running container takes 3–5 s.
  - Unloading: the card was back at its baseline 0.8 s after an unload.
  - Memory: Ollama's own process memory is 403 MiB loaded and 717 MiB at most, during the first load. Page cache comes on top and can be reclaimed.
  - Network: at start-up Ollama tries to reach `ollama.com` unless `OLLAMA_NO_CLOUD` is set.
- **Ollama and the lease:** Ollama keeps its model in VRAM outside PyTorch and knows nothing of the lease (ADR-0006 rule 2, which already names "the Ollama adapter").
- **Memory:** the running set's limits sum to 9,024 MiB, NFR-2's 9 GB, with nothing spare (decision 10).

## Decision

### The `ollama` service

1. **A pinned image with no route out.**
   - The image is `ollama/ollama:0.40.16@sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592`, in profile `gpu`, with the GPU.
   - It is on its own Compose network, `llm`, declared `internal: true`, which has no gateway. Only `inference` joins that network. `api` and the browser never reach Ollama.
   - Environment:
     - `OLLAMA_NO_CLOUD=1`;
     - `OLLAMA_HOST=0.0.0.0:11434`;
     - `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1`;
     - `OLLAMA_CONTEXT_LENGTH=4096`;
     - `OLLAMA_KEEP_ALIVE=-1`, so Ollama never unloads on its own and `inference` decides (rule 5);
     - `CUDA_CACHE_PATH=/root/.ollama/cuda-cache` and `CUDA_CACHE_MAXSIZE=1073741824`.
   - Volumes: `stratal-ollama` (its model store and CUDA's cache, about 1.1 GB), and `stratal-data` read-only.
2. **Its model is built from the verified file.**
   - At start, `inference` checks Ollama's model list for `stratal-qwen3-1.7b:<first 12 characters of the revision>`. If the model is missing, it uploads the GGUF file from the data volume to `POST /api/blobs/sha256:<pinned hash>`; Ollama refuses a body whose hash differs. It then creates the model from that blob with `POST /api/create`.
   - The creation sets `num_ctx` 4,096 and uses the GGUF's own chat template.
   - Ollama never pulls by tag. Its registry is unreachable anyway (rule 1).
   - A new allow-list revision gives a new model name. The old one is deleted from Ollama's store once nothing refers to it.

### `inference` as Ollama's adapter (decision 3)

3. **The LLM is `inference`'s one GPU model** (ADR-0016 rule 2).
   - A chat request while a `/predict` model is on the GPU unloads that model first, verified as today. A GPU `/predict` while the LLM is loaded unloads the LLM first (rule 5).
   - The CPU models of rule 7 are not counted.
4. **The lease before a load.**
   - A request that needs the LLM takes the GPU lease without waiting (ADR-0016 rule 4). Retrieval, abstention and image search run on the CPU and need no lease (rule 7; ADR-0022).
   - If a job holds it, the request is refused with `gpu_busy`, naming the job, and nothing falls back to the CPU: on the CPU, S-11 measured 38 tokens/s and 25 s for a RAG answer.
   - With the lease, `inference` checks free VRAM on the card (NVML) against the model's size plus a 4,096-token cache, about 1.9 GB, as ADR-0006 rule 4 requires.
   - After the load it reads `/api/ps`. If `size_vram` is less than `size`, Ollama has put part of the model on the CPU. In that case the model is unloaded, and the request is refused with `vram_insufficient`.
5. **Giving the GPU back.**
   - As ADR-0016 rule 5 does for `/predict`: after 60 s without a chat request, or at once when a GPU job is waiting (`stratal:gpu:wanted`).
   - The unload is `POST /api/generate` with `keep_alive: 0`. Then the card's used memory is polled (NVML) until it is back within 64 MiB of the baseline measured when `inference` started, for up to 10 s. Only then is the lease released.
   - If the memory does not come back, the unload is tried once more. If it still fails, `inference` keeps the lease, so no job loads onto a full card, refuses chat with `llm_unavailable`, and emits an ERROR `vram_not_released` with what NVML shows.
   - The next chat pays a reload of about 3–5 s. It is recorded as a cold start (rule 8).
6. **Load and unload events (MON-3.3).**
   - Each shows the card's used memory before and after from NVML, and the time taken. "Loading LLM (cold start 3.2 s)" is an example.
   - Each says that no PyTorch figure exists for Ollama.
   - NFR-1 is checked for Ollama from NVML. llama.cpp sizes its allocations to the free memory it finds (seen in S-11's log), so the silent spill of ADR-0006's S-02 does not arise. The `size_vram` check of rule 4 catches a partial offload.

### The CPU models beside it (decision 4)

7. **Embeddings and CLIP's text tower run on the CPU, in `inference`.**
   - The models are `bge-small-en-v1.5` through sentence-transformers, and CLIP ViT-B/32's text tower (`CLIPTextModelWithProjection`).
   - Each loads from its pulled folder at first use and stays loaded. Together they take about 75 MiB of memory, and each embeds a query in about 16 ms (S-11).
   - They never take the lease, so a question is embedded while a job trains, and embedding never unloads the LLM.
   - `HF_HUB_OFFLINE=1` is set, as for every model load.

### Chat

8. **`POST /api/v1/chat` in `api`, streamed over SSE.**
   - `api` forwards the request to `inference`'s `/chat` and relays its stream. It uses the standard library's HTTP client, as it already does for `/predict`. `api` gains no ML library.
   - The request: `{session_id?, mode: "llm" | "rag", message, index_version_id?}`. RAG's part is ADR-0022.
   - The stream's events:
     - `meta` first: the route, model, device and whether this was a cold start;
     - then `token` events;
     - then `citations`, for RAG;
     - then `done`, with the time to the first token, the total time, the prompt and output tokens, and tokens/s.
     - A refusal or failure is an `error` event carrying the problem details (ADR-0011).
   - Generation settings, fixed in M12:
     - `think: false`;
     - temperature 0.3;
     - at most 512 output tokens;
     - a 4,096-token context, of which RAG's sources take at most 3,000 (ADR-0022).
   - Requests run one at a time (`OLLAMA_NUM_PARALLEL=1`). Up to 4 wait in `inference`, first in first out. A fifth is refused with `llm_busy`.
   - S-11 saw Qwen3's first RAG token arrive 1–4 s after its prompt, apparently held back by Ollama. `inference` may switch to Ollama's `raw` mode with Qwen3's chat template written out, if that removes the delay. A test then checks that the template matches the GGUF's.
9. **What is stored** (DATA_MODEL §9's outline, now specified):
   - **`chat_sessions`:** `id`, `title`, `created_at`, `updated_at`.
   - **`chat_messages`:** `id`, `session_id`, `role`, `content`, `citations` jsonb, `request_id`, `created_at`.
   - **`inference_requests`:** one row per chat, RAG or image-search request, with `id`, `kind`, `session_id`, `route`, `model` and `revision`, `device`, `cold_start`, `load_ms`, `ttft_ms`, `total_ms`, `prompt_tokens`, `output_tokens`, `tokens_per_s`, `status` (`ok`, `refused` or `failed`), `error_code`, `created_at`.
   - NFR-6's test reads its 20 measurements from `inference_requests`.
   - Each request also emits MON-3's Inference messages: "Loading LLM (cold start 3.2 s)", "Generating 128 tokens".
10. **New error codes** (ERRORS.md):
    - `llm_unavailable`: Ollama is down, timed out, or did not release VRAM; 503.
    - `llm_busy`: the queue is full; 503 with `Retry-After`.
    - `vram_insufficient`: the model would not fit on the card; 503.
    - `gpu_busy` is unchanged.

### Memory (decision 10, ADR-0007 revised)

11. **The limits, keeping the 9,024 MiB sum:**

| Service | Now | Proposed | Measured |
|---|---|---|---|
| `ollama` | — | **1,000 MiB** | 717 MiB of process memory at most (the first load); page cache is reclaimed under the limit |
| `inference` | 1,250 MiB | **1,350 MiB** | 851 MiB on the GPU in phase 2's acceptance, plus about 75 MiB for rule 7's models and the RAG index of ADR-0022 (a few MB) |
| `worker-gpu` | 3,250 MiB | **2,750 MiB** | 2.08 GiB in phase 2's acceptance, 2.2 GB labelling (S-07) |
| `worker-cpu` | 2,500 MiB | **1,900 MiB** | 753 MiB for `power.parquet`'s ingest and transform (M11b). CPU training of the Small CNN with 4 loader workers is the case to measure (1.6 GB in `worker-gpu`, job_0033) |
| the rest | 2,024 MiB | 2,024 MiB | unchanged: `postgres` 512, `redis` 256, `api` 400, `monitor` 256, `frontend` 400, `beat` 200 |

   - These limits are proposed from the measurements above. M12 measures them again before its merge: a CPU training run in `worker-cpu`, labelling in `worker-gpu`, and chat with RAG in `inference` and `ollama`.
   - If a peak comes within 10 % of its new limit, the table comes back to the owner. A sum above 9 GB would be a change to NFR-2 itself.
   - `worker-cpu`'s loader workers are derived from its limit (ADR-0007 rule 4), so a lower limit means fewer workers, not a crash.

### Tests

12. **NFR-7 and NFR-8, extended.**
    - Chat and RAG run with every outbound connection refused in `inference`.
    - A container attached to the `llm` network cannot reach an outside address.
    - Ollama's model is built only from a blob whose hash is the allow-list's.
    - Ollama's log shows no outbound attempt while `OLLAMA_NO_CLOUD` is set.
13. **BUG6, extended.** After the adapter's unload, the card is back at its baseline, and a queued training job gets the lease within one request's duration.
14. **NFR-6, as decision 11 reads it.**
    - With the GPU free and the model loaded, 19 of 20 queries meet their target: 10 chats with the first token under 2 s, 10 RAG queries with the whole answer under 8 s.
    - The first query after a load is reported apart.

## Alternatives

| Option | Why not |
|---|---|
| Ollama pulls by tag from its registry, recording the digest | A second download path to configure and check; decision 2 chose the allow-list |
| Ollama on the default Compose network | It would have a route out; `internal: true` makes "no network" a property of the network, not a promise of the image |
| Ollama falls back to the CPU while a job trains | 25 s for a RAG answer (S-11) and 1–2 GB more memory; refused in decision 3 |
| A fixed VRAM share for Ollama beside jobs | Breaks RUN-1.4's one large model and NFR-1's margin |
| Ollama's own `keep_alive` timer for unloads | It knows nothing of the lease or of a waiting job; `inference` must decide, and verify |
| No CUDA cache on a volume | Every container start pays about 60 s at the first chat |
| llama.cpp's server, or llama-cpp-python in `inference` | SPEC names Ollama (LLM-1.1); llama-cpp-python would put a native build into the `worker-gpu` image |

## Consequences

- **Chat and jobs share the card.** Chat is unavailable while a GPU job runs, and says which job. A training job waits at most for the current answer.
- **Cold starts.** The first chat after an idle minute pays 3–5 s, and the first after a container rebuild about 60 s until CUDA's cache is warm again.
- **Two copies of the model on disk:** the verified file in the data volume and Ollama's blob in its store, about 1.1 GB each.
- **Less memory for the workers.** `worker-gpu` loses 500 MiB and `worker-cpu` 600 MiB. Both are measured again in M12.
- **Phase 4.** Stable Diffusion needs the card and a large memory share. It will take the lease through the same adapter rules, and ADR-0007 will be rebalanced again then.

## As built (M12, 2026-11-27)

Built as decided, with these differences and measurements.

- **`ollama` mounts no data volume.** Rule 1 gave it `stratal-data` read-only; `inference` uploads the verified GGUF over HTTP to `POST /api/blobs/sha256:…` (rule 2), so Ollama never reads the data volume. Its only volume is `stratal-ollama`.
- **`vram_insufficient` is 409, not 503.** The code already existed (ADR-0006) as a 409 `Conflict` and is reused; `llm_unavailable` and `llm_busy` are 503, and `no_image_embeddings` (ADR-0022) is 409. `llm_busy` carries no `Retry-After` header; its suggested action says to try again in a few seconds.
- **The baseline** for rule 5's check is the card's used memory measured just before each load, not at `inference`'s start.
- **A restarted `inference` unloads what Ollama still holds** before its first load. Found live: with `ollama` left running, the LLM stayed on the card without the lease and the next load took that card as its baseline ("card 1.8 → 1.8 GB"). Now: "card 1.8 → 0.2 GB" at start (commit `a9a4578`).
- **Stop closes the generation.** Starlette cancels a plain generator's stream on a disconnect without closing it, so the adapter's `finally` (close Ollama's stream, release the slot) never ran: Ollama generated all 512 tokens and the next request waited 132 s. The stream is now an async generator that closes the chain on cancellation (`closing_stream`, `test_LLM_1_1_stop_*`).
- **The request** gained the mode `images` (`labelled_version_id`, ADR-0022 rule 12) and SCR-12's stream gained the events `session` (first, with the conversation's ID) and `images`.
- **What is stored** gained, beyond rule 9: `chat_messages.mode` (`llm`, `rag`, `images`) and `details` (sources, abstention, images, stopped, error), and `inference_requests.status` `stopped` for an answer the person stopped. A stopped answer gets no `done` from `inference`, so its total time is `api`'s own clock.
- **Ollama's `raw` mode was not needed.** Warm, a RAG answer took 2.8 s in all (34 ms of retrieval); the template stays the GGUF's.
- **Measured live** (Qwen3-1.7B Q4_K_M on the RTX 4050): first load in a new container 61 s, then 4.3–6.7 s for a reload with the kernel cache kept; warm, the first token in 21 ms at 103–124 tokens/s; after an idle minute the card returned to its baseline (1.8 → 0.2 GB) before the lease was released.
- **`ollama` gains `LLAMA_ARG_CACHE_RAM=0`** (rule 1's environment). The llama-server Ollama 0.40.16 starts keeps every finished prompt in RAM, up to 8 GiB by default, and Ollama does not set the limit: each RAG prompt added about 80 MB until the 1,000 MiB limit killed the runner after about fifteen questions (ERRORS E-023). With the prompt cache off, the runner's anonymous memory peaked at 563 MiB over 60 RAG questions in a row, and the limit stays 1,000 MiB.
- **Memory:** rule 11's limits are in `compose.yaml` (sum 9,024 MiB). Their re-measurement under load is part of M12's final check (MILESTONES).
- **NFR-6, measured** (`tests/acceptance/test_M12_llm_acceptance.py`, the 128-token index): 20 of 20 met; chat first token median 22 ms, max 44 ms; RAG answer median 510 ms, max 1.35 s. The warm-up query after a restart, reported apart: a 5.2 s load, first token at 5.4 s.
