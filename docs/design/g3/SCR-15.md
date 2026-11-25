# SCR-15 transform editor — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-15_v1.html`](../screens/SCR-15_v1.html), G2 approved 2026-11-24 |
| Built | `frontend/src/screens/transform/TransformScreen.tsx`, `frontend/src/styles/scr15.css`; route `/versions/:version_id/transform` (`?from=ds_…` loads a transform's operations). API: `POST /versions/{id}/transform/preview`, `GET /config/schemas/transform` |
| Checked | 2026-11-25, live at 1280 × 800, dark and light, on the real database: wine's raw version `ds_b3f4ff09de8a`. Flow 6 was built through the screen (drop `ash`, rename `od280/od315_of_diluted_wines` → `od280_od315`, `set_kind magnesium categorical`, `set_encoding magnesium embedding`) and run: `job_0125` made transform `ds_ec52a5ed363e` → clean_table `ds_26aa75e5a924` → split `ds_b0138df9a9f2` in 8 s |
| Status | **Proposed for the owner's approval** |

## Screenshots (in `SCR-15/`)

The browser pane captures 800 × 500 of the 1280 × 800 viewport, so the layout was also checked by measurement (below).

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-wine-four-operations-dark.jpg` | A | The four operations, opened with `?from=ds_ec52a5ed363e` ("Loaded the 4 operations"); the effect line under each card from the stage's own steps; the resulting schema with `ash` struck through in place, `magnesium` (categorical · embedding) and `od280_od315` (renamed) outlined; the preview with `magnesium` left-aligned and semibold |
| `live-B-add-operation-dark.jpg` | B | The *Add operation* menu with every type and the note on fitted operations |
| `live-run-job_0125-dark.jpg` | — | SCR-04 after *Run*: transform 3 s, clean_table 2 s, split 2 s |

**Measured:** the operations column 400 px; the header on one line (47 px); every operation select wide enough for its value; the chips in schema order; nothing below the frame; the page itself does not scroll (the operations list scrolls inside its column).

## Differences from the mockup

1. **No drag grip on the cards.** Cards move with the ⋮ menu (*Move up*, *Move down*, *Duplicate*, *Remove*) and Alt+↑ / Alt+↓; the mockup drew a grip as well. Drag would be a third way to do the same.
2. **A card's fields wrap to a second line** when a value is long (`od280/od315_of_diluted_wines`, `categorical`): the selects size to their value, as the fix for the cut-off "categorical" found here.
3. **The reason *Run* is disabled** ("Add an operation first", "2 problems") is a badge at the start of the run area, before the two choices, not beside the button.
4. **`derive` and `filter` are in the menu and the type select:** DATA-7.4 is built, so the "only once built" condition is met.
5. ***Later stages*** shows the split's four settings as number fields in two columns (the mockup: three).
6. **A newer session draft** wins over `?from=` and says so: "Your unsaved draft is shown, not the 4 operations of ds_…", with *Use the loaded list*.

## Not seen live

The wine table has no column for an ordinal order, no value that fails to convert, and no refused operation in the demo. The ordinal order list, a `cast_failed` cell, the all-rejected warning and the refused-operation state were checked through `TransformScreen.test.tsx` and the API's tests, not on the live screen.

## Accessibility

- **Contrast:** AA in both themes. Dark: 832 text elements, lowest 5.15:1. Light: 832, lowest 5.20:1 (after the `t` shortcut and 3 s).
- **Keyboard:** each card is a group named "Operation n: op"; its fields are native selects and inputs; Alt+↑ / Alt+↓ move it; the ordinal order moves with Alt+← / Alt+→. *Run* uses `aria-disabled` with its reason as its description. The preview's counts and freshness are `aria-live="polite"`.

## Tests

`frontend/src/screens/transform/TransformScreen.test.tsx` (7): `test_DATA_7_5_operations_form_generated_from_schema`, `…_column_select_lists_columns_at_that_step`, `…_refused_operation_message_on_its_field`, `…_run_posts_from_version_operations_and_until_stage`, `…_empty_list_shows_raw_rows_and_run_says_why`, `test_DATA_7_1_not_a_raw_table_points_to_its_raw_version`, `test_DATA_7_2_problems_map_to_their_operation_and_field`. API: `tests/integration/test_M11b_transform.py` (`test_DATA_7_5_the_preview_equals_the_stages_first_rows` and the rest).
