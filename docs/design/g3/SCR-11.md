# SCR-11 model registry — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-11_v1.html`](../screens/SCR-11_v1.html), G2 approved 2026-11-17 |
| Built | `frontend/src/screens/models/` (`ModelsScreen`, `LineageScreen`, `VersionDrawer`, `DiskCard`, `RetentionCard`, `TryIt`, `parts`), `frontend/src/api/registry.ts`, `frontend/src/styles/scr11.css`; routes `/models`, `/models/:lineage` (`?version=` opens the drawer) |
| Checked | 2026-11-19, live at 1280 × 760 and 1024 × 640, dark and light, on the real registry: `small_cnn · cifar10` (job_0030–0032, untouched) and the demo lineage `mlp · cifar10` (job_0033 to job_0047, made for this check, with the owner's OK to delete its weights) |
| Status | **Approved by the owner on 2026-11-19** ("Approved"), with the nine listed differences |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-lineages-dark.jpg`, `live-A-lineages-light.jpg` | A | Two lineages with state counts (All 2, Trained 2, Partial 1), latest version and state, "1 partial", main metric, disk, updated |
| `live-B-lineage-and-E-try-cpu-dark.jpg` | B, E | `mlp · cifar10`: a partial (paused) version, latest, pinned, deleted and base rows; the five-part disk bar matching the disk (21.9 MB ✓); retention at 2 (override); Try it on a CIFAR-10 test image, served on the CPU with the RUN-1.4 reason |
| `live-B-lineage-light.jpg` | B (light) | The same lineage in light |
| `live-B-after-confirmed-cleanup.jpg` | B | After frame D was confirmed: N 0 (override), `mv_67259b29b212` deleted, "Last run 00:03 freed 3.1 MB from 1 version (job_0046)" |
| `live-C-deleted-version-drawer.jpg` | C | `mv_add6c4c069e3`: weights deleted by retention (job_0039), metadata kept, Re-train; config, dataset version, job, parent, config hash, val acc · loss |
| `live-D-confirm-lower-n.jpg` | D | Lowering the lineage's N from 2 to 0: 1 version, 3.1 MB, "Kept: the base, the latest, and the pinned mv_806e0196211c", type the name to confirm; Keep them focused, delete disabled |
| `live-E-refused.jpg` | E | The refusal: alert icon, `gpu_busy` badge and the reason, no tinted panel (inference restarted for a minute with a 0.1 M CPU limit, then restored) |
| `live-F-loading.jpg`, `live-F-error-light.jpg` | F | Loading skeletons; the error state for an unknown lineage (`not_found`, request ID, Retry) |
| `live-G-1024-versions.jpg`, `live-G-1024-disk-tab.jpg` | G | 1024 px: Versions / Disk and retention / Try it as tabs beside the table; Created folded into the drawer |

Frame F's empty state ("No models yet") cannot be shown live without deleting the registry; it is the shared `EmptyState` and covered by its unit test.

## Differences from the mockup

1. **The lineage list's Latest is the newest *trained* version** (REG-2.2's latest), with "· 1 partial" beside it when a run is paused. The mockup shows a partial run as the latest. The State filter keeps lineages with a version in that state (DECISIONS, "`GET /models?state=` matches any version in that state").
2. **Build a model… and Train again… are disabled**, with their reason as a tooltip: the builder (SCR-08) and run setup (SCR-09) arrive in M9 (the owner's choice for step 4).
3. **Try it** offers "choose one" (a file) and "pick one from the dataset" (a random accepted test row of the version's dataset version) as two buttons inside the drop zone, which grows to hold them; the mockup shows one line of text.
4. **Preview cleanup opens a dialog** listing every version with what would happen and why, the stale checkpoints and the bytes freed, with **Run cleanup now**; when that deletes weights, it goes through frame D's typed confirmation with the lineage name. The mockup shows the button only.
5. **The pin is shown disabled on versions whose weights are deleted** (the API refuses it: nothing to keep); the mockup shows it enabled.
6. **Partial rows** show "in its checkpoint" under Weights and the job's state ("paused") beside the label; the mockup has no partial row.
7. **Last run** says "deleted nothing" when the newest run covering the lineage freed nothing.
8. **The drawer shows Created and the version's retention reason** ("latest (REG-2.2)", "beyond keep_intermediate", …) in its key-value list.
9. **The error state** is the app's shared `ErrorState` (tinted panel, as on every other screen) rather than the mockup's untinted one.

## Accessibility

- **Contrast:** every text element passes AA, scanned with transitions off, in both themes: list 31 / 33, lineage 87 / 95, with the drawer open 109 / 117, with the preview dialog 117 / 125 (light / dark).
  **Fix applied:** in a selected row in dark, "deleted" (`text-muted`) measured 4.45:1; selected rows now use `text-secondary` for muted text, as SCR-02's selected row does (the owner's rule from SCR-02's G3).
- **Keyboard:** filter buttons, Weights, then the rows (each a tab stop; Enter opens the drawer), the pins, the cards' buttons. Closing the drawer returns focus to the row that opened it (fixed during the check: the drawer opens from the URL, so it now names its return target). The deletion dialog focuses Keep them; the delete button stays disabled, and says why, until the phrase matches exactly.
- **Names:** pins are toggles named with their version ("Pin mv_…", "Unpin mv_…") with `aria-pressed`; the disk bar is an image named with its five parts, the legend is its table view; Try it announces its result politely and the refusal is an alert.

## Found while testing

- **A settings save asked about deletions it did not cause.** `small_cnn · cifar10` had two deletions pending under N = 0 (no retention had run there since registration), so raising another lineage's N asked to confirm them. Fixed: a save asks only about versions the new settings delete and the current ones keep (`retention.caused_by`, `test_REG_2_10_a_save_asks_only_about_the_deletions_it_causes`).
- **The refusal said "the 0 M that run on the CPU"** with a 100,000-parameter limit; the limit is now printed as given ("0.1 M").
- The preview dialog's reasons did not wrap, hiding the Size column; they wrap now.

## Tests

`frontend/src/screens/models/Models.test.tsx`: `test_REG_1_2_the_three_states_are_shown_and_filterable_on_a_lineage`, `test_REG_1_2_versions_filter_by_state_and_by_weights`, `test_REG_1_2_the_lineage_list_filters_by_state_through_the_api`, `test_REG_2_5_the_pin_is_a_toggle_named_with_its_version`, `test_REG_2_11_the_disk_bar_is_named_with_its_five_parts`, `test_REG_1_4_try_it_says_which_device_served_it`, `test_REG_2_10_the_confirmation_lists_versions_and_sizes_and_needs_the_name_typed`, `test_REG_2_10_only_the_exact_phrase_confirms`.

# Task metrics (M10, EVAL-1.2) — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-11eval_v1.html`](../screens/SCR-11eval_v1.html), G2 approved 2026-11-20 (low-fi [`SCR-11eval-lofi_v1.html`](../screens/SCR-11eval-lofi_v1.html)) |
| Built | `frontend/src/screens/models/TaskMetrics.tsx`, in `VersionDrawer.tsx`; styles appended to `scr11.css`. The version view gains `study_id` (API.md §3.9) |
| Checked | 2026-11-20, live, the four frames on real versions: A the CNN study's best (`mv_a4a0fb846812`), B the diabetes regression (`mv_b46259218b0e`), D a run from before M10 (`mv_0cfa1ce88a16`), E the 78-class text model (`mv_2eb4cd656334`); dark and light. Frame C (clustering, PCA) is covered by its Vitest test: no such version is on the live stack. The pane was narrow, so content, behaviour and contrast were checked through the page |
| Status | **Approved by the owner on 2026-11-20** ("Approved, continue"), with the listed differences |

