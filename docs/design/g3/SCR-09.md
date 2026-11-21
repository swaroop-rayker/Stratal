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
| Status | **Approved by the owner on 2026-11-20** ("Approve all four"), with the listed differences |

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

# SCR-09 v2 training entry (M11) — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-09v2_v1.html`](../screens/SCR-09v2_v1.html), G2 approved 2026-11-20, frames A–D, G and H (the training entry). Fine-tuning (frames E, F and the study row in G, RUN-1.10) is not built yet and has its own Gate 3 |
| Built | `frontend/src/screens/build/TrainDrawer.tsx` (new), `RunScreen.tsx`, `BuilderScreen.tsx`, `models/ModelsScreen.tsx`, `version/VersionScreen.tsx`; styles appended to `drawer.css`. Routes: `/runs/new?version=…&kind=network\|classical`; without a kind SCR-09 opens the drawer |
| Checked | 2026-11-20, live on the running stack. The pane was about 600 px wide, so the drawer filled most of it: content, behaviour and contrast were checked through the page. SCR-11 → wine `ds_6448d354c9c6` → Classical → SCR-09 classical (keyboard only: arrow, Enter); SCR-03 on cifar10 `ds_f8e67211dd94` (Classical disabled with its reason) → *Open the builder* with both selects filled; coco20k `ds_005e3e700ee8` (nothing trainable); coco10k-captions `ds_53f72c42aa5a` (tokenize); SCR-03 on `ds_499bfa2ee3d3` (clean_table, button disabled); `/runs/new?version=ds_98c3fdfd7edc` with no kind; *Train again…* from `job_0066` (diabetes) → *Change…* → Classical with the warning |
| Status | **Approved by the owner on 2026-11-20** ("Approved"), with the listed differences |

## Fixed during the check

1. The breadcrumb read *Models › Build › New run* on a classical run; it is now *Models › New run* (frame D).
2. An untrainable type's reason read "Image–caption pairs is not trained…"; now "are" (frame C).
3. Opened with no kind, the drawer's primary button read *Use Classical* and choosing Network stayed on SCR-09; it now reads *Set up the run* / *Open the builder* and Network goes to the builder, as on SCR-11.

## Differences from the mockup

1. **The drawer has the built drawer's header**, with the <kbd>Esc</kbd> hint beside the close button, as every built drawer has.
2. **Network's line of text names the type's families** ("Layers you stack in the builder (MLP), or a template. GPU or CPU, with a VRAM check.") for every type; the mockup had a shorter tabular wording mentioning the Tabular MLP template.
3. **The current kind in *Change…* keeps the usual description with a *current* badge**; frame G's card said "tabular_mlp, 5 layers, from job_0066" instead. The warning under the kinds names the job.
4. **Step 1's summary line in step 2** includes the stage (`split`, `tokenize`) and the input → output, also on wine (frame C's wine card showed only the class count).
5. **The facts box shows *Classes* only up to six classes** (wine's three are listed; cifar10's ten are not).
6. **"No version to train on yet" uses the shared empty state** with the info icon; *Continue the pipeline* opens the *Start pipeline* drawer in place of this one.
7. **SCR-03's *Train a model…* is also in the narrow layout's *More* menu**, disabled there off a trainable stage.
8. **Fine-tuning is not built** (frames E and F, the fixed study row): it waits for RUN-1.10's backend (`init_from`), so SCR-11 has no *Fine-tune…* yet and SCR-09's Start from card is unchanged.

## Accessibility

- **Contrast:** every text element passes AA in both themes, measured after a 3 s wait on switching: drawer step 1 22 elements, step 2 19–22 (wine, cifar10, coco20k, with the warning), SCR-09 classical 55, SCR-09 from `job_0066` 100.
- **Keyboard:** focus moves into the drawer and is trapped; the kinds are native radios in one fieldset, so the arrow keys move between allowed kinds and skip a disabled one; Enter outside a button or select presses the primary button; Esc closes and returns focus to the opener (*Choose…* when SCR-09 opened the drawer itself). A disabled kind's reason is text inside its label, so it is read with the radio. SCR-03's disabled button stays focusable with its reason in `aria-describedby`.

## Tests

`frontend/src/screens/build/TrainEntry.test.tsx`: `test_UI_2_1_a_tabular_split_reaches_classical_mode_without_the_builder`, `test_UI_2_1_a_non_tabular_split_shows_classical_disabled_with_its_reason`, `test_UI_2_1_runs_new_without_a_kind_opens_the_drawer_at_step_2`, `test_UI_2_1_change_to_classical_warns_that_the_draft_network_is_left_behind`. Frontend checks pass in `frontend-tools`: typecheck, lint (one existing warning elsewhere), Vitest 100/100, build.

---

# M11 — Fine-tuning (RUN-1.10), Gate 3

