// SCR-11 v2 (EVAL-1.3, REG-1.4; mockup SCR-11v2_v1): the comparison on one test split, its
// picker with Score on test, and Try it for a table record and a text.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { bySpread, CompareScreen, overlapText } from "./CompareScreen";
import { RecordTry, TextTry } from "./TryValues";

const SPLIT = "ds_6448d354c9c6";
const FACTS = { version_id: SPLIT, dataset: "wine", dataset_type: "tabular", test_rows: 18, task: "classification", classes: ["class_0", "class_1", "class_2"] };

function column(id: string, accuracy: number, correct: number, low: number, high: number, f1: Record<string, number>) {
  return {
    id, lineage_id: `ml_${id}`, lineage_name: `${id} · wine`, kind: "network", model: id, parameters: 836, trained_at: "2026-11-20T12:46:00Z",
    job_id: "job_0091", weights_status: "present", parent_version_id: "mv_base", parent_is_base: true, study_id: null,
    metrics: { accuracy, macro_f1: accuracy }, correct, accuracy_low: low, accuracy_high: high, per_class_f1: f1,
  };
}

const WINE = {
  split: FACTS,
  versions: [
    column("mv_50029d931e06", 0.944444, 17, 0.742427, 0.990125, { class_0: 0.923, class_1: 0.923, class_2: 1 }),
    column("mv_83c38a0d9774", 1, 18, 0.824121, 1, { class_0: 1, class_1: 1, class_2: 1 }),
    column("mv_310c2f892167", 1, 18, 0.824121, 1, { class_0: 1, class_1: 1, class_2: 1 }),
  ],
  best: { accuracy: ["mv_83c38a0d9774", "mv_310c2f892167"], macro_f1: ["mv_83c38a0d9774", "mv_310c2f892167"] },
  overlap: true,
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/models/compare", element: <CompareScreen /> }, { path: "/jobs/:id", element: <p>job</p> }], { initialEntries: [path] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-11 v2 comparison", () => {
  test("test_EVAL_1_3_three_models_compared_in_one_view_with_intervals_and_the_best", async () => {
    stubApi({ "/api/v1/models/compare": WINE });
    renderAt(`/models/compare?split=${SPLIT}&v=mv_50029d931e06&v=mv_83c38a0d9774&v=mv_310c2f892167`);

    const table = await screen.findByRole("table", { name: "Test metrics by version" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(4);
    expect(within(table).getByText("0.742 – 0.990")).toBeInTheDocument();
    expect(within(table).getAllByText("best")).toHaveLength(4); // two ties share it, on two metrics
    expect(within(table).getByText("17 of 18")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Accuracy intervals/ })).toBeInTheDocument();
    expect(screen.getByText(/This split cannot tell these three apart/)).toHaveTextContent("With 18 test rows one mistake moves accuracy by 0.056.");

    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByRole("table", { name: "Accuracy and its Wilson 95 % interval" })).toBeInTheDocument();
  });

  test("test_EVAL_1_3_a_version_tested_elsewhere_is_named_and_removable", async () => {
    stubApi({
      "/api/v1/models/compare": Response.json(
        { code: "validation_error", title: "Invalid", status: 422, detail: "x", errors: [{ loc: ["v", 1], msg: "mv_a4a0fb846812 was tested on ds_f8e67211dd94, not ds_6448d354c9c6", code: "invalid" }] },
        { status: 422 },
      ),
    });
    const router = renderAt(`/models/compare?split=${SPLIT}&v=mv_50029d931e06&v=mv_a4a0fb846812`);

    expect(await screen.findByText("These versions were not tested on one split")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove it" }));
    await waitFor(() => { expect(router.state.location.search).toBe(`?split=${SPLIT}&v=mv_50029d931e06`); });
  });

  test("test_EVAL_1_3_the_picker_offers_score_on_test_once_and_says_why_not", async () => {
    const calls = stubApi({
      "/api/v1/models/compare/candidates": {
        split: { ...FACTS, version_id: "ds_f8e67211dd94", dataset: "cifar10", test_rows: 5963 },
        items: [
          { id: "mv_a4a0fb846812", lineage_id: "ml_319245b543d4", lineage_name: "small_cnn · cifar10", test_metric: "accuracy", test_value: 0.81, weights_status: "present", trained_at: null, study_id: "st_8293da6ebe0e", scoring_job_id: null, can_score: false, why_not: null },
          { id: "mv_a014046ec3b5", lineage_id: "ml_7d806d9897fa", lineage_name: "small_cnn · cifar10", test_metric: null, test_value: null, weights_status: "present", trained_at: null, study_id: null, scoring_job_id: null, can_score: true, why_not: null },
          { id: "mv_2a7c0b4c8b72", lineage_id: "ml_7d806d9897fa", lineage_name: "small_cnn · cifar10", test_metric: null, test_value: null, weights_status: "weights_deleted", trained_at: null, study_id: null, scoring_job_id: null, can_score: false, why_not: "weights deleted: cannot be scored" },
        ],
      },
      "/api/v1/model-versions/mv_a014046ec3b5/score-test": { id: "job_0110", kind: "score_test" },
    });
    renderAt("/models/compare?split=ds_f8e67211dd94&v=mv_a4a0fb846812");

    fireEvent.click(await screen.findByRole("button", { name: "Choose versions" }));
    const dialog = await screen.findByRole("dialog", { name: "Add or remove versions" });
    await within(dialog).findByText("computed once");
    expect(within(dialog).getByText(/ml_319245b543d4/)).toBeInTheDocument(); // two lineages share the name
    expect(within(dialog).getByRole("checkbox", { name: /mv_2a7c0b4c8b72, weights deleted/ })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: /^Compare/ })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: /Score on test/ }));
    await waitFor(() => { expect(calls.some((c) => c.method === "POST" && c.url.pathname.endsWith("/mv_a014046ec3b5/score-test"))).toBe(true); });
  });

  test("test_EVAL_1_3_the_overlap_note_names_only_the_versions_that_overlap", () => {
    const three = { ...WINE, split: { ...FACTS, test_rows: 5963 }, versions: [
      column("mv_a4a0fb846812", 0.81, 4829, 0.8, 0.82, {}), column("mv_7f950100ab48", 0.831, 4956, 0.821, 0.84, {}), column("mv_a014046ec3b5", 0.832, 4962, 0.822, 0.841, {}),
    ] };
    expect(overlapText(three as never)).toBe("Some intervals overlap: this split cannot tell mv_7f950100ab48 and mv_a014046ec3b5 apart. The others differ by more than chance on 5,963 rows.");
  });

  test("test_EVAL_1_3_per_class_f1_puts_the_largest_spread_first_and_keeps_twenty", () => {
    const many = Object.fromEntries(Array.from({ length: 25 }, (_, i) => [`c${String(i)}`, i / 25]));
    const flat = Object.fromEntries(Array.from({ length: 25 }, (_, i) => [`c${String(i)}`, 0]));
    const order = bySpread([{ ...WINE.versions[0], per_class_f1: many }, { ...WINE.versions[1], per_class_f1: flat }] as never);
    expect(order).toHaveLength(20);
    expect(order[0]).toBe("c24");
  });
});

