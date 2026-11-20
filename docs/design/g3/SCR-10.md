# SCR-10 HPO results — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-10_v1.html`](../screens/SCR-10_v1.html), G2 approved 2026-11-19 |
| Built | `frontend/src/screens/studies/StudyScreen.tsx`, `History.tsx`, `frontend/src/api/studies.ts`, `frontend/src/styles/scr10.css`; route `/studies/:studyId`; reached from SCR-07 and SCR-04 (the addenda below and in their notes), and from SCR-09's *Start study* |
| Checked | 2026-11-20, live on two real studies: `st_e06085ead155`, a random forest on `breast-cancer` (classical, 40 trials, 13 pruned, completed), and `st_5869c7673960`, a two-layer MLP on `wine` on the CPU (10 trials, 4 pruned at epoch 2, completed), dark and light. The Browser pane was 583 px wide, so content, behaviour and contrast were checked through the page; no full-width screenshot was taken |
| Status | **Approved by the owner on 2026-11-20** ("Approve all four"), with the listed differences |

## What was checked against the mockup

- **Frame A (running):** the header pill follows the study's job (running, paused); both limits as progress, time not counting pauses; *Pause* and *Cancel study…*; the history chart with dots, hollow pruned dots, failed crosses on the axis and the best-so-far step line, and a Table view; *Best so far* with the fixed fields; the trials table with the state in words; importance and the test evaluation as placeholders.
- **Frame B (finished, classical):** "the trial limit was reached first"; *Start a run from the best configuration* and *Open the best model*; fANOVA bars with the caveat, and a Table view; the test evaluation marked *computed once* with large figures, when and on which version, and no button; the table counts folds and has no job column.
- **Frame C (trial drawer):** the trial's value at each step against the median of earlier trials, its values, job and duration, and why it was pruned (median or budget) or failed (`over_vram`, `invalid_combination`, `interrupted`, or its job's code).
- **Frame D (cancel):** an alert dialog that says what is lost, with *Keep running* focused.
- **Frame E (states):** loading skeleton; not found, with *Go to Jobs*; no finished trial, naming the running one and its job; cancelled; test evaluation running.

## Differences from the mockup

1. **The trial table is not virtualised.** A study has at most 200 trials; the table scrolls inside its card, with the number column and the header pinned.
2. **The test evaluation's *retrying* state is not shown.** The API does not say how many attempts `test_eval` took; a retry shows as *running*.
3. **The cancelled callout does not say who cancelled.** The study view has no actor; it says when.
4. **At 1024 px the best card stacks under the history** (one column below 1280 px), rather than folding into the header line (frame F).
5. **The history's axes** label three values and the first, middle and last trial numbers.
6. **A thermal pause** is said in the limits line ("paused while the GPU cools") beside the job's paused pill.

## Accessibility

- **Contrast:** every text element passes AA, scanned after the theme transition settled (a canvas resolves `color-mix()` backgrounds): the network study 182 elements, lowest 5.44:1 (dark) and 5.2:1 (light); with the trial drawer open, 32 elements, lowest 5.63:1 and 5.35:1.
- **Keyboard:** each trial row is focusable; <kbd>Enter</kbd> opens its drawer and <kbd>Esc</kbd> closes it, returning focus to the row; the cancel dialog opens on *Keep running*.
- **Names and live regions:** trial states are a pill with an icon and a word, plus the step and reason in text; the history chart has a text alternative and the Table view; the trial count and time used are announced politely.

## Found while checking, fixed

Two backend bugs showed up only on the live stack, and are fixed with regression tests (ADR-0018 "As built, part B"): two studies starting at once raced on creating Optuna's tables; and every step of a network study drew the same configuration, because each new process seeded TPE with the study's seed alone.

## Tests

`frontend/src/screens/studies/Study.test.tsx`: `test_HPO_1_3_a_trial_state_is_said_in_words_with_its_step_and_reason`, `test_HPO_1_5_the_history_steps_up_with_the_best_so_far_and_the_median_is_per_step`, `test_HPO_1_3_the_trial_table_says_each_state_in_words_and_marks_the_best`, `test_HPO_1_4_the_test_evaluation_is_marked_computed_once_with_no_button`, `test_HPO_1_3_a_row_opens_the_trial_drawer_with_why_it_was_pruned`, `test_HPO_1_2_cancel_asks_first_with_keep_running_focused`, `test_HPO_1_5_start_a_run_from_the_best_configuration_opens_scr09_prefilled`.
