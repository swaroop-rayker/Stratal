# SCR-06 retrieval report and SCR-14's retrieval section — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-06retrieval_v1.html`](../screens/SCR-06retrieval_v1.html), G2 approved 2026-11-27 (low-fi [`SCR-06retrieval-lofi_v1.html`](../screens/SCR-06retrieval-lofi_v1.html)) |
| Built | `frontend/src/screens/report/RetrievalReport.tsx` (SCR-06 with `?kind=retrieval`), `frontend/src/styles/scr06retrieval.css`, the Retrieval section in `frontend/src/screens/settings/SettingsScreen.tsx`. API: `GET /versions/{id}/reports/latest?kind=retrieval`, `GET /datasets/{slug}/reports?kind=retrieval`, `GET` / `PUT /settings` |
| Checked | 2026-11-27, live at 1280 × 800, dark and light, on the two real retrieval reports of `stratal-docs` (`ds_710b60b40962`: hit@1 38 %, hit@5 68 %, hit@10 86 %; `ds_fff2e02e87a5`: 28 %, 76 %, 86 %) |
| Status | **Approved by the owner, 2026-11-27** |

## Screenshots (in `SCR-06retrieval/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-128-token-index-dark.jpg` | A | The header, the question set, the hit@k tiles, the other index version with the best hit@5 semibold, the "no threshold" callout, the two histograms and the threshold table |
| `live-A-threshold-and-missed-light.jpg` | A | The chart and table, then the missed questions with their labelled sections and the top 3 retrieved |

Also seen live: the confirmation of frame B ("Set the abstention threshold to 0.65?", 50 of 50 still answered, the same as now, and 15 of 15 abstaining, 15 more), closed with *Cancel*; SCR-14's Retrieval section (frame B, region 7) with the threshold empty.

**No threshold was set.** It is the owner's to choose (M12 decision 6); on the 128-token index, 0.65 separates the two groups completely (answerable top-1 relevance 0.650–0.861, unanswerable 0.396–0.626).

## Differences from the mockup

1. **The question set is named, not given as a path**: `stratal-docs-questions-v1`, a resource of the `stratal.eval` package rather than `evals/rag/…jsonl` (to be recorded in ADR-0022's As built).
2. **No duration** ("14 s"): the report's body does not record it; its job does, linked from the header.
3. **"set from this report"** without the date: the settings keep the report's index, not when the threshold was chosen.
4. **Missed questions** show the first six and a *N more* button that opens the rest in place (the mockup: a "4 more" row); each retrieved chunk links to its row on SCR-03.
5. **The threshold table** lists all eleven thresholds, 0.30 to 0.80, in a box that scrolls.

## Accessibility

- **Contrast:** AA in both themes. Dark: 202 text elements, lowest 5.63:1. Light: 202, lowest 5.20:1.
- **Never colour alone:** answerable bars are filled, unanswerable ones hatched; the current threshold row says *current*; the best hit@5 is semibold.
- **Charts have tables:** the histogram is an image whose label points to the table of bins that follows it (both groups' counts per bin).
- **Keyboard:** the header's actions, the other index versions (rows focusable, Enter opens one), each *Use* a button named "Use 0.60"; the confirmation takes focus and gives it back to its *Use* button on Esc (checked live).

## Tests

`frontend/src/screens/report/ReportScreen.test.tsx`: `test_LLM_2_2_retrieval_report_shows_hit_at_k`, `test_LLM_2_2_other_index_versions_listed`, `test_LLM_2_3_use_threshold_confirms_with_counts`, `test_LLM_2_3_no_threshold_says_rag_never_abstains`; `SettingsScreen.test.tsx`: `test_LLM_2_3_retrieval_section_saves_threshold`. Python: `tests/unit/test_LLM_2_2_hit_at_k.py`, `tests/integration/test_M12_retrieval_report.py`.