describe("SCR-11 v2 Try it", () => {
  function renderTry(node: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  }
  const ROWS = { items: [{ row_key: "wine.csv#0000021", label: "class_0", record: JSON.stringify({ alcohol: 14.06, colour: "red", hue: 1.09 }) }], matched: 1, next_cursor: null };

  test("test_REG_1_4_a_record_from_a_test_row_is_sent_and_its_label_compared", async () => {
    const calls = stubApi({
      [`/api/v1/versions/${SPLIT}/rows`]: ROWS,
      "/api/v1/models/mv_50029d931e06/predict": { version_id: "mv_50029d931e06", lineage_id: "ml_x", device: "gpu", fallback_reason: null, task: "classification", top: [{ class: "class_0", probability: 1 }, { class: "class_1", probability: 0 }], value: null, cluster: null, components: null, input: null, load_ms: 11253.7, total_ms: 12973 },
    });
    renderTry(<RecordTry versionId="mv_50029d931e06" datasetVersion={SPLIT} />);

    const fill = await screen.findByRole("button", { name: /Fill from a test row/ });
    await waitFor(() => { expect(fill).toBeEnabled(); });
    fireEvent.click(fill);
    expect(screen.getByLabelText("alcohol")).toHaveValue("14.06");
    fireEvent.click(screen.getByRole("button", { name: "Predict" }));

    expect(await screen.findByText(/This row's label/)).toHaveTextContent("class_0 · matches");
    expect(screen.getByText(/Served on the GPU/)).toHaveTextContent("Served on the GPU · cold start 11.3 s · 13.0 s");

    fireEvent.change(screen.getByLabelText("hue"), { target: { value: "" } }); // no longer that row
    fireEvent.click(screen.getByRole("button", { name: "Predict" }));
    await waitFor(() => { expect(calls.filter((c) => c.method === "POST")).toHaveLength(2); });
    const sent = calls.filter((c) => c.method === "POST")[1]?.body as { record: Record<string, unknown> };
    expect(sent.record).toEqual({ alcohol: 14.06, colour: "red", hue: null }); // an empty field is imputed
    expect(screen.queryByText(/This row's label/)).toBeNull();
  });

  test("test_REG_1_4_a_refused_record_marks_its_fields", async () => {
    stubApi({
      [`/api/v1/versions/${SPLIT}/rows`]: ROWS,
      "/api/v1/models/mv_50029d931e06/predict": Response.json(
        { code: "wrong_input", title: "Wrong input", status: 422, detail: "x", errors: [{ loc: ["body", "record", "alcohol"], msg: "'strong' is not a finite number", code: "wrong_input" }] },
        { status: 422 },
      ),
    });
    renderTry(<RecordTry versionId="mv_50029d931e06" datasetVersion={SPLIT} />);

    fireEvent.change(await screen.findByLabelText("alcohol"), { target: { value: "strong" } });
    fireEvent.click(screen.getByRole("button", { name: "Predict" }));

    expect(await screen.findByText("'strong' is not a finite number")).toBeInTheDocument();
    expect(screen.getByLabelText("alcohol")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("The record does not fit this model's table.");
  });

  test("test_REG_1_4_a_text_says_how_it_was_read", async () => {
    stubApi({
      "/api/v1/versions/ds_53f72c42aa5a/rows": { items: [{ row_key: "c#1", label: "person", text: "A man riding a wave on top of a surfboard." }], matched: 1, next_cursor: null },
      "/api/v1/models/mv_2eb4cd656334/predict": { version_id: "mv_2eb4cd656334", lineage_id: "ml_x", device: "gpu", fallback_reason: null, task: "classification", top: [{ class: "person", probability: 0.917331 }, { class: "surfboard", probability: 0.034831 }], value: null, cluster: null, components: null, input: { tokens: 11, unknown: 0, truncated_at: null }, load_ms: 1232.9, total_ms: 2218.6 },
    });
    renderTry(<TextTry versionId="mv_2eb4cd656334" datasetVersion="ds_53f72c42aa5a" />);

    const fill = await screen.findByRole("button", { name: /Fill from a test row/ });
    await waitFor(() => { expect(fill).toBeEnabled(); });
    fireEvent.click(fill);
    fireEvent.click(screen.getByRole("button", { name: "Predict" }));

    expect(await screen.findByText("11 tokens · 0 unknown · not cut")).toBeInTheDocument();
    expect(screen.getByText(/This row's label/)).toHaveTextContent("person · matches");
  });
});
