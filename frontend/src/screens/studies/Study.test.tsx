// SCR-10 (M10, mockup SCR-10_v1): an HPO study's results against API fixtures (HPO-1.2 to
// HPO-1.5). Trial states in words, the best trial, importance, the single test evaluation,
// the trial drawer and the cancel confirmation.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { bestSoFar, fieldName, type Study, type Trial } from "../../api/studies";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { medianAtSteps } from "./History";
import { minutes, StudyScreen, trialWords } from "./StudyScreen";

const LR = "/config/learning_rate";
const BATCH = "/config/batch_size";

function trial(number: number, state: Trial["state"], value: number | null, extra: Partial<Trial> = {}): Trial {
  return {
    number, state, value, params: { [LR]: 0.001 * (number + 1), [BATCH]: 128 }, step: state === "complete" ? 10 : null,
    prune_reason: null, code: null, intermediate: value === null ? [] : [[1, value - 0.1], [2, value]], duration_s: 60,
    job_id: `job_00${String(60 + number)}`, started_at: "2026-11-19T09:00:00Z", finished_at: state === "running" ? null : "2026-11-19T09:01:00Z",
    ...extra,
  };
}

const TRIALS: Trial[] = [
  trial(0, "complete", 0.55),
  trial(1, "complete", 0.6),
  trial(2, "pruned", 0.4, { step: 3, prune_reason: "median" }),
  trial(3, "failed", null, { code: "over_vram", job_id: null, duration_s: 0 }),
  trial(4, "running", null, { step: 4, intermediate: [[4, 0.548]] }),
];

function study(extra: Partial<Study> = {}): Study {
  return {
    id: "st_4c1e9a07b2d3", job_id: "job_0061", kind: "deep", dataset_version: "ds_f8e67211dd94",
    objective: "val_accuracy", direction: "maximize", limits: { n_trials: 20, timeout_s: 3600 }, pruning: true,
    cv_folds: null, seed: 0, space: [{ path: LR, type: "log_float", low: 1e-4, high: 1e-1 }, { path: BATCH, type: "categorical", choices: [64, 128] }],
    base: { config: { model: { name: "graph", graph: { label: "small_cnn" } }, epochs: 10 } }, state: "running", ended_by: null,
    elapsed_s: 641, trials: { started: 5, complete: 2, pruned: 1, failed: 1, running: 1, interrupted: 0 },
    best: { trial: 1, value: 0.6, params: { [LR]: 0.002, [BATCH]: 128 } }, importance: null, best_version_id: null,
    test_metrics: null, test_evaluated_at: null, created_at: "2026-11-19T09:00:00Z", finished_at: null, ...extra,
  };
}

