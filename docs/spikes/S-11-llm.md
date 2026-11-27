# S-11 — Which LLM, and what serving it costs

| | |
|---|---|
| Milestone | M12 (first step); decides M12 decision 1, informs decisions 10 and 11 |
| Run | 2026-11-27, RTX 4050 Laptop (6 GB), driver 621.24, Ollama 0.40.16 (`sha256:910d0d8107b5…`) |
| Scripts | `scripts/spikes/s11_llm.ps1` and `s11_llm.py` (the measurements), `s11_followup.sh` (repeated loads, memory without page cache), `s11_stream_probe.py` (what Qwen3 streams before its first token) |
| Raw results | `S-11-results.json`, `S-11-memory.json` |
| Status | **Proposed for the owner's choice of model** |

## How it was run

- **No network:** Ollama ran with `--network none`, from the GGUF files `models pull` had verified on the data volume (`ollama create` from a local `FROM` path). The client container shared its network namespace, so it could reach `127.0.0.1:11434` and nothing else.
- **The GPU to itself:** `inference` and `worker-gpu` were stopped while it ran, and started again after.
- **Settings:** `num_ctx` 4,096, temperature 0, `think: false`. The field was accepted by both models, and no thinking text came back in any chat.
- **Prompts:**
  - *Chat:* 10 short questions.
  - *RAG:* 10 prompts of 2,500–3,100 tokens, each built from 12 numbered sections of Stratal's docs plus a question, in the form RAG will use (LLM-2.1). The target was about 2,000 tokens; the real ones came out larger.
  - *Refinement:* 50 COCO captions rewritten into LABEL-1.6's schema (`subject`, `attributes`, `setting`, `style`), with Ollama's `format` constrained to that JSON schema, then checked by a Pydantic model.
  - *Routing:* 12 requests across the five routes, constrained to an enum.

## Results

| | Qwen3-1.7B Q4_K_M | SmolLM3-3B Q4_K_M |
|---|---|---|
| File | 1,056 MiB | 1,827 MiB |
| VRAM on the card, loaded (NVML) | **1,657 MiB** | 2,293 MiB |
| Short chat, time to first token (10) | median 25 ms, max 28 ms | median 27 ms, max 31 ms |
| Short chat, generation speed | **124 tokens/s** | 79 tokens/s |
| RAG answer, whole (10, ≤ 256 tokens) | median 4.1 s, max 8.5 s; **9 of 10 under 8 s** | median 4.5 s, max 5.1 s; **10 of 10 under 8 s** |
| RAG, time to first token | median 2.9 s, max 6.9 s | median 1.0 s, max 1.6 s |
| RAG answers containing a citation `[n]` | 5 of 10 | 9 of 10 (see below: mostly copied text) |
| Refinement valid against the schema | **50 of 50**, median 0.71 s | **50 of 50**, median 0.55 s |
| Routing correct (12) | **9 of 12**, 0.08 s each | 5 of 12, 0.24 s each |
| Unload: card back at its baseline | 0.8 s | 0.5 s |
| On the CPU (`num_gpu: 0`): chat speed / a RAG answer | 38 tokens/s / 25 s | 25 tokens/s / 41 s |

### What the numbers do not show

- **RAG answers:**
  - Qwen3 answers in its own words: "ADR-0016 … discusses the inference service for a registered model, noting that SPEC CL-07 added it…". But in half the answers it gives no `[n]`.
  - SmolLM3's citations are mostly a source copied out word for word under its `[1]` heading. That counts as a citation, but it isn't an answer.
- **Refinement:**
  - Qwen3 fills the fields: "A plane is flying over a small island" → `subject: plane`, `attributes: [airplane, flying, over, island]`, `setting: sky`.
  - SmolLM3 leaves `attributes` empty in most outputs and puts "flying" under `style`.
  - Both were valid every time, because the JSON schema constrains the output.
- **Routing:**
  - SmolLM3 sends almost everything to `llm`. It missed every RAG request, the image request, the caption request and both translations.
  - Qwen3's three misses are a translation, a general question and a fun fact, all sent to `rag`. That's a prompt problem M13's router can tune, since its kickoff has the 50 labelled requests.
- **Short chat:** on "What does hit@5 measure?", Qwen3's answer is right. SmolLM3 calls it "the percentage of documents retrieved within the top 5", which is not quite right.

## Loading, and the cold start

The first GPU load in a new container took **about 60 s** for both models: Qwen3 57 s, of which 32 s loading and 21 s on its first 35-token prompt. In the same container, the next loads took **3.1–3.5 s**, and the first prompt 0.13 s.

