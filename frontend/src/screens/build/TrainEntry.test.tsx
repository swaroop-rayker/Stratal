// The training entry (SCREENS.md v3, mockup SCR-09v2_v1; ML-1.1, BUILD-1.3, UI-2.1): the Train a
// model drawer picks the split first, then the kinds that split allows, and SCR-09 receives the
// kind already chosen.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { saveDraft, type Graph } from "../../lib/graph";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { RunScreen } from "./RunScreen";
import { TrainDrawer } from "./TrainDrawer";

const TYPES = [
  { name: "labelled_images", title: "Labelled images", input_kind: "image", families: ["mlp", "cnn"], default_metrics: [], trainable: true, note: "" },
  { name: "tabular", title: "Tabular", input_kind: "vector", families: ["mlp"], default_metrics: [], trainable: true, note: "" },
];

const WINE_ENDS = {
  version_id: "ds_6448d354c9c6", dataset_type: "tabular", input: { kind: "vector", dims: [13], vocab: null }, input_text: "vector 13",
  output_size: 3, classes: ["class_0", "class_1", "class_2"], families: ["mlp"], trainable: true, problem: null,
};
const CIFAR_ENDS = {
  version_id: "ds_f8e67211dd94", dataset_type: "labelled_images", input: { kind: "image", dims: [3, 32, 32], vocab: null }, input_text: "image 3×32×32",
  output_size: 10, classes: ["airplane", "automobile", "bird", "cat", "deer", "dog", "frog", "horse", "ship", "truck"], families: ["mlp", "cnn"], trainable: true, problem: null,
};
const SPLITS = { split: { splits: { train: { rows: 142 }, val: { rows: 18 }, test: { rows: 18 } } } };

const GRAPH: Graph = {
  spec_version: 1, label: "my_cnn",
  nodes: [{ id: "g1", layer: { type: "global_avg_pool" } }],
  edges: [["input", "g1"], ["g1", "output"]],
};

function routes(ends: object, extra: Record<string, unknown> = {}) {
  return {
    "/api/v1/dataset-types": TYPES,
    "/api/v1/datasets": { items: [{ slug: "wine" }, { slug: "cifar10" }], next_cursor: null },
    "/api/v1/datasets/wine/versions": {
      items: [
        { version_id: "ds_6448d354c9c6", dataset: "wine", stage: "split", dtype: "tabular", row_count: 178, created_at: "2026-11-19T14:51:02Z" },
        { version_id: "ds_499bfa2ee3d3", dataset: "wine", stage: "clean_table", dtype: "tabular", row_count: 178, created_at: "2026-11-19T14:50:59Z" },
      ],
      next_cursor: null,
    },
    "/api/v1/versions/ds_6448d354c9c6": { version_id: "ds_6448d354c9c6", dataset: "wine", stage: "split", dtype: "tabular", counts: SPLITS },
    "/api/v1/versions/ds_f8e67211dd94": { version_id: "ds_f8e67211dd94", dataset: "cifar10", stage: "split", dtype: "labelled_images", counts: {} },
    "/api/v1/model-specs/validate": { dataset: ends, report: null },
    "/api/v1/model-specs/schema": { layer: { $defs: {} }, graph: {}, max_nodes: 200, max_edges: 400 },
    "/api/v1/model-templates": [],
    "/api/v1/config/schemas/train": { type: "object", properties: { epochs: { type: "integer", default: 10 } } },
    "/api/v1/settings": { sections: [] },
    "/api/v1/system": { vram_total_mb: 6141 },
    "/api/v1/ml/estimators/schema": {
      random_forest: { title: "Random forest", tasks: ["classification", "regression"], schema: { type: "object", properties: { n_estimators: { type: "integer", minimum: 1, maximum: 2000, default: 100 } } } },
    },
    ...extra,
  };
}

