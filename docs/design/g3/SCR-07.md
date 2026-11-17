# SCR-07 jobs list (phase-1 slice) — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-07_v1.html`](../screens/SCR-07_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/jobs/JobsScreen.tsx`, `frontend/src/styles/scr07.css`; route `/jobs` |
| Checked | 2026-11-17, live at 1280 × 760 and 1024 × 640, dark and light, on the 21 existing jobs plus `job_0022` and `job_0023`: two demo ingest-to-preprocess runs started for this check |
| Status | Waiting for the owner's review. One proposed design-system fix needs a decision (see Accessibility) |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-all-dark.jpg` | A | All 23 jobs: state counts, Finished section, outcomes (output version, "report for …", "cancelled in ingest · 0 versions kept") |
| `live-A-all-light.jpg` | A (light) | The same in light |
| `live-A-active-and-cancel.jpg` | A, C | `job_0022` and `job_0023` running in the Active section with live progress and ETA; the cancel dialog for a running job, "Keep running" focused |
| `live-A-failed-row.jpg` | A | `job_0022` failed: `stage_failed in ingest` and the reason, cut to fit, with Retry |
| `live-B-failed-filter.jpg` | B | Filtered to Failed |
| `live-B-no-match.jpg` | B | "No cancelled jobs for coco1k" with Clear filters |
| `live-C-retry-toast.jpg` | C | After Retry: the row has left Failed and the toast links to the job. Taken before one fix: the toast now says "attempt 2" (see below) |
| `live-E-1024.jpg` | E | 1024 px: dataset, attempt and created columns folded away |

Frame D (empty, loading, error) uses the shared states; there is no way to empty the job table without deleting data, so it is not shown live. Its error and empty states are the same components as on SCR-02.

## What happened during the live check

- **Cancel arrived too late.** `job_0023` finished before its next safe point. The API answered `409 invalid_state_transition`; the dialog showed it and stayed open, and the row turned Completed on the stream.
- **A real failure, then a retry.** `job_0022` failed with a duplicate-key error (see "Found while testing"). Retry from the Failed filter queued it again; it completed on attempt 2 with the same output version as `job_0023`.
- **The retry toast said "attempt 1".** The worker counts an attempt when it starts one, so the queued job's attempt is still the old one. The toast now shows the next attempt, as the mockup does.
- **Leaving the search box rewrote the URL** even when nothing changed. Fixed: the filter is set only when it changes.

## Differences from the mockup

1. **Kind is a real filter** (any, pipeline, report). The mockup disabled it because only pipeline jobs were expected in phase 1, but evaluate-report jobs (EVAL-1.1) are their own kind. Report jobs have no dataset, so their Dataset cell is "—" and the outcome reads "report for ds_…".
2. **Job-ID search** needed `GET /jobs?q=` (job-ID substring); it is added and documented (API.md §3.3). Enter or leaving the box applies it.
3. **Cancelled outcome** reads "cancelled in clean · 1 version kept"; the API does not record who cancelled, so "by you" is left out.
4. **No "More" menu at 1024 px.** All five state buttons fit at 1024, so the mockup's "More" overflow menu is not needed. The dataset, attempt and created columns fold away as in frame E.
5. **No "1 cancelled job in other datasets"** line in the no-match state; it would need a second count query without the dataset filter. "Clear filters" is there.
6. **Older jobs** load when the end of the table scrolls into view; a "Load more" button beside the count does the same from the keyboard.
7. **A failure's reason is cut** to about 30 characters in the row (full text in its tooltip and on SCR-04). Some reasons are long: `job_0022`'s is a whole SQL statement.

## Accessibility

- **Contrast:** 190 text elements scanned in each theme. Light: all pass. Dark: **the state pill on the selected row fails**, and on hovered rows too, because both use the accent tint behind the pill's own tint:

  | State | Dark, on a selected row |
  |---|---|
  | Cancelled | 3.61 |
  | Failed | 4.04 |
  | Queued | 4.20 |
  | Running | 4.21 |
  | Completed | 4.39 |
  | Stalled / Paused | 4.56 / 4.79 (pass) |

  **Proposed fix (needs approval, a design-system change):** on selected and hovered table rows, in dark, mix the pill's text 70% toward `text-primary`: the lowest becomes 4.94 (Cancelled). The 80% mix approved for light at SCR-04 is not enough here (Cancelled 4.46). The same pills on other screens' selected rows would get the same rule.
- **Keyboard:** Tab goes Start a pipeline, the five state buttons, Dataset, Kind, Job ID, then the selected row (one tab stop for the table's rows), then the rows' Cancel and Retry buttons in order. `/` focuses the search, `j` / `k` move between rows, Enter opens the job. Every stop shows the focus ring.
- **Names:** each state button includes its count ("Failed, 1 job"); row actions are named with their job ("Cancel job_0146").
- **Live regions:** the table is not live; a job's change of state is announced once, politely ("job_0023 completed"). The retry toast is `status`; a failed retry is an `alert`.

## Found while testing (not part of this screen)

**Two identical jobs racing to commit the same version.** `job_0022` and `job_0023` were submitted together with the same source and stages and ran side by side on the CPU workers. Both computed version `ds_1fb7a64c25ce`; the second insert failed with `UniqueViolation` on `dataset_versions_pkey`, so `job_0022` failed in ingest. A retry succeeded because the version then existed. Versioning is the owner's unit, so this is reported, not changed.

## Also changed for this screen

- `GET /jobs` takes `q`, a job-ID substring (RUN-1.1); `test_RUN_1_1_jobs_filter_by_state_list_and_dataset_with_counts` covers it.
- `waitingFor` (what a queued job waits for) moved from SCR-04 into `screens/job/jobs.ts` so both screens use it.

## Tests

`frontend/src/screens/jobs/JobsScreen.test.tsx`:
- `test_RUN_1_1_active_jobs_are_pinned_above_finished_ones_with_what_each_is_doing`
- `test_RUN_1_1_the_state_filter_asks_the_api_and_is_kept_in_the_url`
- `test_RUN_1_1_a_filter_with_no_matches_offers_to_clear_it`
- `test_RUN_1_2_cancelling_a_queued_job_asks_first_and_says_nothing_is_lost`
- `test_RUN_1_2_retry_queues_a_failed_job_again_and_links_to_it`
- `test_RUN_1_1_a_state_change_on_the_stream_moves_the_job_and_is_announced`
- `test_RUN_1_1_j_and_k_move_between_rows_and_enter_opens_the_job`
