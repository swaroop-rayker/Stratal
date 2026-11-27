# ADR-0022 — Corpora and retrieval

**Status:** Accepted by the owner on 2026-11-27 ("Approve both"), as proposed (M12). Applies M12's decisions 4 to 8, 11 and 12, taken on 2026-11-27. Serving, the GPU and memory are in [ADR-0021](ADR-0021-llm-serving.md).

## Context

- **What is required:**
  - DATA-5.3: a text-corpus type and an instruction-pairs type.
  - LLM-2.1: RAG, which ingests, chunks, embeds and indexes (FAISS) a corpus, retrieves the top k, and answers with citations.
  - LLM-2.2: hit@k on a hand-labelled question set.
  - LLM-2.3: "I don't know" below a relevance threshold.
  - LLM-2.4: image search, from text through CLIP to the nearest dataset images.
- **What was decided:**
  - **The corpus** is Stratal's own documentation: about 0.7 MB of Markdown and 111,000 words, without `docs/learning/` (decision 5).
  - **The question set:** about 50 answerable and 15 unanswerable questions. Labels name sections; I draft the set and the owner checks it; it is then frozen (decision 6).
  - **An index** is a dataset version: `ingest → clean → chunk → index` (decision 7). This replaces DATA_MODEL §9's tables `corpora` and `rag_indexes`.
  - **The retrieval report** is a report kind on SCR-06 (decision 8).
- **The embedding model** is `bge-small-en-v1.5`: 384 dimensions, a 512-token input, about 16 ms per query on the CPU (S-11).
- **From S-11:** Qwen3 cited a source in only 5 of 10 RAG answers. Decision 11 reads LLM-2.1 as at least 90 % of answers whose retrieval found the labelled section citing a chunk of it, and no answer citing a chunk it was not given.

## Decision

### Two dataset types (DATA-5.3)

1. **`text_corpus`.**
   - The source is a folder of `.md` and `.txt` files at any depth. Ingest writes one row per document, with its text and its relative path as the row key.
   - Reasons for a bad row: `not_utf8`, `empty_text`, and `unsupported_format` for other files.
   - Its stages are `ingest → clean_text → chunk → index`. `clean_text` is the existing stage (NFKC, whitespace, exact duplicates), with `max_chars` raised to 1,000,000 for corpora, so no document is dropped for length.
2. **`instruction_pairs`.**
   - The source is JSONL with `instruction`, an optional `input`, and `output`, one row per line.
   - Reasons for a bad row: `missing_field`, `empty_output`, `not_json`, `text_too_long`.
   - Its stages are `ingest → clean_text → split`.
   - **The pairs' adapter** turns a row into chat messages (`user`: the instruction, then a blank line and the input if there is one; `assistant`: the output), then into the model's chat format through the GGUF's template. It is tested on a sample. M13's QLoRA uses it; M12 only builds and tests it.

### `chunk` and `index` (LLM-2.1, decision 7)

