# SCR-04 live job view — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-04_v1.html`](../screens/SCR-04_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/job/`, `frontend/src/components/LiveChart.tsx`, `frontend/src/styles/{scr04,stagedrawer}.css` |
| Checked | 2026-11-17, live on real jobs, at 1280 × 760 and 1024 × 640, dark and light. job_0017 is demo's label → filter → split → evaluate run on the GPU, started from SCR-03's drawer. job_0020 is a demo re-ingest, job_0019 a demo ingest cancelled from the dialog |
| Status | **Approved by the owner on 2026-11-17** ("Approved, apply fixes"); the light-theme contrast fixes are applied |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `mockup-A-dark.jpg` | A | The approved mockup, for comparison |
| `live-A-running-gpu-label.jpg` | A | job_0017 live in label: "Captioning batch 12/94 · Florence-2-base · bs=16 · fp16", 13 %, 0.6/s, ETA 2m 08s; VRAM tile with PyTorch's allocation dashed beside NVML's |
| `live-A-running-cpu.jpg` | A | job_0020 live in ingest: "Reading metadata 500/1,535" |
| `live-B-stage-drawer.jpg` | B | job_0017's label stage: run times, versions, metrics, peaks, the resource chart (ECharts) and the stage's events |
| `live-D-summary.jpg` | D | job_0017 completed: per-stage summary with peaks and outputs, then the run's charts and log |
| `live-G-all-jobs-warn.jpg` | G | All jobs, WARN and above: the machine's sleep warnings and job_0008's leakage warning, with Why and Do |
| `live-H-not-found.jpg` | H | An unknown job, with its code and "Go to jobs" |
| `live-I-1024.jpg` | I | 1024 px: tiles three a row, the page scrolls, the log follows |
| `live-narrow-light.jpg` | – | Light theme at the pane's own narrow width |

Frames C (failed) and E (queued) did not occur on this machine. Their states are covered by `test_UI_2_1_a_failed_job_says_why_what_to_do_and_what_was_kept` and `test_UI_2_1_a_queued_job_says_what_it_waits_for`. Frame F (cancel) was run for real on job_0019: the dialog opened with focus on "Keep running" and stated what is kept and lost. The pill read "Cancelling…" and then "Cancelled". The screenshot of the dialog raced its render, so the check is recorded here instead of as an image.

## Needs your decision: light-theme contrast of state colours

Measured on the live page in light (dark passes everywhere, lowest 4.75:1):

| | Now (light) | With the proposed fix |
|---|---|---|
| Pill text (completed, failed, running, cancelled, stalled) | 4.10 to 4.45 (paused 4.60 and queued 4.85 pass) | 5.27 or more |
| Timeline segment labels | 3.88 to 4.58 | 4.98 or more |
| ERROR word on a tinted log row | 4.15 | 5.40 |
| WARN word on a tinted log row | 4.55 (passes) | 5.73 |
| Time and stage on tinted WARN / ERROR rows (`text-muted`) | 4.18 to 4.23 | 5.78 or more with `text-secondary` |

**Proposed fixes, light theme only:**
- Pill and segment text, and the level word on tinted rows, use `color-mix(in srgb, <state colour> 80%, var(--text-primary))`. The hue stays; the text gets darker.
- Time and stage on tinted rows use `text-secondary`, the same rule as SCR-02's selected rows.

Backgrounds, borders and dark are unchanged. This is a design-system change: G0 measured these colours against surfaces, not against their own tints. That's why I'm asking rather than building it.

**A correction for SCR-01.** My earlier contrast scans misread `color-mix()` backgrounds, which pills use. The Idle pill was 4.34:1 in light, not 3.78, and is 5.83:1 with the fix you approved, not 5.22. The fix was still needed. The SCR-02 and SCR-03 figures involved no `color-mix()` and stand.

## Differences from the mockup

