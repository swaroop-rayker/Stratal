# SCR-13 system monitor — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-13_v1.html`](../screens/SCR-13_v1.html), G2 approved 2026-11-22 |
| Built | `frontend/src/screens/system/` (`SystemScreen`, `Overview`, `CompareJobs`, `Lanes`), `frontend/src/api/monitor.ts`, `frontend/src/styles/scr13.css`; route `/system` (`?range=`, `?from=&to=`, `?tab=compare&a=&b=&align=`). Addenda: SCR-04's *Compare with…* and linked log lines (`screens/job/`), SCR-14's Monitoring section (`screens/settings/`) |
| Checked | 2026-11-22, live at 1280 × 1000, dark and light, on the real database: coco10k's first pipeline (`job_0026`, 17 Nov 16:35–17:15) and the two Small CNN runs `job_0030` and `job_0051`, as in the mockup. The monitor was down during the check (E-012, the GPU lost to Docker), so the header showed its error state |
| Status | **Approved by the owner on 2026-11-22** ("Approved"), with the eight listed differences |

## Screenshots (in `SCR-13/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-overview-dark.jpg` | A | The five lanes from `job_0026`'s samples with the thresholds, the jobs strip with load markers, the monitor's "Not reporting" pill (frame D's error state, live) |
| `live-C-compare-dark.jpg` | C | `job_0030` solid against `job_0051` dashed by epoch; the summary matches the mockup's figures: 4.9 s against 8.8 s per epoch, GPU 73 % against 58 %, VRAM 0.74 against 0.75 GB, PyTorch 146 against 137 MB, RSS 6.6 against 5.4 GB, 63 against 60 °C |
| `live-SCR-14-monitoring-dark.jpg` | SCR-14 addendum | The Monitoring section with its five fields |

## Differences from the mockup

1. **The lanes are ECharts**, as the mockup said they would be: one canvas with a grid per lane. A 40-minute range is drawn as 4-second means (600 buckets), not the mockup's 20-second ones. The threshold labels are drawn inside the canvas, at the right end of their line.
2. **The pickers are native selects** listing "job · kind · state", not "job · model · length". The model and length are in the overlay's legend once two jobs are picked ("A · job_0030 · small_cnn · 10 epochs").
3. **The summary table has two more rows:** *Job CPU, mean* (the new `cpu_cores`; "—" for runs before M11) and *Time running*.
4. **Loads and unloads before M11 have no card figure** ("—"): it was not recorded then. A data migration (`e3b6f1a9c204`) gave those events their new types, so they appear at all.
5. **The custom range is a From and To form** under the header, opened by *Custom…*; the pressed button then shows the dates.
6. **The lane labels are 168 px wide**, not 150: at 150 px the VRAM label wrapped onto a third line.
7. **Diagnostics' *Now* has three more lines:** "No stage is running" when idle, a warm-up line for a stage's first minute, and "A CPU stage: shown, not diagnosed" (ADR-0020 rule 7).
8. **An alert still firing** says "firing" in *Lasted*.

## Not seen live

No alert or diagnosis has fired on this laptop, and the monitor was down during the check. So the alert rows, the alert's drawer and *Now* were checked only through `test_M11_monitoring.py` (8 integration tests) and `System.test.tsx` (7 Vitest tests), not on the live screen.

## Accessibility

- AA in both themes: Overview 113 text elements, Compare 80, no failures. The canvas is not scanned; its labels use text-secondary on surface.
- Each lane block is an image with a text label naming each lane's range maximum; *Table* lists the means. Alert rows are focusable and open the drawer with Enter. Markers on the jobs strip are focusable, with their message as the name. New alerts, diagnoses and loads enter `aria-live="polite"` table bodies.

## Tests

`frontend/src/screens/system/System.test.tsx` (7); `tests/unit/test_MON_2_6_alert_rules.py`, `test_MON_3_2_diagnose.py`, `test_MON_3_3_load_events.py`; `tests/integration/test_M11_monitoring.py` (8). The PyTorch-marked tests in the GPU worker's image are still to run (E-012).

## History tab (MON-4.5)

| | |
|---|---|
| Mockup | [`screens/SCR-13history_v1.html`](../screens/SCR-13history_v1.html), G2 approved 2026-11-24 (low-fi 2026-11-22) |
| Built | `frontend/src/screens/system/History.tsx`, the third tab of `SystemScreen`; `useHistory` in `frontend/src/api/monitor.ts`; the History block of `frontend/src/styles/scr13.css`; route `/system?tab=history&stage=&queue=&group=&n=&reused=`. API `GET /system/history` (`src/stratal/api/monitoring.py`), rules in `src/stratal/mon/history.py` |
| Checked | 2026-11-24, live at 1280 × 900 on the dev database, dark and light: `train` on the GPU in `small_cnn · cifar10 · batch 128` (the mockup's frame A: the same 8 runs, rates and times per epoch), *All groups* (frame C) and `label` (frame B) |
| Status | **Waiting for the owner's approval** |

### Screenshots (in `SCR-13/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-history-train-dark.jpg` | A | The six lanes for the 8 runs; `job_0114` tagged "11.0 s · worker lost, resumed", `job_0106` "18.9 s · fine-tune", `job_0057` "paused"; medians and thresholds at the right end |
| `live-history-train-light.jpg` | A, light | The same in light |
| `live-history-all-groups-dark.jpg` | C | The last 20 runs against their groups' medians, six groups each with its own shape and colour |
| `live-history-label-dark.jpg` | B | `label`: three runs that reused every caption hollow at the foot of the rate lane, tagged *reused*; `job_0122` (no peaks recorded) leaves gaps in the peak lanes |

### Differences from the mockup

1. **The lanes are HTML and SVG, not ECharts.** The mockup's note said ECharts; points as elements give the approved look exactly (rings, hollow points, shapes) and the linked hover with the table.
2. **Stage and Group are native selects**, as *Compare two jobs*' pickers already are (that screen's approved difference 2), with the same names and counts.
3. **GB are 1,024 MB**, as everywhere else in the app (Compare's 0.74 GB for the same card peak); the mockup divided by 1,000 (0.76 GB).
4. **Points keep a right margin of 15 %** for the median and threshold labels, and the lane captions are 208 px wide: at the mockup's spacing the median label covered the latest point. The median label is left out where a threshold label would collide with it (the caption still gives it), and the lowest of two threshold labels sits under its line.
5. **Tags beside raised points sit above them**, and only in a single group: in *All groups* the notes are in the table, since relative values and many groups made the tags collide.
6. **Points are not in the tab order**: six lanes of 20 runs would be 120 stops. The keyboard path goes tabs, Stage, Group, Last, the view toggle, then each run's checkbox and link in the table, the lanes' text equivalent.
7. **A seventh group shape**: *All groups* uses filled and hollow circles, squares and diamonds, and a triangle, with 8 categorical colours, so the dev database's six training groups never differ by colour alone (the mockup drew five).
8. **The note column** shows attempts and the note words ("2 attempts · worker lost, resumed"); the mockup's longer wording ("run C: …", "paused by you for 7 s") is not in the data.

### Accessibility

- AA in both themes for every new text style: 8.07:1 dark and 7.77:1 light for text-secondary on the surface (tags, medians, thresholds, captions, axis), 7.19:1 or better for the controls; points against the surface 5.09:1 (light).
- Each lane is an image whose name gives the figure, the number of runs and the median; *Table only* hides the lanes.

### Tests

`tests/unit/test_MON_4_5_history.py` (8), `tests/integration/test_M11_history.py` (2), `System.test.tsx` (3 more, 10 in all).