function renderAt(path: string, drawer: { version?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/models", element: <TrainDrawer open version={drawer.version ?? null} onOpenChange={() => undefined} /> },
      { path: "/models/build", element: <p>builder</p> },
      { path: "/runs/new", element: <RunScreen /> },
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

describe("Train a model drawer", () => {
  test("test_UI_2_1_a_tabular_split_reaches_classical_mode_without_the_builder", async () => {
    stubApi(routes(WINE_ENDS));
    const router = renderAt("/models");
    const visited: string[] = [];
    router.subscribe((state) => { visited.push(state.location.pathname); });

    const dialog = await screen.findByRole("dialog", { name: "Train a model" });
    await within(dialog).findByRole("option", { name: "wine" });
    fireEvent.change(within(dialog).getByLabelText("Dataset"), { target: { value: "wine" } });
    const versionSelect = within(dialog).getByLabelText("Version to train on");
    await waitFor(() => { expect(within(versionSelect).getAllByRole("option")).toHaveLength(2); }); // the clean_table version is not offered
    fireEvent.change(versionSelect, { target: { value: "ds_6448d354c9c6" } });
    expect(await within(dialog).findByText("13 features → 3 classes")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /Next/ }));

    const classical = await within(dialog).findByRole("radio", { name: /Classical/ });
    expect(classical).toBeEnabled();
    fireEvent.click(classical);
    fireEvent.click(within(dialog).getByRole("button", { name: /Set up the run/ }));

    await waitFor(() => { expect(router.state.location.pathname).toBe("/runs/new"); });
    expect(router.state.location.search).toBe("?version=ds_6448d354c9c6&kind=classical");
    expect(await screen.findByLabelText("Model kind: Classical")).toBeInTheDocument();
    expect(await screen.findByLabelText("Estimator")).toHaveValue("random_forest");
    expect(visited).not.toContain("/models/build");
  });

  test("test_UI_2_1_a_non_tabular_split_shows_classical_disabled_with_its_reason", async () => {
    stubApi(routes(CIFAR_ENDS));
    renderAt("/models", { version: "ds_f8e67211dd94" });

    const dialog = await screen.findByRole("dialog", { name: "Train a model" });
    const classical = await within(dialog).findByRole("radio", { name: /Classical/ });
    expect(classical).toBeDisabled();
    expect(within(dialog).getByText("Classical models train on tables. This split holds labelled images.")).toBeInTheDocument();
    const network = within(dialog).getByRole("radio", { name: /Network/ });
    expect(network).toBeChecked();
    expect(within(dialog).getByRole("button", { name: /Open the builder/ })).toBeEnabled();
  });

  test("test_UI_2_1_runs_new_without_a_kind_opens_the_drawer_at_step_2", async () => {
    stubApi(routes(WINE_ENDS));
    renderAt("/runs/new?version=ds_6448d354c9c6");

    const dialog = await screen.findByRole("dialog", { name: "Train a model" });
    expect(within(dialog).getByText("Kind").closest("[aria-current]")).toHaveAttribute("aria-current", "step");
    expect(await within(dialog).findByRole("radio", { name: /Classical/ })).toBeEnabled();
    expect(within(dialog).getByRole("radio", { name: /Network/ })).toBeEnabled();
    expect(screen.getByText("Choose what to train first.")).toBeInTheDocument();
  });

  test("test_UI_2_1_change_to_classical_warns_that_the_draft_network_is_left_behind", async () => {
    saveDraft("ds_6448d354c9c6", GRAPH);
    stubApi(routes(WINE_ENDS));
    const router = renderAt("/runs/new?version=ds_6448d354c9c6&kind=network");

    fireEvent.click(await screen.findByRole("button", { name: "Change…" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the model kind" });
    fireEvent.click(await within(dialog).findByRole("radio", { name: /Classical/ }));
    expect(within(dialog).getByRole("status")).toHaveTextContent("The builder's draft for this split stays in the builder.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Use Classical" }));

    await waitFor(() => { expect(router.state.location.search).toBe("?version=ds_6448d354c9c6&kind=classical"); });
    expect(await screen.findByLabelText("Model kind: Classical")).toBeInTheDocument();
  });
});

// SCR-09's estimator list (addendum SCR-09v2-estimators_v1, approved 2026-11-22; ML-1.1,
// DATA-5.5): every estimator is listed, and the ones the split or a study cannot use are disabled
// with their reason instead of hidden. The schema's order, as GET /ml/estimators/schema serves it.
const NO_PARAMS = { type: "object", properties: {} };
const ESTIMATORS = {
  linear: { title: "Linear regression", tasks: ["regression"], schema: NO_PARAMS },
  ridge: { title: "Ridge regression", tasks: ["regression"], schema: NO_PARAMS },
  lasso: { title: "Lasso regression", tasks: ["regression"], schema: NO_PARAMS },
  logistic_regression: { title: "Logistic regression", tasks: ["classification"], schema: NO_PARAMS },
  random_forest: { title: "Random forest", tasks: ["classification", "regression"], schema: { type: "object", properties: { n_estimators: { type: "integer", minimum: 1, maximum: 2000, default: 100 } } } },
  naive_bayes: { title: "Naive Bayes", tasks: ["classification"], schema: NO_PARAMS },
  kmeans: { title: "k-means", tasks: ["clustering"], schema: { type: "object", properties: { n_clusters: { type: "integer", minimum: 2, maximum: 1000, default: 8 } } } },
  pca: { title: "PCA", tasks: ["projection"], schema: NO_PARAMS },
};
const DIABETES_ENDS = {
  version_id: "ds_de5f2ad338c9", dataset_type: "tabular", input: { kind: "vector", dims: [10], vocab: null }, input_text: "vector 10",
  output_size: 1, classes: [], families: ["mlp"], trainable: true, problem: null,
};
const DIABETES = {
  "/api/v1/versions/ds_de5f2ad338c9": {
    version_id: "ds_de5f2ad338c9", dataset: "diabetes", stage: "split", dtype: "tabular",
    counts: { split: { splits: { train: { rows: 354 }, val: { rows: 44 }, test: { rows: 44 } } } },
  },
};

describe("SCR-09's estimator list", () => {
  test("test_DATA_5_5_estimators_a_split_cannot_use_are_listed_disabled_with_their_reason", async () => {
    stubApi(routes(DIABETES_ENDS, { ...DIABETES, "/api/v1/ml/estimators/schema": ESTIMATORS }));
    renderAt("/runs/new?version=ds_de5f2ad338c9&kind=classical");

    const select = await screen.findByLabelText("Estimator");
    const logistic = await within(select).findByRole("option", { name: "logistic_regression · Logistic regression — classification only" });
    expect(logistic).toBeDisabled();
    expect(within(select).getByRole("group", { name: "Not for this split" })).toContainElement(logistic);
    expect(within(select).getByRole("option", { name: "naive_bayes · Naive Bayes — classification only" })).toBeDisabled();
    expect(within(select).getByRole("option", { name: "linear · Linear regression" })).toBeEnabled();
    expect(within(select).getByRole("group", { name: "For regression" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "kmeans · k-means" })).toBeEnabled();
    expect(select).toHaveValue("random_forest");
    await waitFor(() => {
      expect(select).toHaveAccessibleDescription(
        "Not for this split: logistic_regression and naive_bayes, which are classifiers; diabetes's target is a number. For a linear model, use linear, ridge or lasso.",
      );
    });
  });

  test("test_ML_1_1_logistic_regression_is_offered_on_a_classification_split", async () => {
    stubApi(routes(WINE_ENDS, { "/api/v1/ml/estimators/schema": ESTIMATORS }));
    renderAt("/runs/new?version=ds_6448d354c9c6&kind=classical");

    const select = await screen.findByLabelText("Estimator");
    const logistic = await within(select).findByRole("option", { name: "logistic_regression · Logistic regression" });
    expect(logistic).toBeEnabled();
    expect(within(select).getByRole("option", { name: "linear · Linear regression — regression only" })).toBeDisabled();
    fireEvent.change(select, { target: { value: "logistic_regression" } });
    expect(select).toHaveValue("logistic_regression");
    expect(screen.getByText(/^Logistic regression, after imputing/)).toBeInTheDocument();
    await waitFor(() => {
      expect(select).toHaveAccessibleDescription("Not for this split: linear, ridge and lasso, which predict a number; wine's target is a class.");
    });
  });

  test("test_DATA_5_5_turning_a_study_on_never_changes_the_selected_estimator", async () => {
    // E-017: with kmeans selected, turning a study on took it out of the list, and the select
    // showed logistic_regression while the form was still k-means's.
    stubApi(routes(WINE_ENDS, { "/api/v1/ml/estimators/schema": ESTIMATORS }));
    renderAt("/runs/new?version=ds_6448d354c9c6&kind=classical");

    const select = await screen.findByLabelText("Estimator");
    await within(select).findByRole("option", { name: "kmeans · k-means" });
    fireEvent.change(select, { target: { value: "kmeans" } });
    const turnOn = screen.getByRole("button", { name: "Turn on" });
    expect(turnOn).toHaveAttribute("aria-disabled", "true");
    expect(turnOn).toHaveAccessibleDescription("A study scores each fold against the target, and kmeans ignores it. Choose a supervised estimator to tune.");
    expect(screen.queryByRole("button", { name: "Tune n_clusters" })).not.toBeInTheDocument();
    fireEvent.click(turnOn);
    expect(screen.queryByRole("region", { name: "Tune with a study" })).not.toBeInTheDocument();
    expect(select).toHaveValue("kmeans");

    fireEvent.change(select, { target: { value: "random_forest" } });
    expect(screen.getByRole("button", { name: "Turn on" })).not.toHaveAttribute("aria-disabled");
    fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
    expect(await screen.findByRole("region", { name: "Tune with a study" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "kmeans · k-means — not in a study" })).toBeDisabled();
    expect(select).toHaveValue("random_forest");
    expect(select).toHaveAccessibleDescription(/Not in a study: kmeans and pca, which ignore the target a study scores against\.$/);
  });
});
