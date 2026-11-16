# SCR-03 version detail and lineage — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-03_v1.html`](../screens/SCR-03_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/version/`, `frontend/src/styles/{scr03,rowdrawer}.css` |
| Checked | 2026-11-16, live on coco10k (preprocess `ds_f81d0d552a1a`, split `ds_3f6fcc420a27`) and coco1k (the fork at `ds_acf73ab0758b`), at 1280 × 760 and 1024 × 640, dark and light |
| Status | **Waiting for the owner's sign-off** |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `mockup-A-dark.jpg` | A | The approved mockup, for comparison |
| `live-A-dark.jpg` | A | coco10k's preprocess version: header, lineage from raw to its descendants, status tiles, rows with real thumbnails |
| `live-A-light.jpg` | A (light) | The same in light |
| `live-B-duplicate-drawer.jpg` | B | A near-duplicate opened: group `dg_8287cf7b767e`, pHash distance 8 / 8, the kept row outlined, differing hex digits marked |
| `live-C-reasons.jpg` | C | Reasons tab: reason, deciding stage, status, rows, share, bar, Show rows |
| `live-D-config.jpg` | D | Config tab: the resolved preprocess config (DATA-6.1) and identity and storage (DATA-3.2, DATA-3.4) |
| `live-E-fork.jpg` | E | coco1k: one preprocess version with two label versions, one of which forks again |
| `live-G-labelled-split.jpg` | G | coco10k's split version: captions, CLIP and split columns; "Start a review" is primary; the strip scrolls to show this version |
| `live-H-not-found.jpg` | H | An unknown version, with its code and "Go to datasets" |
| `live-I-1024.jpg` | I | 1024 px: secondary actions under More; nodes without their facts; tiles without context |
| `live-grid.jpg` | – | The grid view (the mockup has the toggle, not the grid) |

Frame F (every row rejected) does not exist on this machine. It is covered by `test_UI_2_1_every_row_rejected_shows_why_and_the_rejected_rows`: the notice with the reason and what to do, Rejected pressed, the rows asked for with `status=rejected`, and Continue disabled.

## Needs your decision

**Lineage is drawn in HTML, not React Flow.** SPEC §7.4 lists React Flow for "model builder, lineage graph, router view". The approved mockup draws lineage as a strip of linked nodes that forks into rows, and its accessibility notes call it "a list of links in order". Plain HTML gives exactly that: real links, Tab order, a sideways scroll. React Flow would draw a pannable canvas whose nodes are not links. I propose keeping React Flow for the model builder (SCR-08), where nodes are dragged and connected. React Flow is installed either way, so switching is local to `LineageStrip.tsx`.

## Differences from the mockup

1. **Labelled versions.** The table shows Reason, Caption, CLIP and, after a split, Split; the mockup's Model column is left out. The captioner appears in the header ("captions by florence-2-base"). Its revision is per row (`caption_model_revision`), so it is in the row drawer, not the header.
2. **A Split filter** appears for split versions (SCREENS.md lists it; the mockup was drawn before splits existed).
3. **Report actions.** Filter and split versions offer "Open report" when one exists, else "Build the eval report" (SCREENS.md lists both; the mockup was drawn before M5).
4. **Start a review** opens a small dialog: sample size (default 50, at most 1,000 and the accepted rows) and an optional seed. It then opens SCR-05. The mockup does not draw the dialog.
5. **Grid view.** The mockup has the Table / Grid toggle but no grid. It is cards of 256 px thumbnails with the row key and status, loaded 200 at a time with "Show more".
6. **Lineage node facts:** ingest "N accepted", clean "N duplicates", preprocess "max 768" (this version: "N copies · +867 MB"), label "N captioned", filter "N filtered", split "80 / 10 / 10".
7. **Tile context.** Filtered reads "0.9% · CLIP below 0.2" once a filter stage exists; Duplicate reads "no clean stage yet" before one.
8. **Row drawer for a row that is not a duplicate:** one large image (the copy if any, else the original). Every row also shows the original's format, pHash, caption and model, CLIP score, and split and stratum when they exist. A group of more than two lists the others by key.
9. **At 1024 px** "Start a review" stays beside More and Continue, because it is the primary action for labelled versions. Copy ID, Open job and the report fold into More, as drawn.
10. **Long chains** scroll the strip so this version is in view (the split version is the sixth node).
11. **Selected rows and pressed tiles** use `text-secondary` for their muted text: the rule you approved at SCR-02, applied here.
12. **Rows are virtualised** and load 200 at a time as you scroll. Filters, the open row, the tab and the view are all in the URL, so any state can be linked.

## Accessibility

- **Contrast**, measured on every text element of the live page with transitions off:
  - Dark and light both pass, with the row drawer open too.
  - Two near-misses (4.45:1 and 4.495:1) were fixed by the selected-row rule above.
- **Keyboard:**
  - `/` focuses the row-key filter.
  - `j` / `k` move the selected row and scroll it into view; Enter on a row opens the drawer.
  - In the drawer, `j` / `k` move to the next row, announced politely; Esc closes it and focus returns.
  - Status tiles and filter buttons are toggle buttons (`aria-pressed`) named with their count.
  - The lineage is a `nav` of links with `aria-current` on this version.
  - Disabled actions use `aria-disabled`, so they stay focusable and explain themselves.

## Tests

`frontend/src/screens/version/VersionScreen.test.tsx`: `test_DATA_3_3_*` (strip links back to raw, forks, the running child), `test_DATA_2_2_*` (pHash distance and differing digits), `test_UI_2_1_*` (every row rejected, unknown version). Rows inside the virtualised table are checked in the browser: jsdom has no layout, so the virtualiser renders none there.
