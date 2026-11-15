# Stratal — Design System

| | |
|---|---|
| Status | **v1, approved by the owner on 2026-11-15** (Gate 0). Changes from here on are new versions and need approval |
| Preview | [design/system-preview.html](design/system-preview.html): every token and component rendered, dark and light. Open it in a browser |
| Scope | SPEC §9.3 a–f. The token file and Tailwind theme (`tokens.css`) are written only after approval |
| Approvals | Logged in [design/APPROVALS.md](design/APPROVALS.md) |

The preview file is the exact statement of every value. This document says what the values are for and the rules for using them. If the two disagree, the preview's token block wins and this file is fixed.

## a) Principles

1. **Data first, chrome second.** This is an engineer's instrument panel. Default text is 13 px, tables are dense, and nothing is decorated that does not carry information.
2. **Calm.** One accent colour. Colour otherwise means something: a job state, a severity, or a resource. A screen at rest is mostly neutral.
3. **Status at a glance.** A job's state is readable from across the desk: a pill with an icon, a word and a colour, in the same place on every screen.
4. **Numbers do not jump.** Every live number is monospaced with tabular numerals, right-aligned in tables, and never reflows the text around it.
5. **Every warning says why and what to do.** A WARN or ERROR is always shown with its reason and suggested action (MON-1.3), never as a bare red line.
6. **"Unavailable" is not zero.** A metric that cannot be read shows a dash and the word "unavailable".
7. **Not a template.** IBM Plex Sans with JetBrains Mono, a violet accent on cool slate, 4–6 px radii, borders instead of shadows, one icon style.
8. **Four states for every screen:** empty, loading, error, success/populated. A screen design without all four is incomplete.

## b) Tokens

Defined once as CSS variables on `:root` (dark) and `[data-theme="light"]`. The Tailwind theme will map onto the same variables, so a Tailwind class and a CSS variable can never disagree. Components use tokens only; a lint rule will reject raw hex values outside the token file (SPEC §9.6).

### Colour

| Group | Tokens | Use |
|---|---|---|
| Neutral | `bg`, `surface`, `surface-raised`, `surface-sunken`, `border`, `border-strong`, `text-primary`, `text-secondary`, `text-muted` | Page, cards, floating layers, wells (inputs, logs); hairlines; control outlines; three text levels |
| Brand | `accent`, `accent-hover`, `accent-soft`, `on-accent` | Primary action, selection, focus ring, links. Nothing else |
| Semantic | `success`, `warning`, `danger`, `info` (+ `-soft` backgrounds, `on-danger`) | Severity of a message or an outcome |
| Job state | `state-queued`, `-running`, `-paused`, `-completed`, `-failed`, `-cancelled`, `-stalled` | Status pills, timeline segments, progress bars |
| Resource | `res-cpu`, `res-ram`, `res-gpu`, `res-vram`, `res-temp` | Fixed app-wide: CPU is always blue, RAM teal, GPU green, VRAM orange, temperature rose |
| Categorical | `cat-1` … `cat-8` | Unordered series in charts, layer families in the model builder |
| Sequential | `seq-1` … `seq-7` | Ordered magnitude (heat maps, distributions) |
| Diverging | `div-1` … `div-7` | Above and below a baseline (comparisons) |

Rules:

- **State colours and resource colours are reserved.** Blue-as-running and blue-as-CPU never appear in the same chart; state appears in pills and bars, resources in charts and tiles.
- Paused shares its hue with warning, and failed with danger, on purpose: they ask for the same kind of attention. Stalled has its own (pink) so it is not mistaken for either.
- The accent (violet) is not used for any state, severity or resource.
- Soft backgrounds are the same hue at low opacity, so they work on any surface.

**Measured contrast** (WCAG 2.1, against the three surfaces a colour can sit on; computed, not estimated):

| | Dark, lowest ratio | Light, lowest ratio |
|---|---|---|
| `text-primary` / `text-secondary` / `text-muted` | 13.1 / 7.4 / 5.2 | 16.0 / 7.2 / 5.2 |
| Accent, semantic, state, resource and categorical colours used as text or icons | 5.2 (`state-cancelled`) | 4.6 (`res-ram`, `res-vram`) |
| `on-accent` on `accent`; `on-danger` on `danger` | 7.4; 6.6 | 6.5; 5.6 |
| `border-strong` against its surface (control boundaries need 3.0) | 3.3 | 3.3 |

