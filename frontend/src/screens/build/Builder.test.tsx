// SCR-08 and SCR-09 (M9, mockups SCR-08_v1 and SCR-09_v1): the builder's UI against API
// fixtures. SCR-08 is tested in its list view: the canvas is a picture of the same graph,
// and React Flow needs layout APIs that jsdom does not have.
import type { RJSFSchema } from "@rjsf/utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { saveDraft, type Graph } from "../../lib/graph";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { BuilderScreen } from "./BuilderScreen";
import { layerTypes, newLayer, unavailable } from "./layers";
import { RunScreen, worstGraph } from "./RunScreen";
import { specProblem, toMarker } from "./study";
import { vramCheck } from "./vram";

const LAYER_SCHEMA = {
  $defs: {
    Conv2d: {
      title: "Conv2d",
      type: "object",
      properties: {
        type: { const: "conv2d", default: "conv2d", type: "string" },
        out_channels: { type: "integer", minimum: 1, maximum: 4096, default: 32, description: "Output channels." },
        kernel_size: { type: "integer", minimum: 1, maximum: 15, default: 3, description: "Square kernel, in pixels." },
        bias: { type: "boolean", default: true, description: "Learn an additive bias per channel." },
        in_channels: { anyOf: [{ type: "integer", minimum: 1 }, { type: "null" }], default: null, description: "Leave empty to take it from the input." },
      },
    },
    GlobalAvgPool: { title: "GlobalAvgPool", type: "object", properties: { type: { const: "global_avg_pool", type: "string" } } },
    LSTM: { title: "LSTM", type: "object", properties: { type: { const: "lstm", type: "string" }, hidden_size: { type: "integer", default: 128 } } },
    Linear: { title: "Linear", type: "object", properties: { type: { const: "linear", type: "string" }, out_features: { type: "integer", default: 128, minimum: 1 } } },
  },
};

const IMAGES = {
  name: "labelled_images", title: "Labelled images", input_kind: "image", families: ["mlp", "cnn"],
  default_metrics: ["accuracy", "macro_f1"], trainable: true, note: "",
};

const GRAPH: Graph = {
  spec_version: 1, label: "my_cnn",
  nodes: [{ id: "c1", layer: { type: "conv2d", out_channels: 32, kernel_size: 3, bias: false } }, { id: "g1", layer: { type: "global_avg_pool" } }],
  edges: [["input", "c1"], ["c1", "g1"], ["g1", "output"]],
};

function ends(version: string, dims: number[], classes: number) {
  return {
    version_id: version, dataset_type: "labelled_images", input: { kind: "image", dims, vocab: null },
    input_text: `image ${dims.join("×")}`, output_size: classes, classes: [], families: ["mlp", "cnn"], trainable: true, problem: null,
  };
}

function report(problems: { node_id: string | null; code: string; message: string }[] = []) {
  const valid = problems.length === 0;
  return {
    valid, input: {}, output_size: 10,
    nodes: [
      { id: "c1", type: "conv2d", inputs: ["input"], shape: valid ? { kind: "image", dims: [32, 32, 32], vocab: null } : null, shape_text: valid ? "image 32×32×32" : null, parameters: valid ? 864 : null },
      { id: "g1", type: "global_avg_pool", inputs: ["c1"], shape: valid ? { kind: "vector", dims: [32], vocab: null } : null, shape_text: valid ? "vector 32" : null, parameters: valid ? 0 : null },
    ],
    head: valid ? { in_features: 32, out_features: 10, parameters: 330 } : null,
    total_parameters: valid ? 1194 : null, family: "cnn",
    memory: valid ? { optimizer: "adam", weights_bytes: 4776, gradients_bytes: 4776, optimizer_bytes: 9552, total_bytes: 19104, activation_bytes_per_sample: 1_663_312 } : null,
    problems, structure_hash: valid ? "ab".repeat(32) : null,
  };
}