function routes(s: Study) {
  return {
    [`/api/v1/studies/${s.id}`]: s,
    [`/api/v1/studies/${s.id}/trials`]: { items: TRIALS },
    "/api/v1/jobs/job_0061": { id: "job_0061", kind: "hpo_study", state: "running", desired_state: null, pause_reason: null, stages: [] },
    "/api/v1/jobs/job_0061/cancel": { id: "job_0061", kind: "hpo_study", state: "cancelled", stages: [] },
    "/api/v1/versions/ds_f8e67211dd94": { version_id: "ds_f8e67211dd94", dataset: "cifar10", stage: "split", counts: {} },
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/studies/:studyId", element: <StudyScreen /> }, { path: "/runs/new", element: <p>run setup</p> }], { initialEntries: [path] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-10 helpers", () => {
  test("test_HPO_1_3_a_trial_state_is_said_in_words_with_its_step_and_reason", () => {
    expect(trialWords(TRIALS[2] as Trial, "epoch")).toEqual({ pill: "pruned", why: "at epoch 3 · median" });
    expect(trialWords(TRIALS[3] as Trial, "epoch")).toEqual({ pill: "failed", why: "over_vram · not run" });
    expect(trialWords(TRIALS[0] as Trial, "epoch")).toEqual({ pill: "complete", why: null });
  });

  test("test_HPO_1_5_the_history_steps_up_with_the_best_so_far_and_the_median_is_per_step", () => {
    expect(bestSoFar(TRIALS, "maximize")).toEqual([[0, 0.55], [1, 0.6]]);
    expect(bestSoFar(TRIALS, "minimize")).toEqual([[0, 0.55], [1, 0.55]]);
    const median = medianAtSteps(TRIALS, 3);
    expect(median.get(2)).toBeCloseTo(0.55);
    expect(fieldName("/config/model/graph/nodes/c1/layer/out_channels")).toBe("c1.out_channels");
    expect(minutes(641)).toBe("10:41");
  });
});

describe("SCR-10", () => {
  test("test_HPO_1_3_the_trial_table_says_each_state_in_words_and_marks_the_best", async () => {
    stubApi(routes(study()));
    renderAt("/studies/st_4c1e9a07b2d3");

    expect(await screen.findByText("small_cnn · cifar10")).toBeInTheDocument();
    expect(screen.getByText("at epoch 3 · median")).toBeInTheDocument();
    expect(screen.getByText("over_vram · not run")).toBeInTheDocument();
    const row = screen.getByText("at epoch 3 · median").closest("tr");
    expect(row).not.toBeNull();
    const bestRow = screen.getAllByText("best").find((el) => el.closest("tr"))?.closest("tr");
    expect(bestRow && within(bestRow).getByText("0.600")).toBeInTheDocument();
    expect(screen.getByText(/Computed when the study closes/)).toBeInTheDocument();
    expect(screen.getByText(/The test split is used once/)).toBeInTheDocument();
  });

  test("test_HPO_1_4_the_test_evaluation_is_marked_computed_once_with_no_button", async () => {
    stubApi(routes(study({
      state: "completed", ended_by: "n_trials", best_version_id: "mv_51d08c3e9f20", test_evaluated_at: "2026-11-19T10:14:07Z",
      test_metrics: { test_accuracy: 0.956, test_macro_f1: 0.952, detail: { rows: 57 } },
      importance: { method: "fanova", values: { [LR]: 0.7, [BATCH]: 0.3 } },
    })));
    renderAt("/studies/st_4c1e9a07b2d3");

    const card = (await screen.findByText("computed once")).closest("section");
    expect(card).not.toBeNull();
    if (!card) return;
    expect(within(card).getByText("0.956")).toBeInTheDocument();
    expect(within(card).queryByRole("button")).toBeNull();
    const importance = screen.getByText("Parameter importance").closest("section");
    expect(importance).not.toBeNull();
    if (!importance) return;
    expect(within(importance).getByText("learning_rate")).toBeInTheDocument();
    expect(within(importance).getByText(/fANOVA over the 2 complete trials/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel study/ })).toBeNull();
  });

  test("test_HPO_1_3_a_row_opens_the_trial_drawer_with_why_it_was_pruned", async () => {
    stubApi(routes(study()));
    renderAt("/studies/st_4c1e9a07b2d3");

    fireEvent.click(await screen.findByText("at epoch 3 · median"));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/compared epoch 3's val_accuracy with the median/)).toBeInTheDocument();
  });

  test("test_HPO_1_2_cancel_asks_first_with_keep_running_focused", async () => {
    const calls = stubApi(routes(study()));
    renderAt("/studies/st_4c1e9a07b2d3");

    fireEvent.click(await screen.findByRole("button", { name: /Cancel study/ }));
    const dialog = await screen.findByRole("alertdialog");
    await waitFor(() => { expect(within(dialog).getByRole("button", { name: "Keep running" })).toHaveFocus(); });
    expect(within(dialog).getByText(/Nothing is registered and the test split is not used/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /Cancel study/ }));

    await waitFor(() => { expect(calls.some((c) => c.url.pathname === "/api/v1/jobs/job_0061/cancel")).toBe(true); });
  });

  test("test_HPO_1_5_start_a_run_from_the_best_configuration_opens_scr09_prefilled", async () => {
    stubApi(routes(study()));
    const router = renderAt("/studies/st_4c1e9a07b2d3");

    fireEvent.click(await screen.findByRole("button", { name: /Start a run from this configuration/ }));

    await waitFor(() => { expect(router.state.location.search).toBe("?study=st_4c1e9a07b2d3"); });
  });
});