The cause is CUDA compiling its kernels the first time, and keeping them only inside that container. With `CUDA_CACHE_PATH` on a volume, a new container loaded in **5.4 s**. The cache holds 18 MB.

**For ADR-0021:**
- `ollama` keeps `CUDA_CACHE_PATH` on its volume.
- After the 60 s idle unload (decision 3), the next chat pays a reload of about 3–5 s.
- So NFR-6's first query after a load misses 2 s. Decision 11 already reports it separately.

## Memory

`memory.peak` reached the container's 4 GiB limit for both models. That figure counts page cache: `ollama create` copies the GGUF into Ollama's store, and loading reads it back. Separated with `memory.stat`, Qwen3's figures were:

| Point | Process memory (`anon`) | Page cache (`file`) |
|---|---|---|
| Started | 368 MiB | 116 MiB |
| After `ollama create` | 181 MiB | 2,136 MiB |
| First GPU load, loaded | **717 MiB** | 2,518 MiB |
| Later GPU loads, loaded | 403 MiB | 2,518 MiB |
| Unloaded | 158 MiB | 2,182 MiB |

The page cache is reclaimable. Under a lower limit the kernel drops it, and a load reads the file from disk again (the 3 s load).

**For decision 10:**
- `ollama` needs about **1 GiB** of limit, to cover the 717 MiB of the first load with a margin.
- If chat may fall back to the CPU, it needs another 1–2 GB. It doesn't under decision 3, which refuses chat while a job holds the GPU.

**`inference`'s new CPU models,** run in `inference`'s 1,250 MiB limit:
- bge-small-en-v1.5 adds 29 MiB of resident memory and CLIP's text tower 43 MiB, on top of the 787 MiB the imports already take.
- The container's peak was 1,182 MiB, page cache included.
- A question takes 16 ms to embed with bge (median), and a CLIP text query 16 ms.
- That is about 75 MiB more for `inference`, which ADR-0021 has to find inside the 9 GB sum.

## Two things seen only with Qwen3

1. **The first token arrives late in RAG.**
   - What happens: the prompt is read in 0.4–0.6 s and the whole answer takes under 3 s to generate, yet the first chunk often arrives 1–4 s after the prompt. When it does, the content comes all at once.
   - The probe shows no hidden thinking text and no empty chunks before it. So Ollama is holding the output back rather than the model generating it late; a parser waiting to see whether the output is a tool call is the likely reason.
   - It doesn't affect short chats (25 ms), and RAG's target is the whole answer under 8 s, so NFR-6 still holds.
   - To try in M12: `raw` mode with our own template, or a Modelfile template without tools. The router (M13) needs to see the whole JSON anyway.
2. **Occasional slow prompts.**
   - In 4 of 20 long prompts across the two runs, reading the prompt took 5.7–9.9 s instead of 0.4–0.6 s.
   - Those caused the one RAG answer over 8 s.
   - Not seen with SmolLM3. The cause is not known: GPU clock changes on battery-or-mains power, memory re-allocation for a new batch shape, or something else.
   - NFR-6's reading (19 of 20 under 8 s) is at risk unless M12 finds and fixes the cause.

## Network

- At start-up Ollama tries to reach `ollama.com` for model recommendations and its cloud catalogue. With no network the calls fail ("network is unreachable"), as intended.
- `OLLAMA_NO_CLOUD=1` was set for the later runs. ADR-0021 should set it, and NFR-7's test should check the log for outbound attempts.

## Recommendation

**Qwen3-1.7B (Q4_K_M).**

Why:
- It is better on what the later work needs. It routes 9 of 12 against 5, which matters for M13's router. It extracts the caption fields that LABEL-1.6 is for. It answers in its own words.
- It is cheaper to run: 1.66 GB of VRAM against 2.29 GB, 124 tokens/s against 79, and a smaller checkpoint for M13's QLoRA (about 3.5 GB against 6 GB).

What it needs in M12:
- **Citations.** The RAG prompt must ask for a citation after every claim, and LLM-2.1 checks them against the chunks given. 5 of 10 is not enough for decision 11's 90 % reading.
- **The late first token.** Try `raw` mode or a template without tools.
- **The occasional slow prompt.** Find the cause, or NFR-6's RAG reading needs revisiting.

SmolLM3-3B is steadier in RAG timing, but it routes poorly and copies its sources. If it isn't chosen, its file is deleted (`/data/models/smollm3-3b-q4km`, 1.8 GB), as planned.
