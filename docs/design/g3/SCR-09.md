# SCR-09 training run setup — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-09_v1.html`](../screens/SCR-09_v1.html), G2 approved 2026-11-19 (the study part with SCR-10, M10) |
| Built | `frontend/src/screens/build/RunScreen.tsx`, `vram.ts`, `frontend/src/styles/scr09.css`; route `/runs/new` (`?version=`, or `?from=job_…&batch_size=`); links from SCR-08 (*Continue to training*), SCR-11 (*Train again…*), SCR-07 and SCR-04 (*Start again with N* on a run that ran out of memory) |
| Checked | 2026-11-19, live at the pane's 800 px (the single-column layout), dark and light, with the CNN built on SCR-08 (`my_model`, 10,602 parameters) on `ds_f8e67211dd94`; *Start run* created `job_0053`, followed on SCR-04 and found on SCR-11 |
| Status | Waiting for the owner's review |

## Screenshots (in `SCR-09/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-A-setup-top-dark.jpg` | A | Model (from the builder, cnn, parameters, memory with Adam, "No problems"), Data (train 47,704, val 5,963, test 5,963, 10 classes, image 3×32×32), the start of the generated form |
| `live-A-training-form-dark.jpg` | A | The rest of the form (learning rate, weight decay, schedule, seed, deterministic, checkpoint interval from SCR-14), Start from, Device |
| `live-start-opens-scr04-job_0053.jpg` | – | *Start run* opened the run's live view (SCR-04), queued |
| `live-demo-found-in-scr11.jpg` | – | The finished run on SCR-11: `my_model · cifar10`, `mv_0cfa1ce88a16`, val acc 53.6 % |

## Differences from the mockup

1. **The Data card shows the split as text**, not a select: the dataset is chosen on SCR-08, whose model's ends depend on it.
2. **The training form is one grid of three columns** without the mockup's group headings (schedule, optimizer, …): the form is generated from the schema in its field order, so a new field needs no UI change (UI-2.3).
3. **Start from is a sentence for every run** ("its lineage's base: an existing lineage's, or a new one saved before step 1"); it does not look up whether the lineage exists yet.
4. **Over the cap there is one start button, renamed *Start anyway***, beside the primary *Use batch N*; the mockup draws *Start anyway* as a second button.
5. **A run started again from an M8 job keeps its reference model** (`small_cnn`, badge "reference model"), so the new run joins the same lineage; its memory estimate comes from the built-in template with the same layers.
6. **The VRAM bar's scale** names the card's size and the cap from `GET /system` (6.0 GB, 5.1 GB) rather than fixed text.

## Accessibility

- **Contrast:** every text element passes AA after the theme transition settled: 106 (dark) and 105 (light).
- **Keyboard:** the form's fields in order, the device toggle, *Start run*; <kbd>Ctrl</kbd>+<kbd>Enter</kbd> starts the run from anywhere on the page. A refused start (422) puts each error on its field.
- **Names:** the VRAM bar is hidden from screen readers (the table beside it says the same in words); the result is a status region.

## Tests

`frontend/src/screens/build/Builder.test.tsx`: `test_BUILD_1_5_the_vram_check_warns_and_offers_a_batch_that_fits`, `test_BUILD_1_2_a_run_starts_with_the_graph_and_the_form_values`, `test_RUN_1_5_start_again_with_the_suggested_batch_prefills_the_failed_run`.
