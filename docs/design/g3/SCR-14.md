# SCR-14 settings — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-14_v1.html`](../screens/SCR-14_v1.html), G2 approved 2026-11-18 |
| Built | `frontend/src/screens/settings/SettingsScreen.tsx`, `frontend/src/styles/scr14.css`; route `/settings`, Settings in the sidebar foot with the sliders icon. Backend: `settings` table and `stratal.runtime.limits` (the training section), `GET`/`PUT /settings` |
| Checked | 2026-11-19, live at 1280 × 760 and 1024 × 640, dark and light. The training values were changed on the page and discarded, never saved. Retention was changed through the page and restored afterwards (global 0, no overrides); the only deletion was one demo version in `mlp · cifar10` |
| Status | **Waiting for the owner's review** |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-training-unsaved-dark.jpg` | A | `stall_timeout_s` 300 → 600: changed badge, Reset to 300, the save bar, "1" in the index; thermal context live: now 36 °C, 7-day high 74 °C (job_0026), slowdown 91 °C (NVML) |
| `live-A-training-light.jpg` | A (light) | The training section in light |
| `live-B-retention-saved-toast.jpg` | B | Overrides table (keep_intermediate, intermediates now, their size, Override… / Remove) after a save, with the toast "Retention saved. Nothing to delete." |
| `live-C-confirm-global-lower.jpg` | C | Lowering the global N from 2 to 0: 1 version in `mlp · cifar10`, 3.1 MB, "Not affected: small_cnn · cifar10", type "delete 1 version" |
| `live-D-field-error.jpg` | D | pause 80, resume 78: "Must be at most 75: 5 °C below thermal_pause_c (80)", "3 unsaved changes · 1 to fix", Save disabled |
| `live-D-stale-version-toast.jpg` | D | Retention saved elsewhere after the page loaded: the alert toast "Settings changed elsewhere … Reload retention"; the training changes are kept |
| `live-E-loading-light.jpg` | E | Loading skeletons |
| `live-F-1024.jpg` | F | 1024 px: the index as a row of buttons above the section; the slowdown point in the pause field's tooltip |

Frame E's empty overrides ("No per-lineage overrides") and error states use the shared components; there is no way to empty the lineages or break the API without affecting the rest of the stack, so they are not shown live.

## Differences from the mockup

1. **Helpers are not shortened at 1024 px** (frame F shortens them to the range and default); the full schema description is kept, which wraps to one more line.
2. **The success toast reads "Saved / Retention saved. Nothing to delete."**, or names the retention job a confirmed save queued; the mockup names the lineage and its new N.
3. **"Not affected" in frame C lists the lineages by name** without saying why ("its override keeps 3" in the mockup).
4. **Monitoring (M11)** is shown disabled in the index, as in the mockup, but has no section yet.
5. **"Also to job_…, which is running"** appears under the stall timeout only while a job runs; none did during the check.

## Accessibility

- **Contrast:** every text element passes AA in both themes, scanned with transitions off: 71 / 71 at rest, 78 / 80 with a changed field and a field error (light / dark).
- **Keyboard:** the index links, then each field and its Reset, the save bar's Discard and Save, the retention Number / All buttons, keep_intermediate, Add override, each override's input and Remove / Override…, the link to SCR-11, the theme buttons. Every stop shows the focus ring. Override buttons are named with their lineage ("Override keep_intermediate for small_cnn · cifar10"; fixed during the check).
- **Names:** each input is labelled by its property name and type; the helper, or the error that replaces it, is its description, and an invalid field has `aria-invalid`. The unsaved count has a text name ("2 unsaved changes"). The temperature does not announce itself (`aria-live="off"`). The save bar is a status region; the save toast is polite, the conflict toast an alert. The deletion dialog focuses Keep them; leaving with unsaved changes asks first.

## Found while testing

- **A clash with a utility class.** The rules note used the class `fixed`, which Tailwind's utilities define as `position: fixed`, so it floated over the section header. Renamed `rules-note`; no other screen class collides with a utility.

## Tests

`frontend/src/screens/settings/SettingsScreen.test.tsx`: `test_RUN_1_6_resume_must_stay_5_degrees_below_pause_and_pause_below_slowdown`, `test_RUN_1_7_a_changed_field_shows_old_to_new_and_saves_its_section`, `test_REG_2_10_lowering_the_global_n_asks_with_the_versions_and_the_phrase`, `test_REG_2_3_changes_lists_each_field_as_old_to_new`. Backend: `tests/integration/test_M8_settings.py` (`test_RUN_1_7_*`, `test_RUN_1_6_*`, `test_RUN_1_3_*`).
