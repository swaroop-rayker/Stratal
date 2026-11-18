# Stratal — instructions for coding agents

Stratal is a local, end-to-end ML lifecycle platform for one laptop (RTX 4050 6 GB, about 15 GB RAM, Windows 11 with Docker on WSL2). It turns raw data into versioned datasets, trains and fine-tunes models, evaluates them, and serves them through a chat UI and an engineer dashboard. One developer owns it and is building it to learn.

## Read first

| File | Use it for |
|---|---|
| `docs/MILESTONES.md` | Where the project is. The in-progress milestone's checklist is the current state |
| `docs/SPEC.md` | What to build. The source of truth for requirements |
| `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/API.md` | How it is built |
| `docs/decisions/` | Why. One ADR per major decision |
| `docs/KICKOFF.md` | What was agreed at the start and what is waiting on the owner |

## Doc maintenance rules

- Docs are the source of truth. If code and docs disagree, stop and ask.
- Any change to schema, interfaces, or architecture → update the relevant doc in the same change; architectural changes need a new ADR first.
- After each milestone: tick MILESTONES.md, update affected docs.
- Never silently change a requirement; propose the change and wait.

## General rules (SPEC §9.1)

- Every change references a requirement ID (commit, PR, test name).
- Do NOT add a dependency unless a requirement needs it; state which.
- Human owns core logic (dedupe, versioning, batching/VRAM handling, scoring, evaluation, from-scratch layers). Agent may write plumbing (routes, configs, Docker, UI wiring, plots) and must explain any code it writes on request.
- Never mark a requirement done without a passing test_<ID>_* test.
- Record every non-trivial decision in DECISIONS.md (What/How/Why).

## Who writes what

The owner implements the units marked 👤 in `docs/SPEC.md` and `docs/MILESTONES.md`. Do not implement them, even partly, unless the owner asks.

For each owner unit, the agent provides ahead of time:

- a typed stub that raises `NotImplementedError`, with the contract in its docstring;
- failing tests named with the requirement ID, marked as strict expected-failures;
- a simple fake where other code needs something to call.

Then the agent reviews what the owner writes. Review means pointing out problems and explaining them, not rewriting.

The full split is in `docs/decisions/ADR-0012-working-model-and-git-workflow.md`.

## Conventions

- Commit messages reference IDs: `feat(DATA-2.2): pHash near-dup removal`
- Tests reference IDs: `test_DATA_2_2_near_duplicates`
- Every component gets 3 lines in DECISIONS.md: What / How / Why
- Acceptance criteria are `<ID>.AC<n>`, e.g. `DATA-2.2.AC1`. Bug regression tests are `test_BUG4_*`.
- Typed everywhere, strict mode. Linter and formatter enforced.
- Unit tests for domain logic; integration tests for API + DB against real services. Never mock the database in integration tests.
- Errors are typed and reach clients as RFC 9457 problem details with a stable `code` (ADR-0011).

## Git

- Remote `origin` is https://github.com/swaroop-rayker/Stratal.git (set up 2026-11-15). Push only `main` and milestone tags, and only after the owner's OK to merge. Milestone branches stay local unless the owner asks. Never force-push.
- `main` holds reviewed milestones only. Work happens on the milestone's branch (`m0-environment`, `m1-ingest-versioning`, …).
- Commit your own work on the milestone branch with conventional commits.
- Merge into `main` only with the owner's explicit OK, after the milestone's done-checklist passes.
- `docs/learning/` is ignored by git. Never commit it and never force-add it.

## Ask the owner first

- **Downloads.** State what, from where, and roughly how large, and wait for a yes.
- **Anything needing an account, email, key or license acceptance.** The budget is free tier only.
- **System settings.** On 2026-11-13 the owner delegated two things to the agent: `.wslconfig` and Docker Desktop's own settings. Say what you are changing before you change it, and back up the file first. Everything else stays with the owner: Windows power settings, NVIDIA settings, antivirus, firewall and anything security-related. For those, give the exact steps and do not change them.
- **Any requirement change**, and any deviation from an approved design.
- **Deleting data** that is not clearly a temporary file made in the current task.

## Environment facts

- The project is at `D:\Stratal` on Windows. The shell is PowerShell.
- Python runs only in containers (3.12). The host's Python is not the project's. Node (24) likewise: the `frontend` service and the on-demand `frontend-tools` (1.5 GB, profile `dev`); `node_modules` lives in the `stratal-node-modules` volume.
- Data lives on the Docker volume `stratal-data`, not in the repository.
- Memory: the WSL VM is capped at 10 GB; all containers together stay under 9 GB. VRAM: any job stays under 5.5 GB.
- One GPU job at a time. Each job runs in its own process (ADR-0003).
- The laptop has only Modern Standby; a sleep suspends the whole Linux VM.
- Docker's disk image is at `D:\Docker\wsl` (setting `CustomWslDistroDir` in `%APPDATA%\Docker\settings-store.json`).
- Norton Antivirus inspects HTTPS on this laptop. Containers trust its root certificate through `docker/certs/` (ignored by git; see the README there). Never work around a certificate error by disabling verification.

