# SCR-12 chat (phase-3 slice) — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-12_v1.html`](../screens/SCR-12_v1.html), G2 approved 2026-11-27 (low-fi [`SCR-12-lofi_v1.html`](../screens/SCR-12-lofi_v1.html)) |
| Built | `frontend/src/screens/chat/ChatScreen.tsx`, `frontend/src/api/chat.ts`, `frontend/src/styles/scr12.css`; route `/chat/:sessionId?` (`?index=ds_…` opens *RAG* on that index). API: `POST /chat` (server-sent events), `GET /chat/sources`, `GET` / `DELETE /chat/sessions…` |
| Checked | 2026-11-27, live at 1280 × 800, dark and light, with the real model: Qwen3-1.7B on the GPU through `ollama`, the real `stratal-docs` indexes and coco10k's CLIP vectors |
| Status | **Proposed for the owner's approval** |

## What was run live

- **RAG** on `ds_710b60b40962` (128-token chunks): "How does the inference service share the GPU with training jobs?" streamed with a 6.7 s cold start (the LLM had unloaded after 60 s idle), first token 7.1 s, total 7.8 s, 103 tokens/s; 1 source cited of 5 given, the other four folded as "1, 3, 4, 5".
- **LLM**: a short answer in 0.82 s warm; a long essay stopped mid-stream with *Stop*, the partial answer kept and stored as *stopped*.
- **Images** on coco10k's split `ds_ade1d41f91f5`: "a red bus on a city street" gave ten red buses (similarity 0.32–0.36), 9,830 images searched; each result links to its row on SCR-03.
- The empty state, the stored conversations in the rail, resuming a conversation in its last mode and version.

## Found and fixed at this gate

1. **Stop leaked the LLM** (`a9a4578`). Starlette cancels a plain generator's stream on a disconnect without closing it, so the generation's `finally` never ran: Ollama produced all 512 tokens and the LLM stayed claimed until the next request timed out (132 s). The inference service now closes the stream on cancellation; a test covers it.
2. **A restarted `inference` left the LLM on the card** without the GPU lease, and took that card as its baseline ("card 1.8 → 1.8 GB"). It now unloads what Ollama holds at start (live: 1.8 → 0.2 GB).
3. **The stopped turn vanished** until a reload: the screen refetched before the API had stored it. The live copy now stays and the screen polls (1 s, up to 30 s) until the stored one is there.
4. **A stopped turn had no time**: the API now times it itself, since inference's `done` never comes.

## Screenshots (in `SCR-12/`)

The browser pane captures 800 × 500 of the 1280 × 800 viewport, so some frames show its left part.

| File | Mockup frame | What it shows |
|---|---|---|
| `live-B-rag-cited-and-folded-dark.jpg`, `…-light.jpg` | B | The RAG answer with its citation, the cited source, the folded uncited ones and the footer |
| `live-llm-stopped-and-answer-dark.jpg` | B (stop) | A stopped essay ("Stopped", "stopped at"), then a short answer with its timings |
| `live-images-red-bus-light.jpg` | C | Ten image results with their similarity and row keys, the footer with the images searched |

## Differences from the mockup

1. **A stop before the first token** reads "Stopped before the first token" (the mockup drew only a stop after some text).
2. **Folded sources that are not consecutive** are listed ("1, 3, 4, 5"); the mockup's example was a range ("3–5"), which is still used when they are consecutive.
3. **The footer of a stopped answer** shows "first token –" when no token came, and "stopped at" with the API's own time.

## Accessibility

- **Contrast:** AA in both themes. RAG conversation, dark: 127 text elements, lowest 5.51:1; light: 127, lowest 5.20:1. Images conversation, light: 101, lowest 5.20:1.
- **Keyboard:** `/` focuses the box; Alt+1–3 switch the mode; Enter sends and Shift+Enter adds a line; Esc or *Stop* stops; a citation is a link that moves focus to its source row; the conversation is a `log` with `aria-live="polite"`; deleting a conversation is a modal with *Cancel* focused first.

## Tests

`frontend/src/screens/chat/ChatScreen.test.tsx` (7): `test_LLM_2_1_citations_link_to_their_sources`, `test_LLM_2_1_uncited_sources_folded`, `test_LLM_2_3_abstention_shows_nearest_source_and_threshold`, `test_LLM_2_4_image_results_open_their_rows`, `test_LLM_1_1_stop_keeps_the_partial_answer`, `test_LLM_1_1_gpu_busy_names_the_job`, `test_NFR_6_footer_shows_the_timings`. Python: `tests/unit/test_LLM_1_1_stop.py`, `test_LLM_1_1_a_restarted_service_unloads_what_ollama_still_holds`, `tests/integration/test_M12_chat_api.py` (with `test_LLM_1_1_a_stopped_answer_is_stored_with_its_text_and_time`).
