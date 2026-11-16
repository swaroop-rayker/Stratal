# Design approvals

Only an explicit "approved" from the owner passes a gate (SPEC §9.2). Silence or partial feedback is not approval. One line per decision:

`<date> | <gate or screen ID> | vN | approved by | notes`

| Date | Gate / screen | Version | Approved by | Notes |
|---|---|---|---|---|
| 2026-11-15 | G0 design system | v1 | owner | Approved as delivered on 2026-11-14 ("I approved it"). Open: the font download was not answered, so the preview still shows fallback fonts. Fonts bundled later the same day with the owner's OK |
| 2026-11-15 | G1 screen inventory and flows (`docs/SCREENS.md`) | v1 | owner | "SCREENS.md approved". Open point 1 then decided by the owner: (a), a phase-1 slice of SCR-07 in M6. Points 2–4 accepted as placed in the document |
| 2026-11-15 | SCR-01 app shell, low-fi wireframe (`screens/SCR-01-lofi_v1.html`) | v1 | owner | "Approved". Includes the two shortcuts it proposed (`g a` active job, `t` theme), now added to the design system's keyboard pattern. Next: the hi-fi mockup |
| 2026-11-15 | SCR-01 app shell, hi-fi mockup (`screens/SCR-01_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-01. The seven icons it introduced (datasets, jobs, health, theme, shortcuts, chevron-down, retry) join the design system's set |
| 2026-11-15 | SCR-02 datasets and versions, low-fi wireframe (`screens/SCR-02-lofi_v1.html`) | v1 | owner | "Approved", including the Start pipeline drawer and the proposed `GET /sources` endpoint (added to API.md as planned). Next: the hi-fi mockup |
| 2026-11-15 | SCR-02 datasets and versions, hi-fi mockup (`screens/SCR-02_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-02. Its row-status colours become a design-system rule, and its five icons (plus, more, folder, copy, branch) join the set |
| 2026-11-15 | SCR-03 version detail and lineage, low-fi wireframe (`screens/SCR-03-lofi_v1.html`) | v1 | owner | "Approved", including the `dup_group_id` filter on `GET /versions/{id}/rows` (added to API.md). Next: the hi-fi mockup |
| 2026-11-15 | SCR-03 version detail and lineage, hi-fi mockup (`screens/SCR-03_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-03. Its three icons (eye, report, grid) join the set; button groups with a pressed state become the pattern for filters |
| 2026-11-15 | SCR-04 live job view, low-fi wireframe (`screens/SCR-04-lofi_v1.html`) | v1 | owner | "Approved". Next: the hi-fi mockup |
| 2026-11-15 | SCR-04 live job view, hi-fi mockup (`screens/SCR-04_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-04 |
| 2026-11-15 | SCR-05 labeling review, low-fi wireframe (`screens/SCR-05-lofi_v1.html`) | v1 | owner | "Approved". Next: the hi-fi mockup |
| 2026-11-15 | SCR-05 labeling review, hi-fi mockup (`screens/SCR-05_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-05. Its note icon joins the set |
| 2026-11-15 | SCR-06 eval report, low-fi wireframe (`screens/SCR-06-lofi_v1.html`) | v1 | owner | "Approved". Next: the hi-fi mockup |
| 2026-11-15 | SCR-06 eval report, hi-fi mockup (`screens/SCR-06_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-06. Its chart icon joins the set |
| 2026-11-15 | SCR-07 jobs list (phase-1 slice), low-fi wireframe (`screens/SCR-07-lofi_v1.html`) | v1 | owner | "Approved", including the `GET /jobs` additions (dataset filter, state lists, per-state counts; added to API.md). Next: the hi-fi mockup |
| 2026-11-15 | SCR-07 jobs list (phase-1 slice), hi-fi mockup (`screens/SCR-07_v1.html`) | v1 | owner | "Approved". Gate 2 passed for SCR-07. With it, every phase-1 screen (SCR-01 to SCR-06 and the SCR-07 slice) has passed Gate 2; M6 can build them once M5 is done |
| 2026-11-16 | SCR-01 app shell, G3 implementation (`g3/SCR-01.md`) | v1 | owner | "Approved, fix Idle pill". The seven listed differences accepted. The Idle pill is drawn in `text-secondary` instead of `text-muted` (light contrast 3.78 → 5.22:1); the same rule applies wherever Idle appears |