## Commands

Run from `D:\Stratal` in PowerShell. Everything executes in containers.

| Purpose | Command |
|---|---|
| Start postgres, redis, api, worker-cpu, monitor, beat and frontend | `docker compose up -d` (add `--profile gpu` for the GPU worker) |
| Stop them | `docker compose down` |
| All quality gates (format, lint, types, tests, integration; training tests in worker-gpu) | `.\scripts\check.ps1` |
| Same, without integration tests | `.\scripts\check.ps1 -Fast` |
| Same, plus GPU-marked tests | `.\scripts\check.ps1 -Gpu` |
| Same, plus the timing benchmarks (NFR-11; needs a quiet machine) | `.\scripts\check.ps1 -Bench` |
| Phase-1 acceptance on the live stack (over an hour on new images; NFR-1, NFR-2, NFR-3) | `docker compose run --rm dev pytest -q -s -m acceptance tests/acceptance` (`STRATAL_ACCEPTANCE_SOURCE` picks the folder, default `coco20k`) |
| Keep Windows awake while jobs run; report sleeps | `.\host\stratal-host.ps1` |
| One tool | `docker compose run --rm --no-deps dev ruff check .` (or `ruff format .`, `pyright`, `pytest -q`) |
| Integration tests | `docker compose run --rm dev pytest -q -m "integration and not benchmark"` |
| Training integration tests (need PyTorch; skipped in dev) | `docker compose run --rm -v ./src:/app/src:ro -v ./migrations:/app/migrations:ro worker-gpu pytest -q -m "integration and torch" tests/integration` |
| Anything on the GPU | `docker compose run --rm worker-gpu python <script>` |
| Re-lock after editing `pyproject.toml` | `docker run --rm -v "D:\Stratal:/app" -w /app stratal-base uv lock`, then `docker compose --profile dev --profile gpu build` |
| Health check | `http://127.0.0.1:8000/api/v1/health` |
| Web UI (Vite dev server, `/api` proxied) | `http://127.0.0.1:5173` |
| Frontend checks (typecheck, lint with the raw-hex rule, Vitest, build) | `docker compose run --rm frontend-tools sh -c "npm run typecheck && npm run lint && npm test && npm run build"` |
| Regenerate the TypeScript API types after changing an endpoint | `docker compose build api; docker compose up -d api; docker compose run --rm frontend-tools npm run api:types` |
| Add an npm package (ask first) | `docker compose run --rm frontend-tools npm install --save-exact NAME` |
| Apply database migrations | `docker compose run --rm dev alembic upgrade head` |
| Create a migration after changing `src/stratal/db/models.py` | `docker compose run --rm dev alembic revision --autogenerate -m "what changed"`, then read the generated file |
| Command line | `docker compose run --rm dev python -m stratal.cli ingest --dataset NAME --source PATH` (also `versions`, `lineage`, `events backfill --job ID` or `--all`) |
| Pull the allowed pretrained models (the only hub access) | `docker compose run --rm dev python -m stratal.cli models pull --all` (`models list` shows what is pulled) |
| Rebuild the phase-1 source folder (COCO 2017, 10k) | `docker compose run --rm dev python -I scripts/datasets/coco10k.py fetch` (`select` first if `sources/coco10k-meta` is missing); add `--size 20000` before `select` or `fetch` for `coco20k`, which contains coco10k |

