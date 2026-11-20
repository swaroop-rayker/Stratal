# SCR-09 training run setup — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-09_v1.html`](../screens/SCR-09_v1.html), G2 approved 2026-11-19 (the study part with SCR-10, M10) |
| Built | `frontend/src/screens/build/RunScreen.tsx`, `vram.ts`, `frontend/src/styles/scr09.css`; route `/runs/new` (`?version=`, or `?from=job_…&batch_size=`); links from SCR-08 (*Continue to training*), SCR-11 (*Train again…*), SCR-07 and SCR-04 (*Start again with N* on a run that ran out of memory) |
| Checked | 2026-11-19, live at the pane's 800 px (the single-column layout), dark and light, with the CNN built on SCR-08 (`my_model`, 10,602 parameters) on `ds_f8e67211dd94`; *Start run* created `job_0053`, followed on SCR-04 and found on SCR-11 |
| Status | **Approved by the owner on 2026-11-19** ("Approved"), with the listed differences |

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

# Study part and Classical mode (M10) — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-09hpo_v1.html`](../screens/SCR-09hpo_v1.html), G2 approved 2026-11-19 |
| Built | `frontend/src/screens/build/RunScreen.tsx`, `StudyParts.tsx`, `study.ts`; `SchemaForm`'s new `decor` (a control after a field's label and a replacement input, generic); styles appended to `scr09.css`. `/runs/new?study=st_…` prefills a study's best configuration (SCR-10's *Start a run from this configuration*) |
| Checked | 2026-11-20, live: Classical mode on `breast-cancer` (a random forest, `n_estimators` and `max_features` tuned); a network study prefilled from `st_5869c7673960` with `learning_rate`, `batch_size` and the layer field `d1.out_features` tuned and a refused range; both themes. The pane was 583 px wide: content, behaviour and contrast were checked through the page |
| Status | **Waiting for the owner's review** |

## Differences from the mockup

1. **`time_budget` is entered in minutes**, a number field, rather than as a duration ("1 h").
2. **The plan has no total duration.** It gives the trial count, the epochs and steps, and S-09's ~19 s start per trial; the training time per trial is not known before a run.
3. **The Tune toggle shows on every tunable field whether or not the study is on**; pressing one turns the study on (the low-fi decision "a tune toggle on every generated field"). The collapsed card has *Turn on*; the open card has an *on* pill and *Turn off*.
4. **Integer fields offer a linear range (with an optional step) or choices, not a log scale:** the server has no log-integer marker.
5. **The objectives are the server's:** `val_accuracy` or `val_loss` for a classifier, `val_rmse` or `val_loss` for a regressor; `cv_accuracy` or `cv_macro_f1`, `cv_r2` or `cv_rmse` for classical models. The mockup's helper also named `val_macro_f1`, which the server does not offer.
6. **The data card names the task and the classes**, not the target column and the column count (frame B): the builder's dataset ends do not carry them.
7. ***Drop N from batch_size* appears when batch_size is tuned as three or more choices**; with a range the warning stands alone.
8. ***Add a model parameter* opens as a panel below the cards**, not a popover anchored to its button; a chosen layer field's range is edited in its row of the search space.
9. **In Classical mode, `random_state`** (the run's seed) is a field under the estimator's parameters.
10. **At 1024 px the right-hand column stacks below**, as SCR-09's approved build does, rather than folding into a bottom bar (frame E).

## Accessibility

- **Contrast:** every text element passes AA in both themes: Classical study 123 elements (lowest 5.44:1 dark, 5.2:1 light); the network study with a refused marker and a layer field 174 elements (same lowest).
- **Keyboard and names:** each Tune button is a toggle (`aria-pressed`) named "Tune <field>"; a range's inputs are named "<field>, low", "<field>, high" and "<field>, scale"; a space row's remove button is "Stop tuning <field>" and returns focus to the field's Tune button. *Start study* stays enabled: with a refused marker it sends nothing and moves focus to the first problem; a 422 from `POST /jobs` lands on the marker's row and field. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> starts the study.

## Tests

`frontend/src/screens/build/Builder.test.tsx` (SCR-09 study part): `test_HPO_1_1_a_marker_is_refused_on_its_row_by_the_fields_own_rules`, `test_HPO_1_1_the_vram_check_takes_the_widest_layer_in_the_space`, `test_HPO_1_1_tuning_a_field_starts_a_study_with_its_marker`, `test_HPO_1_1_a_refused_marker_from_the_server_lands_on_its_row`.