Everything used as text meets AA (4.5:1). `border` (hairlines between rows and around cards) is decorative and is not relied on to identify a control. The preview prints each swatch's ratio against the page background live.

### Typography

- **UI:** IBM Plex Sans (400, 500, 600). **Mono:** JetBrains Mono (400, 500), used for IDs, hashes, numbers, logs, code and config keys. Both are under the SIL Open Font License and will be bundled with the frontend, not fetched at run time (NFR-7).
- **Scale (px):** 11, 12, 13, 14, 16, 20, 24, 32. 13 is the default. Line height 1.5 for text, 1.25 for headings.
- **Roles:** 24 page title; 20 section; 16 card and dialog title; 14 reading text and chat; 13 tables, forms, buttons; 12 labels and log lines; 11 uppercase with letter-spacing for table headers and tile labels.
- **Tabular numerals** on every live or tabulated number (`font-variant-numeric: tabular-nums`, always in the mono face).

### Space, shape, depth, motion

| | Values |
|---|---|
| Spacing (4 px base) | 4, 8, 12, 16, 20, 24, 32, 40, 48, 64 |
| Radius | 2 (badges), 4 (controls), 6 (cards, tiles, tables), 10 (modals, chat bubbles), pill (status) |
| Borders | 1 px. Focus ring 2 px accent with 2 px offset |
| Control height | 24 small, 32 default, 40 large |
| Elevation | `shadow-1` graph nodes; `shadow-2` toasts, menus, tooltips; `shadow-3` modals, drawers. In dark, depth is the surface step plus the border; shadows only separate floating layers |
| Z-index | sticky 100, drawer 200, modal 300, toast 400, tooltip 500 |
| Motion | 80 ms hover and press; 160 ms toggles, tabs, tooltips; 240 ms drawers, modals, progress. One easing, `cubic-bezier(.2, 0, 0, 1)`. Nothing bounces. With `prefers-reduced-motion`, durations are 0 and the pulse, shimmer and spinner stop |
| Breakpoints | Desktop-first at ≥ 1280. Usable at 1024: four-column grids become two, forms become one column, the drawer overlays instead of pushing |

### Themes

Dark is the default (a monitoring tool is often watched for hours). Light redefines only the colour and shadow tokens; no component has theme-specific CSS. The choice is stored per browser and follows the system setting on first visit.

## c) Components

All are in the preview with their variants and states. States shown where they apply: default, hover, focus, active, disabled, loading, error.

| Component | Variants | Notes |
|---|---|---|
| Button | primary, secondary, ghost, danger; sm / md / lg | One primary per view. Loading keeps the width and shows a spinner with a verb ("Starting…"). Danger is outlined until hovered |
| Input | text, numeric (mono), with helper, with error | Label above, helper below; the error replaces the helper and has an icon |
| Select | single | Native control, styled; options come from the schema or the allow-list |
| Toggle | on, off, disabled | For settings that apply at once. A checkbox is used inside forms that are submitted |
| Slider | with live value | Always paired with the number in mono |
| Tabs | with counts, disabled | Underline style; the count is a mono badge |
| Table | default, dense; sortable; selected row | Numbers right-aligned in mono. Sticky header. Virtualised beyond about 200 rows. Row hover and selection use `accent-soft` |
| Card | plain, interactive | Border, no shadow |
| Badge | neutral, info, warn, error, ok, debug | Small facts: types, levels, counts |
| Status pill | the seven job states | Icon + word + colour. Running has a pulsing dot. Cancelled is also struck through |
| Progress bar | determinate with %, rate and ETA; indeterminate; paused; failed | The bar takes the state's colour. ETA shows "—" when unknown |
| Toast | success, info, warning, error; with action | Bottom right, stacked. Errors stay until dismissed and offer the suggested action |
| Modal | confirm, destructive confirm | See patterns |
| Drawer | right side | For detail that keeps the page in view: a stage's events and charts |
| Tooltip | plain | Explanations only, never the only place an action lives |
| Code / log viewer | config view; activity log | Log rows: time, level, stage, message; WARN and ERROR rows are tinted and show "Why / Do" |
| Metric tile | with sparkline; unavailable | Top edge in the resource colour; value in mono; secondary line for detail (allocated vs reserved) |
| Sparkline | 28 px | No axes; last 60 samples |
| Live chart | multi-series line | Resource colours; a dashed line of the same colour for the PyTorch view beside the NVML view (MON-2.4); built with ECharts using these tokens |
| Timeline bar | per stage | Width follows duration; state colour, icon, name, duration; pending stages dashed; the selected stage has the focus ring |
| Lineage node | default, selected | Stage, version ID, one fact. Left edge in the state colour |
| Layer node | default, selected, invalid | Layer type, key parameters, output shape and parameter count; invalid shows the shape mismatch in words |
| Chat bubble | user, assistant, streaming | Assistant footer shows the route, latency and speed in mono |
| Empty state | with one action | Says what is missing and how to get it |
| Error state | with retry | Shows the problem's `code` and the suggested action from the API's problem details |
| Loading | skeleton, spinner | Skeletons hold the final layout's size |

