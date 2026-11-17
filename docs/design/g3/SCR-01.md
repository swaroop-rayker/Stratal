# SCR-01 app shell — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-01_v1.html`](../screens/SCR-01_v1.html), G2 approved 2026-11-15 |
| Built | `frontend/src/shell/`, `frontend/src/styles/{tokens,components,shell,theme}.css`, on branch `m6-phase1-ui` |
| Checked | 2026-11-16, live against the running stack at 1280 × 760 and 1024 × 640, dark and light |
| Status | **Approved by the owner on 2026-11-16** ("Approved, fix Idle pill"); the Idle pill now uses `text-secondary` |

## Screenshots (in this folder)

| File | Mockup frame | What it shows |
|---|---|---|
| `mockup-A-dark.jpg` | A | The approved mockup, for comparison |
| `live-A-running-dark.jpg` | A | `job_0015` running (ingest 65 %, ETA 4 s), live telemetry at 1 s |
| `live-B-idle-dark.jpg` | B | Idle: "No job running · last job_0013 completed 2h ago", idle telemetry at 10 s |
| `live-A-idle-light.jpg` | A (light) | Light theme, switched with `t` |
| `live-G-toast.jpg` | G | "job_0015 completed · 29s, Output ds_dad3b193aae1", from the global stream |
| `live-H-help-light.jpg` | H | Shortcut help opened with `?`, focus on Close |
| `live-I-1024-rail.jpg` | I | 1024 px: icon rail, indicator without bar and ETA, strip without sparklines and units |
| `live-D-api-down.jpg` | D | The api container stopped: banner with `dependency_unavailable` and its action, "API down" in the sidebar |
| `live-D-reconnecting.jpg` | D | After the API returns: values dimmed and dated, "Reconnecting…" |
| `live-E-loading.jpg` | E | First paint while the API does not answer: skeletons hold the final sizes |

## Differences from the mockup

1. **Completed toast's close control** is a ghost icon button, not a bare icon, so it can be reached and pressed from the keyboard.
2. **Rail tooltips** (frame I) use the mockup's `.tip` style, placed by CSS beside the focused or hovered item.
3. **Jobs that cannot be read** show "Idle · Jobs unavailable", not "No job running" (principle 6). The mockup has no frame for this; once jobs have been read, an outage keeps the last known indicator, as frame D shows.
4. **Health before the first answer** reads "Checking…". The mockup has no frame for it.
5. **Frame F (GPU metrics unreadable)** is built but was not seen live: making NVML fail would mean changing the machine. Before any sample exists the GPU values read "—", not "unavailable".
6. **Sparklines** start flat and fill as samples arrive (60 kept); the mockup draws them full.
7. **Dev server only:** with the api container stopped, the error appears after about 20 s, the time the dev proxy waits before giving up.

## Accessibility

- **Contrast**, measured on every text element of the live page against its real background:
  - Dark: every element at least 4.5:1.
  - Light: one failure. The "Idle" pill's word is **3.78:1** (`text-muted` on its own 14 % tint, 11 px). It is drawn exactly as in the approved mockup, so fixing it changes the design. Proposed: draw "Idle" in `text-secondary`, measured at 5.22:1 there. Needs the owner's decision. **Corrected 2026-11-17:** that scan misread `color-mix()` backgrounds; the true figures are 4.34:1 before and 5.83:1 after, so the fix was still needed.
- **Keyboard:** Tab goes Datasets → Jobs → Theme → Shortcuts → job indicator, each with the 2 px focus ring. `g d`, `g j`, `g a`, `t`, `?` and Esc work. Focus moves into the help dialog and returns on close.
- **Landmarks and live regions:** `nav` (Main), `header`, `main`, `footer` (machine resources, `aria-live="off"`, values in its accessible name); health `role="status"` or `role="alert"`; failed toasts `role="alert"`, completed `role="status"`.

## Tests

`frontend/src/shell/JobIndicator.test.tsx` (`test_UI_2_1_*`), `frontend/src/api/stream.test.ts` (`test_MON_1_4_*`), `frontend/src/lib/format.test.ts`.
