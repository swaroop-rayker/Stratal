// Fine-tuning on SCR-09 v2 (RUN-1.10; mockup SCR-09v2_v1, frames E and F; ADR-0014 Addendum A):
// ?init_from= prefills from the parent, the checks come from POST /jobs/validate, a mismatched
// head blocks the start until it is reset, and freezing sends node IDs.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { RunScreen } from "./RunScreen";

const PARENT = "mv_a014046ec3b5";
const LINEAGE = "ml_7d806d9897fa";
const SPLIT = "ds_f8e67211dd94";
const GRAPH = {
  spec_version: 1, label: "small_cnn",
  nodes: [
    { id: "n1", layer: { type: "conv2d", out_channels: 32, kernel_size: 3 } },
    { id: "n2", layer: { type: "batch_norm" } },
    { id: "n3", layer: { type: "relu" } },
    { id: "n4", layer: { type: "global_avg_pool" } },
  ],
  edges: [["input", "n1"], ["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n4", "output"]],
};
const MODEL = { name: "graph", graph: GRAPH };
const ENDS = {
  version_id: SPLIT, dataset_type: "labelled_images", input: { kind: "image", dims: [3, 32, 32], vocab: null }, input_text: "image 3×32×32",
  output_size: 10, classes: ["airplane", "automobile", "bird", "cat", "deer", "dog", "frog", "horse", "ship", "truck"], families: ["cnn"], trainable: true, problem: null,
};
const MEMORY = { optimizer: "adam", weights_bytes: 5032, gradients_bytes: 5032, optimizer_bytes: 10064, total_bytes: 20128, activation_bytes_per_sample: 400000 };
const REPORT = {
  valid: true, input: ENDS.input, output_size: 10, family: "cnn", problems: [], structure_hash: "x", total_parameters: 1258, memory: MEMORY,
  head: { in_features: 32, out_features: 10, parameters: 330 },
  nodes: [
    { id: "n1", type: "conv2d", inputs: ["input"], shape: null, shape_text: "32×32×32", parameters: 864 },
    { id: "n2", type: "batch_norm", inputs: ["n1"], shape: null, shape_text: "32×32×32", parameters: 64 },
    { id: "n3", type: "relu", inputs: ["n2"], shape: null, shape_text: "32×32×32", parameters: 0 },
    { id: "n4", type: "global_avg_pool", inputs: ["n3"], shape: null, shape_text: "32", parameters: 0 },
  ],
};
const DETAIL = {
  id: PARENT, lineage_id: LINEAGE, lineage_name: "small_cnn · cifar10", state: "trained", is_latest: true, weights_status: "present", weights_bytes: 1178731,
  pinned: false, parent_version_id: "mv_406e717d58e8", job_id: "job_0057", job_state: "completed", dataset_version_id: SPLIT, config_hash: "h",
  main_metric: 0.84035, note: null, created_at: "2026-11-19T13:20:00Z", trained_at: "2026-11-19T13:26:07Z", weights_deleted_by_job_id: null,
  retention_action: "keep", retention_reason: "latest", config: { model: MODEL, epochs: 30, batch_size: 128, learning_rate: 0.001, optimizer: "adam", device: "gpu" },
  metrics: { val_loss: 0.70038, val_accuracy: 0.84035 },
};
const CHECKS_OK = [
  { rule: "Dataset type", ok: true, parent: "labelled_images", here: "labelled_images", message: "", code: "init_from_type" },
  { rule: "Task", ok: true, parent: "classification", here: "classification", message: "", code: "init_from_task" },
  { rule: "Input", ok: true, parent: "3×32×32", here: "3×32×32", message: "", code: "init_from_input" },
  { rule: "Output", ok: true, parent: "10 classes (airplane, …)", here: "10 classes (airplane, …)", message: "", code: "init_from_output" },
  { rule: "Spec", ok: true, parent: "small_cnn · cifar10", here: "small_cnn", message: "", code: "init_from_spec" },
];

function validation(over: Record<string, unknown> = {}) {
  return {
    parent_version_id: PARENT, checks: CHECKS_OK, lineage: { id: LINEAGE, name: "small_cnn · cifar10", joins: true },
    freeze_problems: [], trainable_parameters: 1258, frozen_parameters: 0, memory: MEMORY, ...over,
  };
}

