# SCR-03 version detail and lineage — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-03_v1.html`](../screens/SCR-03_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/version/`, `frontend/src/styles/{scr03,rowdrawer}.css` |
| Checked | 2026-11-16, live on coco10k (preprocess `ds_f81d0d552a1a`, split `ds_3f6fcc420a27`) and coco1k (the fork at `ds_acf73ab0758b`), at 1280 × 760 and 1024 × 640, dark and light |
| Status | **Approved by the owner on 2026-11-16**, including the lineage strip in HTML rather than React Flow |

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

# M11 — SCR-03 v2: distributions, the diff, semantic clusters, models trained on a version (Gate 3)

| | |
|---|---|
| Mockup | [`screens/SCR-03v2_v1.html`](../screens/SCR-03v2_v1.html), G2 approved 2026-11-21 |
| Built | `frontend/src/screens/version/` (`Distributions`, `CompareVersions`, `ModelsTab`; the tile, filter and chip in `VersionScreen` and `RowsPanel`; the cluster in `RowDrawer`; the end chip in `LineageStrip`), `frontend/src/styles/scr03.css` (M11 section); API `GET /versions/{id}/distributions`, `/diff?with=`, `/models`, `/clusters/{id}`, rows `semantic` and `semantic_cluster_id` |
| Checked | 2026-11-22, live at 1280 × 1000, dark and light, on the mockup's real data: coco10k's split `ds_3f6fcc420a27` (frame A), its preprocess versions `ds_f81d0d552a1a` and `ds_bad290a73446` (frame B), CIFAR-10's split `ds_f8e67211dd94` (frame D); and for frame C, coco10k labelled again by `job_0109` with the semantic clusters (`ds_859d0861ae19` → split `ds_ade1d41f91f5`): 93 clusters, 261 rows, the largest 22 |
| Status | **Approved by the owner on 2026-11-22** ("Approved"), with the eight listed differences |

## Screenshots (in `SCR-03/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `v2-live-A-funnel-dark.jpg` | A | The funnel row for row as the mockup: 10,000 → 60 `below_min_size` → 9,940 → 8 `near_duplicate` → 9,932 → 94 `clip_below_threshold` → 9,838, the split's parts 7,870 · 984 · 984 |
| `v2-live-A-columns-dark.jpg` | A | Column cards: width's five numbers as the mockup (raw 150 / 427 / 640, here 263 / 428 / 640), file size and the preprocessed long side |
| `v2-live-B-compare-dark.jpg` | B | `min_size 256 → 255` in the callout; 1 changed, 59 wording only, 9,940 the same; `000000471773.jpg` rejected → accepted |
| `v2-live-C-cluster-dark.jpg` | C | Rows filtered to the semantic clusters with their chips; the drawer on S-10's pair `000000185291.jpg` and `000000452782.jpg`: similarity 0.981, pHash 22 bits apart, one split group |
| `v2-live-D-models-dark.jpg` | D | 17 versions by lineage, "test 0.831", "study's best · computed once", folded lineages; the "→ 17 models" chip |

## Differences from the mockup

1. **Every column has its own card** with the histogram, the five numbers and the removed rows' line; the mockup grouped height, file size and preprocessed size in one table ("each has the histogram card of 2"). Bins are round numbers from the data (width 100–199, 200–299, …), not the mockup's 0–199 … 600–640.
2. **The CLIP threshold is marked with ◂ on its bin's label**, and the mark line names it: "The filter's threshold, 0.2 (OC-5), cuts the left tail: 94 rows".
3. **The removed rows' line is one sentence of figures** ("Removed · 162: median 640 px, from 150 to 640"), not a reading such as "the 60 rejected at ingest have a shorter side of 120–255 px".
4. **The Compare picker is a native select**, and the changed rows list every differing column (`decided_at_stage`, `phash`, `derived_sha256`, `derived_width`, `derived_height`), long values shortened, where the mockup grouped "derived size". "Changed, by field" groups them as content and derived size and file.
5. **The semantic tile reads "Semantic clusters · 93" with "261 rows · largest 22"**, and pressing it filters Rows like a status tile; the mockup's extra *All · In a semantic cluster* toolbar is not built, since the tile does the same.
6. **The drawer shows two members side by side** and lists the rest as links; the similarity comes from the cached CLIP vectors (0.981 for S-10's pair, which S-10 measured at 0.985 on its own encoding).
7. **The Models tab shows three versions per lineage before *Show***, and counts partial versions too (17 here, the mockup's 16).
8. **The new tabs scroll inside the fixed-height page**, as Rows does.

## Accessibility

- AA in both themes: Rows with the cluster drawer 318 text elements, Distributions 358, Compare 375, Models 156; no failures.
- A histogram is an image with every bin's two counts in its label, and its *Table* view lists them; the reason chips are buttons named by reason and count; the semantic tile is a toggle like the status tiles; the cluster chip carries its name in text.

## Tests

`frontend/src/screens/version/VersionV2.test.tsx` (4); `tests/integration/test_M11_profile.py` (3), `tests/integration/test_M11_semantic.py` (the pipeline and the cluster view); `tests/unit/test_DATA_2_3_semantic_clusters.py` (13).