## d) Patterns

- **Forms from JSON schema (UI-2.3).** The label is the property name in mono with its type as a badge; the schema's `description`, range and default are the helper text; validation errors come from the API's `errors[].loc` and are placed on the matching field. Two columns at ≥ 1280, one below. No per-form styling: a new parameter needs no UI change.
- **Live-updating numbers.** Tabular mono, fixed-width containers, right-aligned in tables. Values update in place with no animation of the digits. A value older than three sample intervals is dimmed to `text-muted`.
- **Long-running jobs.** Always visible together: state pill, the current activity message in plain language, progress with percentage, rate and ETA, and the stage timeline. A requested cancel shows "Cancelling…" until the job has actually stopped (`desired_state`).
- **Destructive actions.** A modal that names the object, says what is kept and what is lost, puts the safe choice first, and labels the destructive button with the verb and object ("Cancel job"), never "OK". Deleting data additionally requires typing the name.
- **Keyboard.** Everything is reachable by Tab in reading order. Shortcuts are additions: `g d` / `g j` navigation, `g a` the active job, `t` theme, `/` filter, `j` / `k` rows, `Enter` open, `Esc` close, `a` / `r` accept and reject in review, `?` help.

## e) Accessibility

- **Contrast:** WCAG 2.1 AA, measured for every text and icon colour in both themes (table above).
- **Keyboard:** full navigation without a mouse; focus is never trapped except inside an open modal, and returns to the trigger when it closes.
- **Focus:** a visible 2 px ring on every interactive element, in both themes.
- **Never colour alone:** every state has an icon and a word; every severity has a label; chart series that share a colour differ by line style; charts have a text alternative.
- **Live regions:** the activity log is `role="log"` with `aria-live="polite"`; job state changes and toasts use `role="status"`, and failures `role="alert"`.
- **Motion:** honours `prefers-reduced-motion`.
- **Targets:** interactive controls are at least 24 px high; the default is 32.

## f) Deliverables and what happens after approval

| Deliverable | State |
|---|---|
| `docs/DESIGN-SYSTEM.md` | this file, v1 |
| `docs/design/system-preview.html` | v1 |
| `tokens.css` and the Tailwind theme | written only after approval (M6) |

## Open points for the owner

1. **Fonts. Resolved 2026-11-15:** with the owner's OK, IBM Plex Sans (400, 500, 600) and JetBrains Mono (400, 500) are bundled in `assets/fonts/` as WOFF2 with their OFL licences (about 390 KB; sources and hashes in its README). The preview loads them from there; opened from disk in Edge it renders both. Firefox has not been tried and may refuse local font files from a parent folder, in which case the preview falls back and says so.
2. **Accent colour.** Violet was chosen because it collides with no state, severity or resource colour. If you would rather have another hue, the constraint is the same.
3. **Density.** 13 px default text and 36 px table rows (28 px dense). Say if you want it looser.

## Revision history

| Version | Date | Changes |
|---|---|---|
| v1 | 2026-11-14 | First version, for Gate 0 review |
| v1 (icons) | 2026-11-15 | Seven icons join the set, drawn on its 16-px grid and stroke: datasets, jobs, health, theme, shortcuts, chevron-down, retry. Introduced and approved with the SCR-01 hi-fi mockup, where their definitions are |
| v1 (shortcuts) | 2026-11-15 | `g a` (active job) and `t` (theme) added to the keyboard pattern; proposed in the SCR-01 low-fi wireframe and approved with it |
| v1 (fonts) | 2026-11-15 | Not a new version: the approved typefaces are now bundled and the preview loads them. No token, size or component changed; the preview now shows the type the approval named instead of the fallback |