function routes(answer: unknown) {
  return {
    "/api/v1/model-versions/mv_a014046ec3b5": DETAIL,
    "/api/v1/jobs/validate": answer,
    "/api/v1/jobs": { id: "job_0110", stages: [{ config: {} }] },
    "/api/v1/models": { items: [{ id: LINEAGE, name: "small_cnn · cifar10", model: MODEL }], next_cursor: null },
    [`/api/v1/models/${LINEAGE}`]: { id: LINEAGE, name: "small_cnn · cifar10", versions: [{ id: PARENT, state: "trained", weights_status: "present", main_metric: 0.84035, dataset_version_id: SPLIT, trained_at: DETAIL.trained_at, job_id: "job_0057" }] },
    "/api/v1/datasets": { items: [{ slug: "cifar10", modality: "image" }], next_cursor: null },
    "/api/v1/datasets/cifar10/versions": { items: [{ version_id: SPLIT, dataset: "cifar10", stage: "split", dtype: "labelled_images", created_at: "2026-11-18T10:00:00Z" }], next_cursor: null },
    [`/api/v1/versions/${SPLIT}`]: { version_id: SPLIT, dataset: "cifar10", stage: "split", dtype: "labelled_images", counts: { split: { splits: { train: { rows: 47704 }, val: { rows: 5963 }, test: { rows: 5963 } } } } },
    "/api/v1/model-specs/validate": { dataset: ENDS, report: REPORT },
    "/api/v1/model-specs/schema": { layer: { $defs: {} }, graph: {}, max_nodes: 200, max_edges: 400 },
    "/api/v1/model-templates": [],
    "/api/v1/config/schemas/train": { type: "object", properties: { epochs: { type: "integer", default: 10, title: "Epochs" }, learning_rate: { type: "number", default: 0.001 }, init_from: { type: "string" } } },
    "/api/v1/settings": { sections: [] },
    "/api/v1/system": { vram_total_mb: 6141 },
    "/api/v1/ml/estimators/schema": {},
  };
}

function renderRun() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/runs/new", element: <RunScreen /> },
      { path: "/jobs/:id", element: <p>job page</p> },
    ],
    { initialEntries: [`/runs/new?init_from=${PARENT}`] },
  );
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

beforeEach(() => {
  FakeSource.install();
  sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-09 fine-tuning", () => {
  test("test_RUN_1_10_init_from_prefills_from_the_parent_and_lists_its_checks", async () => {
    stubApi(routes(validation()));
    renderRun();

    expect(await screen.findByText(`fine-tuning ${PARENT}`)).toBeInTheDocument();
    expect(screen.getByText("the parent's architecture")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit in builder/ })).toBeNull();
    expect(await screen.findByText("3×32×32 = 3×32×32")).toBeInTheDocument();
    expect(screen.getByText(/Registers as a new version of/)).toHaveTextContent("small_cnn · cifar10");
    expect(screen.getByText(/On this split it should equal the parent's val loss/)).toHaveTextContent("0.700");
    expect(screen.getByText(/prefilled from the parent's run/)).toHaveTextContent("job_0057");
    await waitFor(() => { expect(screen.getByRole("button", { name: /Start run/ })).toBeEnabled(); });
    expect(screen.queryByLabelText("init_from")).toBeNull(); // the generated form hides the three fields
  });

  test("test_RUN_1_10_AC3_other_classes_block_the_start_until_the_head_is_reset", async () => {
    const mismatch = CHECKS_OK.map((c) => (c.rule === "Output" ? { ...c, ok: false, parent: "10 classes (airplane, …)", here: "4 classes (airplane, automobile, ship, truck)" } : c));
    const calls = stubApi(routes(validation({ checks: mismatch, lineage: { id: "ml_new", name: "small_cnn · vehicles", joins: false } })));
    renderRun();

    expect(await screen.findByText("The head does not fit this split.")).toBeInTheDocument();
    expect(screen.getByText("10 classes (airplane, …) in the parent, 4 classes (airplane, automobile, ship, truck) here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start run/ })).toBeDisabled();
    expect(screen.getByText(/Founds a new lineage/)).toHaveTextContent("small_cnn · vehicles");

    fireEvent.click(screen.getByLabelText("Reset the head"));
    await waitFor(() => {
      const last = calls.filter((c) => c.url.pathname === "/api/v1/jobs/validate").pop();
      expect((last?.body as { config: { reset_head: boolean } }).config.reset_head).toBe(true);
    });
    expect(screen.getByLabelText("Freeze the head")).toBeDisabled();
  });

  test("test_RUN_1_10_AC2_freezing_sends_node_ids_and_counts_what_trains", async () => {
    const calls = stubApi(routes(validation()));
    const router = renderRun();

    const table = await screen.findByRole("table", { name: "Layers with weights, to freeze" });
    expect(within(table).queryByText("n3")).toBeNull(); // relu has no weights
    fireEvent.click(within(table).getByRole("button", { name: "Freeze up to n2" }));
    expect(screen.getByText(/parameters will train/)).toHaveTextContent("330 of 1,258 parameters will train · 928 frozen in 2 layers");
    await waitFor(() => { expect(screen.getByRole("button", { name: /Start run/ })).toBeEnabled(); });

    fireEvent.click(screen.getByRole("button", { name: /Start run/ }));
    await waitFor(() => { expect(router.state.location.pathname).toBe("/jobs/job_0110"); });
    const start = calls.find((c) => c.method === "POST" && c.url.pathname === "/api/v1/jobs");
    const config = (start?.body as { config: Record<string, unknown> }).config;
    expect(config).toMatchObject({ init_from: PARENT, reset_head: false, freeze: ["n1", "n2"], epochs: 30, model: MODEL });
  });

  test("test_RUN_1_10_a_parent_without_weights_offers_re_train_and_blocks_the_start", async () => {
    stubApi({ ...routes(validation()), "/api/v1/model-versions/mv_a014046ec3b5": { ...DETAIL, weights_status: "weights_deleted", weights_deleted_by_job_id: "job_0058" } });
    renderRun();

    expect(await screen.findByText("Its weights were deleted by retention")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Re-train it/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start run/ })).toBeDisabled();
  });
});
