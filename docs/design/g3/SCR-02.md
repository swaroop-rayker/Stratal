# SCR-02 datasets and versions — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-02_v1.html`](../screens/SCR-02_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/datasets/`, `frontend/src/components/{SchemaForm,Drawer,states}.tsx`, `frontend/src/styles/{scr02,drawer}.css` |
| Checked | 2026-11-16, live on the real datasets (coco10k, coco2k, coco1k, demo, demo-m3) at 1280 × 760 and 1024 × 640, dark and light |
| Status | **Approved by the owner on 2026-11-16** ("Approved, fix selected row"); the selected row's sub-lines now use `text-secondary` |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `mockup-A-dark.jpg` | A | The approved mockup, for comparison |
| `live-A-dark.jpg` | A | coco10k selected: facts, status key, six versions newest first with counts, new files and creating job |
| `live-A-light.jpg` | A (light) | The same in light |
| `live-A-row-menu.jpg` | A, region 8 | The row menu on coco2k's filter version |
| `live-H-1024.jpg` | H | 1024 px: rail, narrower list, no Parent column, "Ingest", "6 versions · split" |
| `live-F-drawer-folder.jpg` | F | Start pipeline from a folder: `/sources/coco1k · 1,000 files`, slug filled from the folder, preprocess fields from its schema |
| `live-G-drawer-version-error.jpg` | G | From `ds_3c8ee080a4c9` (clean): earlier stages "already in this version", `max_side` 20 → "must be at least 32", Start disabled |

Frames B (no datasets), C (a first ingest still running), D (loading) and E (versions cannot be loaded) cannot be produced on this machine without deleting data or stopping the data volume. They are covered by `frontend/src/screens/datasets/DatasetsScreen.test.tsx` instead: the empty state with its button, the pending row with stage, parent, step and job, and the error with its code, action and Retry.

**Run through the drawer:** from coco2k's filter version, "Continue the pipeline from here" → Start job made `job_0016` (split, then evaluate), which completed with `ds_cbd23ad6dbff`, coco2k's first split.

## Differences from the mockup

1. **All seven stages are listed** in the drawer. The mockup was drawn in M3, when label, filter, split and evaluate showed as "not built yet".
2. **Hidden settings.** Fields the system sets are not offered: the `algorithm_version` fields and the split's `continues_version_id`, marked `readOnly` in their JSON Schema. The other fields of each stage appear in schema order (the mockup drew only two for preprocess).
3. **Settings summaries.** A folded stage shows one setting: `min_size`, `max distance` (`phash_max_distance`), `max_side`, `batch` (`caption_batch_size`), `threshold`, `seed`, `confidence`.
4. **No `POST /datasets` before the job.** The drawer sends only `POST /jobs`, which creates a new dataset itself. Creating it first would leave an empty dataset behind whenever the job is refused.
5. **The first stage that would run cannot be unticked**: a job runs at least one stage.
6. **The dataset field** says "Exists: the new versions are added to it" for a slug that exists. The mockup drew only the "New" case.
7. **Error wording.** ajv's range errors are reworded as in the mockup ("must be at least 32"). Server errors land on their fields from `errors[].loc`, with the summary below.
8. **Versions table "Made"** uses the date ("Nov 15"), not "yesterday", so the column keeps its width. The dataset list keeps "yesterday", as drawn.
9. **Byte sizes** have at most three significant digits ("+867 MB").
10. **Table density** follows this mockup's own table (cells 8 px apart), not the preview's 12 px.

## Accessibility

- **Contrast**, measured on every text element of the live page in both themes with transitions off:
  - Light: no failures.
  - Dark: **one failure, as in the approved mockup.** In the *selected* dataset row, the grey sub-lines ("6 versions · latest split", the time) are `text-muted` on the accent-soft selection: **4.45:1**, just under 4.5. Proposed: `text-secondary` for the sub-lines of the selected row only. Needs the owner's decision.
  - The drawer's fields, helper text and error passed in both themes.