function routes(extra: Record<string, unknown> = {}) {
  return {
    "/api/v1/model-specs/schema": { layer: LAYER_SCHEMA, graph: {}, max_nodes: 200, max_edges: 400 },
    "/api/v1/dataset-types": [IMAGES],
    "/api/v1/model-templates": [],
    "/api/v1/datasets": { items: [{ slug: "cifar10" }], next_cursor: null },
    "/api/v1/datasets/cifar10/versions": { items: [{ version_id: "ds_a", stage: "split", row_count: 59630 }, { version_id: "ds_b", stage: "split", row_count: 100 }], next_cursor: null },
    "/api/v1/versions/ds_a": { version_id: "ds_a", dataset: "cifar10", stage: "split", counts: { split: { splits: { train: { rows: 47704 }, val: { rows: 5963 }, test: { rows: 5963 } } } } },
    "/api/v1/versions/ds_b": { version_id: "ds_b", dataset: "cifar10", stage: "split", counts: {} },
    ...extra,
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/models/build", element: <BuilderScreen /> },
      { path: "/runs/new", element: <RunScreen /> },
      { path: "/jobs/:jobId", element: <p>job page</p> },
      { path: "/studies/:studyId", element: <p>study page</p> },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  FakeSource.install();
  sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-08", () => {
  test("test_UI_2_3_a_field_added_to_a_layer_schema_appears_in_its_form_with_no_ui_change", async () => {
    saveDraft("ds_a", GRAPH);
    const withGroups = structuredClone(LAYER_SCHEMA) as typeof LAYER_SCHEMA & { $defs: { Conv2d: { properties: Record<string, unknown> } } };
    withGroups.$defs.Conv2d.properties.groups = { type: "integer", minimum: 1, default: 1, description: "Channel groups." };
    stubApi(routes({
      "/api/v1/model-specs/schema": { layer: withGroups, graph: {}, max_nodes: 200, max_edges: 400 },
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
    }));
    renderAt("/models/build?version=ds_a&view=list");

    fireEvent.click(await screen.findByText("c1", { selector: "td" }));

    const inspector = await screen.findByRole("region", { name: "Selected layer" });
    expect(within(inspector).getByText("groups")).toBeInTheDocument();
    expect(within(inspector).getByText(/Channel groups/)).toBeInTheDocument();
    expect(within(inspector).getByPlaceholderText("inferred: 3")).toBeInTheDocument();
  });

  test("test_DATA_5_5_the_palette_offers_only_layers_the_dataset_type_takes", async () => {
    saveDraft("ds_a", GRAPH);
    stubApi(routes({ "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() } }));
    renderAt("/models/build?version=ds_a&view=list");

    const palette = await screen.findByRole("region", { name: "Layers" });
    const lstm = await within(palette).findByRole("button", { name: /LSTM/ });
    expect(lstm).toHaveAttribute("aria-disabled", "true");
    expect(lstm).toHaveAttribute("title", "Labelled images take mlp or cnn models");
    expect(within(palette).getByRole("button", { name: /Conv2d/ })).not.toHaveAttribute("aria-disabled");
    expect(unavailable("conv2d", { ...IMAGES, families: ["mlp"] } as never)).toMatch(/take mlp/);
  });

  test("test_BUILD_1_3_changing_the_dataset_updates_the_locked_input_and_output", async () => {
    saveDraft("ds_a", GRAPH);
    saveDraft("ds_b", GRAPH);
    // The validate route answers by the version in the body, so this test stubs fetch itself.
    const fetchMock = vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/model-specs/validate") {
        const body = (await request.json()) as { dataset_version: string };
        const dims = body.dataset_version === "ds_a" ? [3, 32, 32] : [3, 64, 64];
        return Response.json({ dataset: ends(body.dataset_version, dims, body.dataset_version === "ds_a" ? 10 : 5), report: report() });
      }
      const value = (routes() as Record<string, unknown>)[url.pathname];
      return value === undefined ? Response.json({ code: "not_found", detail: "x" }, { status: 404 }) : Response.json(value);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAt("/models/build?version=ds_a&view=list");

    expect(await screen.findByText(/input image 3×32×32 → 10 classes/)).toBeInTheDocument();
    fireEvent.change(await screen.findByRole("combobox", { name: "Split version" }), { target: { value: "ds_b" } });
    expect(await screen.findByText(/input image 3×64×64 → 5 classes/)).toBeInTheDocument();
  });

  test("test_BUILD_1_4_problems_are_listed_on_their_nodes_and_block_continuing", async () => {
    saveDraft("ds_a", GRAPH);
    stubApi(routes({
      "/api/v1/model-specs/validate": {
        dataset: ends("ds_a", [3, 32, 32], 10),
        report: report([{ node_id: "c1", code: "shape_mismatch", message: "c1: in_channels is 1, but its input has 3 channels" }]),
      },
    }));
    renderAt("/models/build?version=ds_a&view=list");

    const list = await screen.findByRole("region", { name: /1 problem/ });
    expect(within(list).getByText(/in_channels is 1, but its input has 3 channels/)).toBeInTheDocument();
    expect(within(list).getByText("shape_mismatch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue to training/ })).toBeDisabled();
    expect(screen.getAllByText("shape_mismatch").length).toBeGreaterThan(1); // also on the node's row
  });

  test("test_BUILD_1_7_a_template_starts_the_graph", async () => {
    stubApi(routes({
      "/api/v1/model-templates": [{ id: "mt_1", name: "Small CNN", description: "M8's reference CNN.", dataset_type: "labelled_images", builtin: true, spec: GRAPH, structure_hash: "x", created_at: "", updated_at: "" }],
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
    }));
    renderAt("/models/build?version=ds_a&view=list");

    expect(await screen.findByText("Start from a template, or add a first layer.")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Use" }));

    expect(await screen.findByText("g1", { selector: "td" })).toBeInTheDocument();
    expect(JSON.parse(sessionStorage.getItem("stratal.builder.ds_a") ?? "{}")).toMatchObject({ label: "my_cnn" });
  });

  test("new layers take their schema's defaults", () => {
    const types = layerTypes(LAYER_SCHEMA);
    const conv = types.find((t) => t.type === "conv2d");
    expect(conv?.inferred).toEqual(["in_channels"]);
    expect(conv ? newLayer(conv) : null).toEqual({ type: "conv2d", out_channels: 32, kernel_size: 3, bias: true });
  });
});

describe("SCR-09", () => {
  test("test_BUILD_1_5_the_vram_check_warns_and_offers_a_batch_that_fits", () => {
    const card = 6141; // MB, the RTX 4050
    const fits = vramCheck(4_619_936, 2_892_880, 128, card);
    expect(fits.fits).toBe(true);
    const over = vramCheck(4_619_936, 2_892_880, 2048, card);
    expect(over.fits).toBe(false);
    expect(over.suggested).toBe(1024);
  });

  test("test_BUILD_1_2_a_run_starts_with_the_graph_and_the_form_values", async () => {
    saveDraft("ds_a", GRAPH);
    const calls = stubApi(routes({
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
      "/api/v1/config/schemas/train": {
        type: "object",
        properties: {
          model: {}, epochs: { type: "integer", minimum: 1, default: 10 }, batch_size: { type: "integer", minimum: 1, default: 128 },
          optimizer: { enum: ["adam", "sgd"], default: "adam" }, momentum: { type: "number", default: 0.9 }, device: { enum: ["auto"], default: "auto" },
        },
      },
      "/api/v1/settings": { sections: [{ name: "training", values: { checkpoint_interval_min: 5 } }] },
      "/api/v1/system": { vram_total_mb: 6141 },
      "/api/v1/jobs": { id: "job_0099" },
    }));
    const router = renderAt("/runs/new?version=ds_a");

    expect(await screen.findByText("Fits under the cap.")).toBeInTheDocument();
    expect(screen.queryByText("momentum")).not.toBeInTheDocument(); // Adam: no momentum field
    fireEvent.click(screen.getByRole("button", { name: /Start run/ }));

    await waitFor(() => { expect(router.state.location.pathname).toBe("/jobs/job_0099"); });
    const post = calls.find((c) => c.url.pathname === "/api/v1/jobs");
    expect(post?.body).toMatchObject({
      kind: "train", dataset_version: "ds_a",
      config: { epochs: 10, batch_size: 128, device: "auto", model: { name: "graph", graph: { label: "my_cnn" } } },
    });
  });

  test("test_RUN_1_5_start_again_with_the_suggested_batch_prefills_the_failed_run", async () => {
    stubApi(routes({
      "/api/v1/jobs/job_0067": {
        id: "job_0067", kind: "train", state: "failed", input_version_id: "ds_a", spec: { dataset_version: "ds_a" },
        stages: [{ name: "train", input_version_id: "ds_a", config: { model: { name: "graph", graph: GRAPH }, epochs: 30, batch_size: 128, device: "gpu", optimizer: "adam" } }],
      },
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
      "/api/v1/config/schemas/train": { type: "object", properties: { epochs: { type: "integer", default: 10 }, batch_size: { type: "integer", default: 128 } } },
      "/api/v1/settings": { sections: [] },
      "/api/v1/system": { vram_total_mb: 6141 },
    }));
    renderAt("/runs/new?from=job_0067&batch_size=64&version=ds_a");

    expect(await screen.findByText(/Prefilled from/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("64")).toBeInTheDocument();
    expect(screen.getByDisplayValue("30")).toBeInTheDocument();
    expect(screen.getByText(/batch_size was 128 in/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GPU" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("SCR-09 study part", () => {
  const LR: RJSFSchema = { type: "number", exclusiveMinimum: 0, maximum: 1, default: 0.001 };
  const BATCH: RJSFSchema = { type: "integer", minimum: 1, maximum: 4096, default: 128 };
  const TRAIN: RJSFSchema = {
    type: "object",
    properties: {
      model: {}, epochs: { type: "integer", minimum: 1, maximum: 1000, default: 10 }, batch_size: BATCH, learning_rate: LR,
      seed: { type: "integer", minimum: 0, default: 0 }, device: { enum: ["auto"], default: "auto" },
    },
  };

  test("test_HPO_1_1_a_marker_is_refused_on_its_row_by_the_fields_own_rules", () => {
    const lr = LR;
    expect(specProblem(lr, { scale: "log", low: 0, high: 0.1 })).toBe("A log scale needs a low above 0");
    expect(specProblem(lr, { scale: "linear", low: 0.5, high: 2 })).toMatch(/Outside the field's range/);
    expect(specProblem(BATCH, { scale: "choices", choices: [64] })).toBe("Choose at least two values");
    expect(specProblem(BATCH, { scale: "choices", choices: [64, 8192] })).toMatch(/Each must be 1–4096/);
    expect(toMarker("/config/learning_rate", lr, { scale: "log", low: 1e-4, high: 1e-1 })).toEqual({ path: "/config/learning_rate", type: "log_float", low: 1e-4, high: 1e-1 });
    expect(toMarker("/config/batch_size", BATCH, { scale: "linear", low: 32, high: 256, step: 32 })).toEqual({ path: "/config/batch_size", type: "int", low: 32, high: 256, step: 32 });
  });

  test("test_HPO_1_1_the_vram_check_takes_the_widest_layer_in_the_space", () => {
    const worst = worstGraph(GRAPH, { "/config/model/graph/nodes/c1/layer/out_channels": { scale: "linear", low: 8, high: 512 } });
    expect(worst.nodes[0]?.layer.out_channels).toBe(512);
    expect(GRAPH.nodes[0]?.layer.out_channels).toBe(32);
  });

  test("test_HPO_1_1_tuning_a_field_starts_a_study_with_its_marker", async () => {
    saveDraft("ds_a", GRAPH);
    const calls = stubApi(routes({
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
      "/api/v1/config/schemas/train": TRAIN,
      "/api/v1/settings": { sections: [] },
      "/api/v1/system": { vram_total_mb: 6141 },
      "/api/v1/ml/estimators/schema": {},
      "/api/v1/jobs": { id: "job_0100", kind: "hpo_study", stages: [{ config: { study_id: "st_abc" } }] },
    }));
    const router = renderAt("/runs/new?version=ds_a");

    fireEvent.click(await screen.findByRole("button", { name: "Tune learning_rate" }));

    expect(screen.getByRole("button", { name: "Tune learning_rate" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("learning_rate, low")).toBeInTheDocument();
    const card = screen.getByRole("region", { name: "Tune with a study" });
    expect(within(card).getByText("log float")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "New study" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Start study/ }));

    await waitFor(() => { expect(router.state.location.pathname).toBe("/studies/st_abc"); });
    const post = calls.find((c) => c.url.pathname === "/api/v1/jobs");
    expect(post?.body).toMatchObject({
      kind: "hpo_study", dataset_version: "ds_a", objective: "val_accuracy", n_trials: 20, timeout_s: 3600, pruning: true,
      space: [{ path: "/config/learning_rate", type: "log_float", low: 0.0001, high: 0.01 }],
      config: { model: { name: "graph" }, epochs: 10 },
    });
  });

  test("test_HPO_1_1_a_refused_marker_from_the_server_lands_on_its_row", async () => {
    saveDraft("ds_a", GRAPH);
    stubApi(routes({
      "/api/v1/model-specs/validate": { dataset: ends("ds_a", [3, 32, 32], 10), report: report() },
      "/api/v1/config/schemas/train": TRAIN,
      "/api/v1/settings": { sections: [] },
      "/api/v1/system": { vram_total_mb: 6141 },
      "/api/v1/ml/estimators/schema": {},
      "/api/v1/jobs": Response.json(
        { type: "about:blank", title: "Validation failed", status: 422, code: "validation_error", detail: "The search space has problems (HPO-1.1).", errors: [{ loc: ["space", 0, "path"], msg: "no value is valid with the fixed fields", code: "invalid" }] },
        { status: 422 },
      ),
    }));
    renderAt("/runs/new?version=ds_a");

    fireEvent.click(await screen.findByRole("button", { name: "Tune learning_rate" }));
    fireEvent.click(screen.getByRole("button", { name: /Start study/ }));

    const card = screen.getByRole("region", { name: "Tune with a study" });
    expect(await within(card).findByText("no value is valid with the fixed fields")).toBeInTheDocument();
  });
});