| | |
|---|---|
| Mockup | [`screens/SCR-09v2_v1.html`](../screens/SCR-09v2_v1.html), G2 approved 2026-11-20: frames E and F, and the fixed study row of frame G |
| Built | `frontend/src/screens/build/FineTune.tsx` (new: the Start from and Freeze layers cards, the validate and picker hooks), `RunScreen.tsx`, `StudyParts.tsx`, `models/VersionDrawer.tsx` (*Fine-tune…*, the parent), `job/Training.tsx` (epoch 0); styles appended to `scr09.css`. Route `/runs/new?init_from=mv_…` |
| Checked | 2026-11-21, live on the running stack with the rebuilt images. Fine-tuned `mv_a014046ec3b5` (`small_cnn · cifar10`) from SCR-09, layers `n1`–`n12` frozen, 3 epochs on the GPU: `job_0106` registered `mv_7f950100ab48`. Epoch 0 gave val loss 0.7004, the parent's 0.7004 (AC1); the 24 frozen tensors, BatchNorm statistics included, are identical to the parent's on disk and the rest changed (AC2); retention now blocks the parent ("parent of mv_7f950100ab48, still with weights"). The pane was about 800 px wide, so the page was one column; content, behaviour and contrast were checked through the page |
| Status | **Approved by the owner on 2026-11-21** ("Approved"), with the listed differences |

## Screenshots (in `SCR-09/`)

| File | Shows |
|---|---|
| `live-E-fine-tune-start-from-freeze-dark.jpg` | Start from with the parent, its three checks, *Reset the head* and the lineage callout; Freeze layers with `n1`–`n12` frozen |
| `live-E-fine-tune-vram-plan-dark.jpg` | The VRAM check *trainable only* (weights 1.1 MB, gradients and Adam for 222,986 trainable 2.6 MB, total 449 MB) and the plan with epoch 0 against 0.700 |

## Fixed during the check

1. SCR-11's version drawer called every parent "(base)". It now looks the parent up: a base keeps "(base)"; a trained parent is a link to its drawer, with its lineage and an *another lineage* badge when it is in another one (SCR-11 v2's frame E, built here because fine-tuning makes it visible).
2. Retention's blocked reason read "which keep their weights" for one child; it now reads "parent of mv_…, still with weights".
3. The freeze table now drops its Output column below 1280 px, as frame E's caption says.

## Differences from the mockup

1. **The version picker is a native select**, each option naming the version, its lineage, val accuracy, job, and "weights deleted" where so; frame F drew it as an open table. It is keyboard-native and lists versions of the same spec from every lineage, newest first.
2. **The Data card's split is two selects** (dataset, then version, of the parent's type only), as on SCR-08 and the *Train a model* drawer; frame E drew one select.
3. **The layer column describes the spec's own fields**, e.g. "conv2d 32 filters, 3×3", not "conv2d 3→32": a node's input channels are inferred, not in the spec.
4. **The Output column shows the builder's shape text** ("image 32×32×32").
5. **Sizes use the app's units** (1.1 MB, 2.6 MB, 449 MB), as the rest of SCR-09; the mockup's figures were rounded by hand (1.2 MB, 2.7 MB, 0.44 GB).
6. **An OK Output line names the classes** ("10 classes (airplane, automobile, …), the same list in the same order").
7. **A blocked start says why** under the plan: "Start is disabled until every check passes."
8. **The fixed study row** sits at the end of the search-space table, so it appears once a field is tuned; the Start from callout also says "Every trial starts from it; it is not tunable".
9. **SCR-04 draws epoch 0 as the start of the validation curves**, not as an epoch in the strip, so a 3-epoch run shows 3 epochs. Not in the mockup; the log shows the epoch-0 line with the parent's value.
10. **SCR-11's *Fine-tune…*** sits in the version drawer's action row after *Try it*, for a trained network version with its weights (decision 1 of the low-fi). The rest of SCR-11 v2 has its own Gate 3.

## Accessibility

- **Contrast:** every text element passes AA in both themes, measured after a 3 s wait on switching: the fine-tune page 187 (188 with rows frozen), the weights-deleted parent 114, with a study on 200, SCR-11's version drawer 184.
- **Keyboard:** Tab reaches *Fresh weights* / *A trained version*, the picker, *Reset the head* (focus ring on the toggle), then each row's checkbox and its *Freeze up to here*, which is visible when focused or hovered; the head's checkbox is disabled while the head is reset. The checks have words and an icon each, never colour alone.

## Tests

- `frontend/src/screens/build/FineTune.test.tsx`: `test_RUN_1_10_init_from_prefills_from_the_parent_and_lists_its_checks`, `test_RUN_1_10_AC3_other_classes_block_the_start_until_the_head_is_reset`, `test_RUN_1_10_AC2_freezing_sends_node_ids_and_counts_what_trains`, `test_RUN_1_10_a_parent_without_weights_offers_re_train_and_blocks_the_start`.
- `frontend/src/screens/job/Training.test.tsx`: `test_RUN_1_10_a_fine_tunes_epoch_0_is_the_val_curves_start_not_an_epoch_done`.
- Backend: `tests/integration/test_M11_fine_tune.py` (11 tests: AC1, AC2, AC3 with a reset head, another input shape, the parent rules, freezing and reference models, `POST /jobs/validate`, a table's preprocessing, a text's vocabulary, a study's start, retention) and `tests/unit/test_RUN_1_10_config.py` (8).