- **Keyboard:**
  - `/` focuses the filter.
  - The dataset list is one Tab stop; ↑ ↓ move within it.
  - `j` / `k` move the selected version row and focus it; Enter opens it.
  - Each row's menu is a button, opened with Enter or Space, its items chosen with the arrow keys.
  - Tab order: Ingest a folder → filter → list → Copy slug → versions → row menus.
  - The drawer is a modal dialog: focus moves in and is trapped; Esc closes it and focus returns to the opener.
- **Never colour alone:** the stacked bar has a text alternative ("9,838 accepted, 60 rejected, 8 duplicate, 94 filtered") and the four numbers beside it. The creating job's state icon carries its word for screen readers.

## Also changed for this screen

- **Response models** for every §3.2 endpoint, so the screen's data is typed from OpenAPI.
- **Live stream:** it now sends a comment line as soon as it opens, so the strip goes "live" at once instead of after the first event or ping. This also improves SCR-01.

## Tests

`frontend/src/screens/datasets/DatasetsScreen.test.tsx`: `test_UI_2_1_*`, `test_DATA_3_4_*`.

---

# *Start pipeline* drawer, type first — Gate 3 comparison (M11b)

| | |
|---|---|
| Mockup | [`screens/SCR-02drawer_v1.html`](../screens/SCR-02drawer_v1.html), G2 approved 2026-11-24 |
| Built | `frontend/src/screens/datasets/StartPipelineDrawer.tsx`, `frontend/src/styles/drawer.css` (M11b section). API: `GET /dataset-types` (`stages`, `source`), `GET /sources` (`columns`) |
| Checked | 2026-11-25, live at 1280 × 800, dark and light, from SCR-02 on `wine` with *Ingest*: `/sources/wine`, then *Tabular* |
| Status | **Proposed for the owner's approval** |

## Screenshot (in `SCR-02/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-drawer-B-wine-dark.jpg` | B | `/sources/wine` ("1 files · a table with 14 columns"), *Tabular* folded with *Change*, the dataset `wine` ("An existing dataset"), the stages, `ingest` open with `table.target` = `cultivar` chosen from the header and *table.kinds*, and *Start job · to split* |

**Checked live, by the page's state:** the four type cards and "Choose what the folder holds" before a type; after *Tabular*, the stages `ingest → transform → clean_table → split` with the stop on `split`; moving the stop to `clean_table` marks only `split` "not run" and the button reads *Start job · to clean_table*; the drawer 480 px wide.

## Differences from the mockup

1. **The disabled *Start job*** keeps its label and gives its reason to screen readers ("Choose what the folder holds", "Choose the table's target", "Nothing left to run"); the mockup showed no visible reason either.
2. **The `transform` row** has no chevron, since it has no settings; its switch (from a raw version) or *Stop after ingest* (from a folder) sits where the stop radio would be.
3. ***table.kinds*** is a "Set a column's kind…" select that then offers *categorical* or *numeric* as two buttons, and lists the choices as tags.
4. **Labelled images' and texts' ingest fields** come from the schema with only their type's fields (`min_size` and `label_from`; `text`), as the mockup's frame E says, not drawn there.

## Not seen live

The type-change notice with *Undo*, the version tab's transform toggle and "Nothing left to run" were checked through `StartPipelineDrawer.test.tsx`, not on the live screen. No job was started from the drawer during the check (Flow 6's runs came from SCR-15).

## Accessibility

- **Contrast:** 40 text elements in the drawer; dark lowest 5.15:1, light lowest 4.84:1.
- **Keyboard:** the types are one fieldset of native radios with a legend; the stops are one radio group labelled *Stages*, one "Stop after …" radio per stage; the transform switch is a native checkbox named "transform: open the transform editor".

## Tests

`frontend/src/screens/datasets/StartPipelineDrawer.test.tsx` (5): `test_DATA_5_2_table_source_sends_tabular_and_only_table_stages`, `…_moving_the_stop_changes_no_other_stage`, `…_image_source_runs_the_image_pipeline`, `test_DATA_7_1_transform_toggle_opens_the_editor`, `…_from_a_folder_transform_offers_stop_after_ingest`; `DatasetsScreen.test.tsx`'s `pipelineBody` test. API: `test_DATA_5_2_dataset_types_list_their_stages_and_sources_their_columns`.