1. **Retry is plain Retry.** The mockup's "Retry with batch size 16" would need a retry that changes the config; `POST /jobs/{id}/retry` reuses it (SCREENS.md flow 3). Changing a value means a new run from the last good version.
2. **Progress counts steps.** The line reads "12 / 94 · 0.6/s", and the summary's column is "Steps". Each stage counts its own unit (label counts batches), so "img" would be wrong for some stages.
3. **The cancel dialog's Kept and Lost** are worded from the job's own stages. The mockup's "1,232 captions cached" count is not something the job reports.
4. **The log opens on the newest 300 events** for the filters, through the new `tail` and `min_level` parameters (API.md §3.3, §3.4). A text filter on `/` was added beside level and stage. New lines wait behind "Jump to latest · N new" while you are scrolled down or have paused auto-scroll.
5. **The stage drawer** shows the stage's own metrics as recorded (`images 1,500 · seconds 162 · oom_retries 0 …`). The mockup has a "Decided" line and a bottleneck note written per stage, which Stratal does not record.
6. **Tile charts** are the design system's sparkline drawn as SVG. The stage drawer's chart is the "live chart" component built with ECharts. Its time axis shows a few labels and hides overlaps.
7. **Finished jobs scroll**, and so does the page at 1024 px, with the log at a fixed height below the tiles (frames D and I describe this).
8. **Selecting a stage** puts it in the URL (`?stage=label`), as do the log's scope, level, stage, job and text.
9. **A crash in a screen** now shows an error state inside the shell, with Reload, instead of React Router's developer page (found while building this screen).

## Accessibility

- **Contrast:** see the decision above. Dark passes everywhere.
- **Keyboard:**
  - Timeline segments are buttons named with stage, state and duration; Enter opens the drawer, Esc closes it.
  - `/` focuses the log filter.
  - The cancel dialog focuses "Keep running" first; the destructive button names its verb and object.
- **Live regions:** the activity line is `aria-live="polite"`, the log is `role="log"`, the state pill is `role="status"` (`role="alert"` when failed), and the failure panel is `role="alert"`.

## Tests

`frontend/src/screens/job/JobScreen.test.tsx`:
- `test_MON_4_1_live_view_renders_and_updates_during_a_job`: activity, progress, ETA, tiles and PyTorch VRAM update from stream messages; the state follows the job.
- `test_MON_4_2_timeline_shows_every_stage_and_opens_a_stage_with_its_events`: all seven phase-1 stages, and a stage's drawer with its events (MON-4.2.AC1).
- `test_MON_4_3_*`: level "and above", stage, scope, job and text filters, and the request the filters send.
- `test_UI_2_1_*`: the failed and queued states.

Also `tests/integration/test_M6_system_and_jobs_api.py::test_MON_4_3_events_tail_and_minimum_level` against the real database.

## Addendum: SCR-04 v2 with the registry (M8 part B, 2026-11-19)

The accepted difference "no model version link on finished training runs" is now built. **Approved by the owner on 2026-11-19.**

| File | What it shows |
|---|---|
| `live-v2-open-version.jpg` | job_0047, completed: "Open mv_e56a61b2e5af" in the header, opening SCR-11 with that version's drawer |

The job view gained `model_lineage_id` for this link (API.md §3.3).

# Study addendum (M10) — Gate 3, no mockup

Designed at Gate 3 (SCR-10's approval, 2026-11-19). Built in `frontend/src/screens/job/JobScreen.tsx` and `Timeline.tsx`; **Approved by the owner on 2026-11-20** ("Approve all four"), with the listed differences.

- **Open the study:** a button in the header of a study's job (primary) and of a trial's job (secondary), to SCR-10.
- **A trial's job** says "trial n of st_…" before its model in the header line, and shows the training charts and run views a `train` job does.
- **A study's `waiting` stage** shows the clock icon and "waiting for its trial" on the timeline. (The study stage's duration is its last step's; the study's own time is on SCR-10.)
- **Contrast:** the study's job page 320 text elements and a trial's 386 pass AA in both themes (lowest 5.63:1 dark, 4.75:1 light).

## Fix after Gate 3 (2026-11-20, owner's report)

The activity log's stage column was 84 px, from the mockups, which fits names up to about 11 characters; `train_classical` (M10) ran into the message. It is now `15ch`, the longest stage name in the mono font, in every log (SCR-04, the stage drawer, the shared `.log`), with an ellipsis and the full name as a tooltip for anything longer.
