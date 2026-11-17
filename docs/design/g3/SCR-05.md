# SCR-05 labeling review — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-05_v1.html`](../screens/SCR-05_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/review/`, `frontend/src/styles/scr05.css`; SCR-03's "Start a review" dialog aligned with frame B |
| Checked | 2026-11-17, live at 1280 × 760 and 1024 × 640, dark and light: `rev_0002`, coco1k's closed review of 50; and `rev_0003`, a new open sample of 20 from demo's split `ds_a0d33211cd07`, drawn through the dialog |
| Status | **Waiting for the owner's sign-off** |

**No decisions were recorded on real data.** Review decisions are the owner's judgments, so `rev_0003` was left open with nothing decided. It can be used or ignored. Deciding, saving, a failed save and closing are covered by the tests below.

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-reviewing-dark.jpg` | A | rev_0003, item 1 of 20: the copy with its size, caption, model and revision, CLIP score against the 0.20 threshold, row, original, split and stratum, Accept / Reject, note, strip |
| `live-A-reviewing-light.jpg` | A (light) | Item 3, the same in light |
| `live-C-close-undecided.jpg` | C | "Close rev_0003 with 20 undecided?", Keep reviewing first and focused |
| `live-D-closed-summary.jpg` | D | rev_0002: 45 accepted (90.0 %), 5 rejected, each with its image, caption and note |
| `live-F-1024.jpg` | F | 1024 px: details folded behind "Details", the decision buttons kept |
| `live-E-not-found.jpg` | E | An unknown review, with its code and "Go to datasets" |

Frame B (the start dialog) was checked in SCR-03. It now has the mockup's title, the "Seed, optional" field and its interval hint, computed for the size entered: "20 gives an interval about ±13 points wide; 80 about ±7". Frame E's failed save is covered by a test.

## Differences from the mockup

1. **An empty review cannot be closed.** With nothing decided, the close dialog disables its close button and says "Decide at least one item before closing: an empty review measures nothing". The mockup has no frame for this case.
2. **The CLIP scale** spans 0 to 0.5, the report's score axis (EVAL-1.1). The threshold comes from the version's filter stage in its lineage.
3. **Extra facts:** the item's split and stratum are listed (DATA-4.7); the mockup drew a version that had not been split.
4. **The closed summary** lists every rejected item with its image, caption and note. Items are not reopened from it, because a closed review is read-only.
5. **Next item.** After a decision the screen moves to the next item while the save runs, as frame A in light shows ("Saving… then item 25"). A failed save marks its cell dashed, shows the alert with Retry, and keeps that decision pending until saved.
6. **The open item is in the URL** (`?item=24`). Without it, the screen opens on the first undecided item.

## Accessibility

- **Contrast:** every text element passes in both themes, on the open and the closed review. The light-theme rule approved at SCR-04 also covers the strip's ✔ / ✖ and the progress counts.
- **Keyboard:**
  - `a` / `r` decide.
  - `n` focuses the note; Esc leaves it.
  - `j` / `k` move between items.
  - `?` opens the shortcut help, which the "Keys" button also opens.
  - These keys are ignored while typing in the note.
  - The strip is a listbox: each cell is an option named "Item 4, rejected" (or "…, not saved").
  - The close dialog focuses "Keep reviewing".
- **Live regions:** the caption is announced politely as items change, and a failed save is an alert.
- **The one exception:** judging a caption against its image needs sight of the image. The mockup's notes say this; the image's accessible name is its item and row key.

## Also changed for this screen

- **More facts per item.** `GET /reviews/{id}` items now include the caption model and revision, the original's size and dimensions, and the copy's dimensions (API.md §3.5).
- **Response models** for the four review endpoints.

## Tests

`frontend/src/screens/review/ReviewScreen.test.tsx`, `test_LABEL_1_5_*`:
- a key saves the decision and moves on;
- a note goes with its decision;
- a failed save says so and offers Retry;
- closing with undecided items asks first;
- a closed review shows its summary, read-only.

`tests/integration/test_M5_review.py` passes against the new response models.
