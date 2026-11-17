# SCR-06 eval report — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-06_v1.html`](../screens/SCR-06_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/screens/report/ReportScreen.tsx`, `frontend/src/styles/scr06.css`; route `/versions/:versionId/report` |
| Checked | 2026-11-17, live at 1280 × 760 and 1024 × 640, dark and light: coco1k report #5 (`ds_7ffe080f799f`, with `rev_0002`); coco10k report #7 (no review); `ds_db6bcccdb054` with no report, built from the page (`job_0021` → report #10) |
| Status | Waiting for the owner's review |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `mockup-A.jpg` | A | The approved mockup, for comparison |
| `live-A-report-dark.jpg` | A | coco1k: 985 of 1,000 kept, filter rate 0.91 %, acceptance 90.0 % with its interval; rows by stage; CLIP histogram with the 0.20 threshold |
| `live-A-report-light.jpg` | A (light) | The same in light |
| `live-B-no-report.jpg` | B | No report yet, with "Build report". Pressing it showed the building state, followed `job_0021` on the live stream and opened report #10 when the job completed |
| `live-C-without-review.jpg` | C | coco10k, built without a review: "— not measured" and "Start a review" |
| `live-D-clip-table.jpg` | D | The CLIP distribution as a table |
| `live-E-not-found.jpg` | E | An unknown version, with its code and "Go to datasets" |
| `live-F-1024.jpg` | F | 1024 px: the interval's sentence shortened to its range |

Not seen on real data, covered by tests: the "newer review" callout of frame C (no version has a closed review newer than its report) and the failed build of frame E.

## Differences from the mockup

1. **Acceptance per split.** Under the interval, the acceptance card lists each split's accepted / decided and its interval, from the report's `by_split` (DATA-4.7). It is hidden at 1024 px.
2. **One reason per row.** The reasons table lists each (stage, reason) pair with its rows and share of the raw rows, rather than grouping reasons.
3. **Building lands here.** "Build the eval report" on SCR-03 and SCR-05 now opens this page with `?job=`, so the build is followed in place (frame B's building state).
4. **A named report** opens with `?report=<id>`; without it the page shows the version's newest.
5. **The threshold** comes from the version's filter stage in its lineage, as on SCR-05.

## Accessibility

- **Contrast:** all 107 text elements pass in both themes on coco1k's report. The histogram's labels are HTML, not SVG text, so the same check covers them.
- **Keyboard:** Tab follows reading order: the job and review links, Open version, Rebuild, each panel's Chart / Table switch, Open review, then the sidebar. The focus ring shows on each. Enter follows links and presses buttons.
- **Switches:** each Chart / Table pair is a group named after its panel ("CLIP score distribution: chart or table"), and each button carries `aria-pressed`.
- **Charts:** the funnel and the histogram are images whose accessible names give their numbers (for example "filter: 985 kept, 9 removed"), and each has a table with the same numbers.
- **Live regions:** the building state and the newer-review callout are `status`; a failed build is an `alert`.

## Also changed for this screen

- **`GET /versions/{id}/reviews`:** a version's reviews, newest first, with their progress (API.md §3.5). The page uses it to name the review a build will use and to notice a newer one. Tested by `test_LABEL_1_5_a_version_lists_its_reviews_newest_first`.
- **Response models** for the report endpoints.

## Tests

`frontend/src/screens/report/ReportScreen.test.tsx`, `test_EVAL_1_1_*`:
- headlines, funnel and acceptance interval;
- the CLIP chart has a table with the same numbers;
- without a review, acceptance is "not measured";
- a newer closed review offers a rebuild with it;
- with no report yet, it builds one and follows the job to the new report.