The `stratal-base` image is built with `docker build --target base -t stratal-base -f docker/Dockerfile .`. Rebuild `dev` after changing dependencies; source changes need no rebuild because the repository is bind-mounted into `dev`. The `api`, `monitor` and `beat` services share one image that copies the source; `worker-cpu` has its own (`stratal-worker-cpu`, the same plus PyTorch's CPU build), and so does `worker-gpu`. Run `docker compose --profile gpu build api worker-cpu worker-gpu` and `docker compose up -d` to see code changes there. Integration tests use their own database (`stratal_test`), Redis database 15 and a temporary data folder. Source folders for ingestion go under `.\sources` (or `STRATAL_SOURCES_DIR`) and are seen as `/sources/<name>`.

## Learning notes

The owner keeps local notes in `docs/learning/`. After each milestone, update `Decisions.md` (what was decided and why, in plain language) and `sys-design.md` (how what was built works, the algorithms and libraries used and why, and interview questions with answers). Explain; do not just list.

## UI/UX workflow — DESIGN BEFORE CODE (mandatory gates)

This mirrors SPEC §9.2–9.6. If the two differ, SPEC.md wins and this file is fixed.

No UI page or component is implemented until its design is APPROVED by the human. Order:

- GATE 0 Design system -> must be approved before ANY screen design.
- GATE 1 Screen inventory + user flows -> approved.
- GATE 2 Per-screen mockup -> approved (one screen at a time).
- GATE 3 Implementation -> visually matches approved mockup.

Approval protocol:

- After producing a design artifact, the agent STOPS and asks: "Screen <ID> ready for review: <link/path>. Approve, or list changes?"
- Only an explicit "approved" from the human passes the gate. Silence, "looks ok-ish", or partial feedback = NOT approved.
- Each revision is versioned (v1, v2...) and its changes listed.
- Approved mockups are stored in docs/design/screens/<ID>_vN.(html|png) and logged in docs/design/APPROVALS.md: `<date> | <screen ID> | vN | approved by | notes`
- If implementation needs to deviate from an approved mockup, the agent asks first; it never silently diverges.

### GATE 0 — Design system

The agent writes the design system FIRST, covering:

a) Principles

- Information-dense but calm: an engineering tool, not a marketing site. Data first, chrome second.
- Status must be readable at a glance (running/paused/failed).
- Avoid generic template look: distinct type pairing, deliberate color, consistent iconography.
- Every screen has defined empty, loading, error, success states.

b) Tokens (defined once; exposed as CSS variables + Tailwind theme)

- Color: neutral scale (bg, surface, border, text-primary/secondary/muted), brand/accent, semantic (success, warning, danger, info), job-state colors (queued, running, paused, completed, failed, cancelled, stalled).
- Data-viz palette: categorical (8 colors), sequential, diverging; resource colors fixed app-wide (CPU, RAM, GPU, VRAM, temp).
- Light + dark themes from the same tokens (dark is default for a monitoring tool; both must pass contrast).
- Typography: UI font + monospace font (logs, IDs, metrics); type scale; tabular numerals for all live numbers.
- Spacing scale (4px base), radii, borders, elevation/shadows, z-index layers.
- Motion: durations/easings; respect prefers-reduced-motion.
- Breakpoints: desktop-first (>=1280), usable at 1024.

c) Components (each with variants + states: default, hover, focus, active, disabled, loading, error)

Buttons, inputs, selects, toggles, sliders, tabs, tables (sortable, virtualized), cards, badges/status pills, progress bar with ETA, toasts, modals, drawers, tooltips, code/log viewer, metric tile, sparkline, live chart, timeline bar, lineage node, layer node (model builder), chat bubble, empty state, error state.

d) Patterns

Forms generated from JSON schema (UI-2.3) styling rules; live-updating numbers (no layout shift); long-running job feedback; destructive-action confirmation; keyboard shortcuts.

e) Accessibility

WCAG 2.1 AA contrast; full keyboard navigation; visible focus rings; never encode meaning by color alone (icons/text on status); aria-live for activity log and job status updates.

f) Deliverables for approval

- docs/DESIGN-SYSTEM.md
- docs/design/system-preview.html (all tokens + components rendered, light and dark)
- tokens.css / tailwind theme file (implemented only AFTER approval)

### GATE 1 — Screen inventory & flows (docs/SCREENS.md)

The agent lists every screen with ID, purpose, requirement IDs served, key data shown, and main actions; plus 3-5 core user flows. The initial inventory (SCR-01 to SCR-14) is in SPEC §9.4.

Core flows to diagram: raw folder -> versioned dataset; dataset -> build model -> train -> evaluate; job failure -> diagnose -> resume; chat request -> router -> model -> answer.

### GATE 2 — Per-screen mockups

For each screen, in priority order (Phase-1 screens first: SCR-01, 02, 03, 04, 05, 06):

- Low-fi wireframe first (layout + hierarchy only) -> approve.
- Then hi-fi mockup using ONLY design-system tokens/components, with realistic data (real-looking IDs, numbers, log lines).
- Show all states: empty, loading, populated, error; plus dark/light.
- Annotate: which API endpoint feeds each region, what updates live.
- Format: static HTML (preferred, uses real tokens) or PNG export.

### GATE 3 — Implementation

- Build only approved screens, using design-system components; no one-off colors/spacing (lint for raw hex values).
- After building, the agent captures a screenshot and compares it to the approved mockup; lists any differences for human sign-off.
- Accessibility check (contrast, keyboard path) before marking done.