## Differences from the mockup

1. **The drawer is 560 px wide for every version**, the mockup's width for frame A (it was 460 px).
2. **"Most confused" uses the selected split** (Validation by default), as the matrix does; the mockup drew it on Test.
3. **The per-class F1 list above 20 classes** scrolls within 180 px, lowest first.

## Accessibility

- **Contrast:** every text element passes AA in both themes once the theme transition settles: frame A 204 elements (lowest 5.0:1, the *computed once* badge, dark; 5.35:1 light), E 257, B 58, D 36.
- **Keyboard:** the matrix is a grid with one cell in the tab order; arrow keys move between cells and the tooltip, a polite status, says the cell's sentence ("cat predicted as dog: 94 of 598 (15.7 %)"). *Table* swaps it for a table with row and column headers. The split toggles are pressed-button groups.
- **Never colour alone:** every cell prints its count; bars carry their figure as text.

## Tests

`frontend/src/screens/models/TaskMetrics.test.tsx`: `test_EVAL_1_2_the_right_metric_set_appears_for_each_task`, `test_EVAL_1_2_the_matrix_follows_the_split_and_arrow_keys_move_between_cells`, `test_EVAL_1_2_a_study_test_is_marked_computed_once_and_many_classes_list_the_confusions`, `test_EVAL_1_2_a_version_without_metrics_says_why`.