3. **`chunk` cuts at headings, then by size.**
   - A Markdown document is split at `#`, `##` and `###` headings.
   - Each piece keeps its **section path**, for example `ADR-0006 › Decision`, and its character offsets in the cleaned document.
   - A section longer than `chunk_tokens` is split at paragraph boundaries, then at sentence boundaries if needed, with `overlap_tokens` repeated between neighbours.
   - A section shorter than `min_tokens` joins the next one in the same document.
   - Sizes are counted with the embedding model's tokenizer. Defaults: `chunk_tokens` 256 (64 to 480), `overlap_tokens` 32, `min_tokens` 32.
   - Rows:
     - one per chunk, with key `<document>#<chunk number, 4 digits>`;
     - fields: `text`, `document`, `section`, `char_start`, `char_end`, `tokens`;
     - the parent is the document's row in the clean version.
   - The config (sizes and the tokenizer's model and revision) is part of the version's identity (DATA-3.2). Two chunk sizes therefore give two version IDs, as the demo needs.
4. **`index` embeds every chunk and writes one FAISS index.**
   - Embeddings: `bge-small-en-v1.5` on the CPU in `worker-cpu`, in batches of 32, normalised.
   - Each vector is a derivation (kind `text_embedding`; input hash: the chunk text's SHA-256; parameters: the model, its revision and the sentence-transformers version). Re-indexing chunks already embedded costs nothing.
   - The index is FAISS's `IndexFlatIP` over the normalised vectors, which is exact cosine search. A corpus of a few thousand chunks needs no approximate index: 2,000 chunks × 384 × 4 B is 3 MB.
   - The index is written as a blob. The manifest's metadata `stratal.rag_index` records:
     - the blob's hash, the dimension, the model and its revision;
     - the query instruction;
     - the row keys in the index's order.
   - FAISS's file holds vectors and no code, so NFR-8 holds.
   - The query instruction is bge's own prefix for short queries, `Represent this sentence for searching relevant passages: `. It is part of the index's config, so queries are always embedded the way the index expects.

### Retrieval and the answer (LLM-2.1)

5. **Retrieval runs in `inference`, on the CPU.**
   - The question is embedded with the index's instruction, then the top `k` are searched (default 5, at most 20).
   - A chunk's relevance is its cosine similarity.
   - Indexes and their chunk rows are cached by version ID, the 2 most recent kept.
6. **The prompt.**
   - The system message tells the model:
     - to answer only from the numbered sources;
     - to put `[n]` after every sentence that uses source n;
     - to say "I don't know" when the sources do not answer.
   - Each source is `[n] <document> › <section>` and its text.
   - The sources take at most 3,000 tokens (ADR-0021 rule 8). If they would exceed it, the lowest-ranked sources are dropped, never cut in the middle.
7. **Citations are checked, not trusted.**
   - Every `[n]` in the answer is mapped back to its chunk, document, section and offsets.
   - An `[n]` outside the sources given is removed from the answer and logged as a WARN, "Citation [7] refers to no source given; removed". The answer is not regenerated.
   - An answer with no valid citation, and no "I don't know", is marked `uncited` and shown as such.
   - The `citations` event carries, for each citation: `n`, the chunk's row key, `document`, `section`, `char_start`, `char_end` and `relevance`.
   - These counts are what decision 11's reading is measured on.

### Abstention (LLM-2.3)

8. **Below the threshold the LLM is not asked.**
   - If the top chunk's relevance is below the threshold, the answer is "I don't know". It gives the nearest source and its relevance, and makes no Ollama call, so no GPU is needed either.
   - **The threshold is the owner's** (decision 6). The retrieval report shows the top-1 relevance of the answerable and the unanswerable questions side by side, as OC-5 did for pHash.
   - It is stored per embedding model in the `settings` table (SCR-14).
   - An answer where the model itself says it doesn't know is counted apart from a threshold abstention.

### hit@k (LLM-2.2, decision 6)

9. **The question set is a versioned file.**
   - It lives at `evals/rag/stratal-docs-questions-v1.jsonl` in the repository, one question per line: `id`, `question`, `answerable`, and `labels`, a list of `{document, section}`.
   - Once the owner has checked it, it is frozen. A change makes `v2`, and every report names the file and its SHA-256.
10. **A hit.**
    - A retrieved chunk is a hit for a question when it comes from a labelled document and its section path equals, or sits under, a labelled section.
    - hit@k is the share of answerable questions with at least one hit in the top k.
    - Labels name sections, not chunks, so index versions with different chunk sizes are compared on the same set.
11. **The retrieval report** (decision 8).
    - A `retrieval_eval` stage, run on `worker-cpu` against one index version. Its config: the index version, the question set's path and hash, and k = 1, 5, 10.
    - It writes a report of kind `retrieval`, shown on SCR-06:
      - hit@1, hit@5 and hit@10;
      - the questions missed, with what was retrieved instead;
      - the top-1 relevance of answerable against unanswerable questions;
      - how many unanswerable questions fall below a given threshold.
    - No LLM call is made, so the report is quick and needs no GPU.
    - **Owner unit:** hit@k's scoring (rule 10) was a candidate under SPEC §9.1's "scoring, evaluation". On 2026-11-27 the owner asked the agent to write it (decision 12, "Agent writes it"); the owner reviews it like any other change.

### Image search (LLM-2.4)

12. **Text to CLIP to images, over vectors that already exist.**
    - A query's CLIP text vector is computed on the CPU in `inference` (ADR-0021 rule 7).
    - It is compared with a labelled version's `clip_image_embedding` derivations, which LABEL-1.3 wrote in M11.
    - For that version, `inference` builds an exact FAISS index (10,000 images × 512 × 4 B is 20 MB) at the first query, keeps the most recent one, and returns the 10 nearest images with their row keys and similarities. Chat shows them.
    - A version without CLIP vectors is refused with `no_image_embeddings`. Computing them is a `label` run's work, not a query's.
    - Tests: planted images found from their text. Live: "dogs at night" on coco10k, judged by the owner (decision 11).

### Screens (decision 9, the G1 revision)

13. **No new screen for corpora.**
    - Corpora, chunks and indexes are dataset versions on SCR-02 and SCR-03. SCR-03 shows a chunk's document, section and text.
    - The retrieval report is on SCR-06.
    - Chat is SCR-12's phase-3 slice: manual mode, *LLM* and *RAG*, with citations, abstentions and image results.
    - SCREENS.md v5 specifies them.

## Alternatives

| Option | Why not |
|---|---|
| Tables `corpora` and `rag_indexes` (DATA_MODEL §9) | A second kind of versioned object, with its own identity, lineage and screen; as versions they get DATA-3's identity, lineage and SCR-02/03 for free (decision 7) |
| Fixed-size chunks only | Sections are what the question set labels and what citations name; heading-first keeps a chunk inside one section |
| An approximate index (IVF, HNSW) | Exact search over a few thousand vectors takes under a millisecond; approximation would only add a recall to measure |
| LangChain's retrievers and chains | Four steps of our own (embed, search, prompt, cite) are short and testable; LangChain was left out of the plan |
| Trust the model's citations | S-11: half the answers cited nothing; an unchecked `[n]` could point at a source it was never given (LLM-2.1) |
| Ask the LLM even below the threshold | Spends the GPU and a few seconds to produce an answer we would then discard (LLM-2.3) |

## Consequences

- **Rebuilding:** a corpus rebuilds like any dataset. Editing the docs and ingesting again gives new versions, and unchanged chunks reuse their vectors.
- **Comparing index versions:** two index versions are compared on SCR-06 by hit@k with the same frozen question set.
- **Citations:** every citation traces to a chunk, a section and a source file through lineage (DATA-3.3).
- **Chat without the GPU:** retrieval and abstention need no GPU, so "I don't know" still works while a job trains. Only the generated answer waits for the card.
- **The threshold:** it is set by the owner from measured relevance, and must be chosen again if the embedding model changes.

## As built (M12, 2026-11-27)

Built as decided, with these differences and measurements.

- **`clean_text` keeps line breaks for corpora** (rule 1 said the existing stage, unchanged). The existing stage collapsed all whitespace, which removed the Markdown headings `chunk` needs. It gained `keep_line_breaks`, set for `text_corpus` only and left out of the config when off, so earlier versions keep their IDs. The owner was told when it was found.
- **The question set lives in the package**, at `src/stratal/eval/rag/stratal-docs-questions-v1.jsonl` (a resource of `stratal.eval`), not `evals/rag/` (rule 9): the `worker-cpu` image copies the package, not the repository. v1, with 50 answerable and 15 unanswerable questions, was approved by the owner on 2026-11-27 and is frozen; reports name it and its SHA-256.
- **Chunk rows** carry `token_count` (rule 3 said `tokens`, a name the labelled-text type already uses for token IDs), plus `title`, `document`, `section`, `char_start`, `char_end`.
- **`stratal.rag_index`** records the order as "accepted rows by row key" rather than listing the keys; the rows are read in that order when the index is loaded.
- **API added for SCR-03:** `GET /versions/{id}/index` (the index's facts), the rows filters `document` and `text`, and an instruction pair's `chat_format`, made by the pairs' adapter when rows are read. SCR-06 lists a corpus's reports with `GET /datasets/{slug}/reports?kind=retrieval`.
- **A dataset's modality** reads `text corpus` or `instruction pairs` (SCREENS.md v5); DATA_MODEL.md and the API's schema were updated when the dataset list failed on it.
- **Measured on Stratal's docs** (43 files, 36 documents, 7 `.json` rejected `unsupported_format`):
  - 256-token chunks: 1,273 chunks, embedded in 106 s on the CPU; hit@1 0.28, hit@5 0.76, hit@10 0.86.
  - 128-token chunks: 2,555 chunks (157 vectors from the cache); hit@1 0.38, hit@5 0.68, hit@10 0.86.
  - Top-1 relevance on the 128-token index: answerable 0.650–0.861, unanswerable 0.396–0.626, so a threshold of 0.65 separates them completely. **No threshold is set:** it is the owner's (decision 6), from SCR-06 or SCR-14.
  - Warm RAG: 2.8 s in all, 34 ms of retrieval.
  - Image search on coco10k's split (9,830 images): 6.6 s for the first query (CLIP's text tower and the index loading), 22 ms after; "a red bus on a city street" returned ten red buses.
