# SCR-03 for corpora — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-03corpora_v1.html`](../screens/SCR-03corpora_v1.html), G2 approved 2026-11-27 (low-fi [`SCR-03corpora-lofi_v1.html`](../screens/SCR-03corpora-lofi_v1.html)) |
| Built | `frontend/src/screens/version/Corpus.tsx`, with `VersionScreen.tsx`, `RowsPanel.tsx`, `rows.ts`, `datasets/StartPipelineDrawer.tsx` and `frontend/src/styles/scr03corpus.css`. API: `GET /versions/{id}/index`; the rows filters `document` and `text`; the Row's corpus columns and an instruction pair's `chat_format`; `GET /config/defaults?dataset_type=` |
| Checked | 2026-11-27, live at 1280 × 800 and 1024 × 768, dark and light, on the real `stratal-docs` corpus: documents `ds_4a91744afd05` (36 accepted, 7 `.json` rejected), indexes `ds_710b60b40962` (2,555 chunks at 128 tokens) and `ds_fff2e02e87a5` (1,273 at 256) |
| Status | **Approved by the owner, 2026-11-27** |

## Found and fixed at this gate

1. **`GET /datasets` was a 500** once a corpus existed: its modality, *text corpus*, was not in the response's schema (SCREENS.md v5 shows it). DATA_MODEL.md's value list is updated.
2. **The Start pipeline drawer showed the image ingest fields** for a text corpus and the stages' global defaults (`max_chars 10000` instead of the corpus's 1,000,000). The drawer now has the corpus types' rows and asks for the type's own defaults.
3. **Esc left focus on the page body**; it now returns to the row, as the accessibility notes say.
4. **The selected row's text was 4.45:1 in dark**; it now uses text-secondary, as frame A draws it.
5. **The header's actions wrapped** at 1280; they now stay on one line.

## Screenshots (in `SCR-03corpora/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-index-facts-and-chunks-dark.jpg` | A | The header with *Ask this index*, *Open the retrieval report* and *Continue the pipeline*; one corpus fact per lineage node; the facts (2,555 chunks, 36 documents and 750,605 characters, 2,555 vectors and 157 from cache, 3.7 MB, bge-small-en-v1.5); the chunks with their document filter and text search |
| `live-A-cited-chunk-with-overlap-light.jpg` | A, region 5 | A chunk opened from a chat citation: "Cited as 2 · Back to the conversation", its offsets of the document, "32 shared with the previous chunk" and the overlap marked |
| `live-E-1024-dark.jpg` | E | At 1024 px: *Retrieval report* shortened with its full name as the tooltip, the facts on two rows, no horizontal scroll |

Also seen live: the documents version (frame B's table: path, title, characters, status with reason); the drawer's corpus types (frame B: ingest "one row per document" with nothing to set, `clean_text`'s `max_chars 1000000`, `chunk`'s three fields, "embedding bge-small-en-v1.5 · 5c38ec7 fixed", *Start job · to index*); `]` moving to the next chunk.

## Differences from the mockup

1. **The chunk drawer overlays the table at 1280 too.** Frame A drew it beside the table, with the Document column dropped to make room; the built drawer is SCR-03's approved row drawer, a modal over the page at every width (as frame E draws it at 1024). The Document column is still dropped while it is open.
2. **"32 shared with the previous chunk" is the chunk stage's setting**, shown when the chunk does overlap the one before; the overlap itself is marked from the two chunks' offsets. Overlap is whole sentences, so a chunk after a long table line can share nothing.
3. **The documents version's *Accepted* tile shows the share**, as for every type, not "752,552 characters" (the characters are on the index's *Documents* fact).
4. **The Python test names differ** from the mockup's list: `test_DATA_5_3_pairs_adapter_gives_the_chat_format` and `test_LLM_2_1_long_sections_split_with_overlap` (offsets are checked in `test_M12_corpus_index.py`).

## Not seen live

No instruction-pairs dataset is in the owner's data, so the pair drawer (frame C) and its chat format were checked through `VersionScreen.test.tsx` and `test_M12_pairs.py`. "Every document rejected" and the gone-citation state (frame D) are covered by tests.

## Accessibility

- **Contrast:** AA in both themes. The index page, light: 194 text elements, lowest 4.85:1; dark: 193, lowest 5.15:1 (after fix 4). The chunk drawer, light: lowest 5.35:1.
- **Keyboard:** the header's actions, the lineage nodes, the tabs, the document filter and the text search (`/`), the rows (`j` / `k`, Enter). In the drawer: *Back to the conversation*, *Previous* / *Next chunk* (`[` / `]`), Esc closing with focus back on the row. Opened from a citation, the drawer's name is "Chunk … , cited as n".

## Tests

`frontend/src/screens/version/VersionScreen.test.tsx`: `test_DATA_5_3_corpus_versions_hide_columns_models_and_train`, `test_LLM_2_1_citation_opens_the_chunk_drawer`, `test_LLM_2_1_index_actions_open_chat_and_report`, `test_DATA_5_3_corpus_cells_show_sections_reasons_and_pairs`, `test_LLM_2_1_citation_to_a_gone_index_says_so`, `test_LLM_2_1_ask_this_index_opens_chat_in_rag`; `StartPipelineDrawer.test.tsx`: `test_DATA_5_3_corpus_types_offer_their_stages`. Python: `tests/integration/test_M12_corpus_index.py` (facts and filters through the API), `tests/integration/test_M12_pairs.py` (`chat_format`, the dataset list).
