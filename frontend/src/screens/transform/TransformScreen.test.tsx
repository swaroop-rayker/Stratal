// SCR-15 transform editor (M11b; approved hi-fi SCR-15_v1; DATA-7.1 to DATA-7.5, UI-2.3).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { stubApi } from "../../test/api";
import { problemsByOp, TransformScreen } from "./TransformScreen";

type Version = Schemas["Version"];

const RAW: Version = {
  version_id: "ds_b3f4ff09de8a", dataset: "wine", stage: "ingest", parent_version_id: null, content_hash: "a".repeat(64),
  config_hash: "b".repeat(64), config: {}, dtype: "tabular", row_count: 178,
  counts: { accepted: 178, rejected: 0, duplicate: 0, filtered: 0 }, manifest_path: "manifests/ds_b3f4ff09de8a.parquet",
  manifest_bytes: 36_493, new_blob_bytes: 0, schema_version: 1, code_version: null,
  created_by_job_id: "job_0061", created_by_job_state: "completed", created_at: "2026-11-19T14:50:53Z",
};
const COLS = [
  { name: "alcohol", kind: "numeric", role: "feature" },
  { name: "ash", kind: "numeric", role: "feature" },
  { name: "magnesium", kind: "numeric", role: "feature" },
  { name: "od280/od315_of_diluted_wines", kind: "numeric", role: "feature" },
  { name: "cultivar", kind: "categorical", role: "target" },
];
const SCHEMA = {
  $defs: {
    Drop: { properties: { op: { const: "drop" }, columns: { type: "array" } } },
    Rename: { properties: { op: { const: "rename" }, from: { type: "string" }, to: { type: "string" } } },
    SetKind: { properties: { op: { const: "set_kind" }, column: { type: "string" }, kind: { enum: ["numeric", "categorical"] } } },
    SetEncoding: { properties: { op: { const: "set_encoding" }, column: { type: "string" }, encoding: { enum: ["one_hot", "embedding", "ordinal", "ignore"] }, order: { anyOf: [{ type: "array" }, { type: "null" }] } } },
  },
};
const row = (key: number, values: Record<string, unknown>) => ({ row_key: `wine.csv#${String(key).padStart(7, "0")}`, status: "accepted", reason_code: null, reason_detail: null, values });

function preview(ops: { op: string }[]) {
  const dropped = ops.some((o) => o.op === "drop");
  const cols = dropped ? COLS.filter((c) => c.name !== "ash") : COLS;
  return {
    version_id: RAW.version_id,
    columns: cols,
    steps: ops.map((o, i) => ({ index: i, op: o.op, summary: o.op === "drop" ? "removes ash · 4 → 3 features" : "magnesium: numeric → categorical · 0 cast_failed", columns: cols, cast_failed: 0, filtered: 0, filtered_null: 0, examples: [] })),
    rows: [row(1, { alcohol: 14.23, ...(dropped ? {} : { ash: 2.43 }), magnesium: 127, "od280/od315_of_diluted_wines": 3.92, cultivar: "class_0" })],
    counts: { rows: 1, by_status: { accepted: 1 }, readmitted: 0, cast_failed: 0, filtered: 0 },
  };
}

function setup(extra: Record<string, unknown> = {}) {
  return stubApi({
    [`/api/v1/versions/${RAW.version_id}`]: RAW,
    [`/api/v1/versions/${RAW.version_id}/lineage`]: { version_id: RAW.version_id, ancestors: [], descendants: [], in_progress: [] },
    "/api/v1/config/schemas/transform": SCHEMA,
    "/api/v1/jobs": { id: "job_0099" },
    ...extra,
  });
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [{ path: "/versions/:versionId/transform", element: <TransformScreen /> }, { path: "/jobs/:jobId", element: <p>SCR-04</p> }],
    { initialEntries: [path] },
  );
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

/** fetch answers the preview from its body's operations, and 422 for a dropped target. */
function stubPreview(calls: ReturnType<typeof stubApi>) {
  const real = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/transform/preview")) {
      const body = (await request.clone().json()) as { operations: { op: string; columns?: string[] }[] };
      calls.push({ method: "POST", url, body });
      if (body.operations.some((o) => o.op === "drop" && o.columns?.includes("cultivar"))) {
        return Response.json({ code: "validation_error", detail: "1 operation problem(s)", title: "Validation failed", status: 422, suggested_action: null, request_id: null, errors: [{ loc: ["stages", "transform", "operations", 0, "columns"], msg: "cultivar is the target and cannot be dropped", code: "target_dropped" }] }, { status: 422 });
      }
      return Response.json(preview(body.operations));
    }
    return real(request);
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("SCR-15", () => {
  test("test_DATA_7_5_operations_form_generated_from_schema", async () => {
    const calls = setup({ [`/api/v1/versions/ds_7a1e00c0ffee`]: { ...RAW, version_id: "ds_7a1e00c0ffee", stage: "transform", config: { operations: [{ op: "drop", columns: ["ash"] }, { op: "set_kind", column: "magnesium", kind: "categorical" }] } } });
    stubPreview(calls);
    renderAt(`/versions/${RAW.version_id}/transform?from=ds_7a1e00c0ffee`);

    const first = await screen.findByRole("group", { name: "Operation 1: drop" });
    expect(await within(first).findByText("ash")).toBeInTheDocument();
    const second = screen.getByRole("group", { name: "Operation 2: set_kind" });
    expect(within(second).getByRole("combobox", { name: "set_kind kind" })).toHaveValue("categorical");
    expect(screen.getByText(/Loaded the 2 operations of/)).toBeInTheDocument();
    expect(await screen.findByText("removes ash · 4 → 3 features")).toBeInTheDocument();
  });

  test("test_DATA_7_5_column_select_lists_columns_at_that_step", async () => {
    const calls = setup({ [`/api/v1/versions/ds_7a1e00c0ffee`]: { ...RAW, version_id: "ds_7a1e00c0ffee", stage: "transform", config: { operations: [{ op: "drop", columns: ["ash"] }, { op: "set_kind", column: "magnesium", kind: "categorical" }] } } });
    stubPreview(calls);
    renderAt(`/versions/${RAW.version_id}/transform?from=ds_7a1e00c0ffee`);

    await screen.findByText("removes ash · 4 → 3 features");
    const select = within(screen.getByRole("group", { name: "Operation 2: set_kind" })).getByRole("combobox", { name: "set_kind column" });
    await waitFor(() => { expect(within(select).queryByRole("option", { name: "ash" })).not.toBeInTheDocument(); });
    expect(within(select).getByRole("option", { name: "magnesium" })).toBeInTheDocument();
  });

  test("test_DATA_7_5_refused_operation_message_on_its_field", async () => {
    const calls = setup();
    stubPreview(calls);
    sessionStorage.setItem(`stratal.transform.${RAW.version_id}`, JSON.stringify([{ op: "drop", columns: ["cultivar"] }]));
    renderAt(`/versions/${RAW.version_id}/transform`);

    expect(await screen.findByText("columns: cultivar is the target and cannot be dropped")).toBeInTheDocument();
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("1 problem")).toBeInTheDocument();
  });

  test("test_DATA_7_5_run_posts_from_version_operations_and_until_stage", async () => {
    const calls = setup();
    stubPreview(calls);
    sessionStorage.setItem(`stratal.transform.${RAW.version_id}`, JSON.stringify([{ op: "drop", columns: ["ash"] }]));
    const router = renderAt(`/versions/${RAW.version_id}/transform`);

    await screen.findByText("removes ash · 4 → 3 features");
    fireEvent.click(screen.getByLabelText("Transform only"));
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => { expect(router.state.location.pathname).toBe("/jobs/job_0099"); });
    const job = calls.find((c) => c.url.pathname === "/api/v1/jobs" && c.method === "POST");
    expect(job?.body).toEqual({ kind: "pipeline", from_version: RAW.version_id, stages: { transform: { operations: [{ op: "drop", columns: ["ash"] }] } }, until_stage: "transform" });
    expect(sessionStorage.getItem(`stratal.transform.${RAW.version_id}`)).toBeNull();
  });

  test("test_DATA_7_5_empty_list_shows_raw_rows_and_run_says_why", async () => {
    const calls = setup();
    stubPreview(calls);
    renderAt(`/versions/${RAW.version_id}/transform`);
    expect(await screen.findByText("No operations yet.")).toBeInTheDocument();
    expect(await screen.findByText("wine.csv#0000001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Add an operation first")).toBeInTheDocument();
  });

  test("test_DATA_7_1_not_a_raw_table_points_to_its_raw_version", async () => {
    const clean = { ...RAW, version_id: "ds_499bfa2ee3d3", stage: "clean_table", parent_version_id: RAW.version_id };
    setup({
      "/api/v1/versions/ds_499bfa2ee3d3": clean,
      "/api/v1/versions/ds_499bfa2ee3d3/lineage": { version_id: clean.version_id, ancestors: [RAW], descendants: [], in_progress: [] },
    });
    renderAt("/versions/ds_499bfa2ee3d3/transform");
    expect(await screen.findByText("Transforms apply to a table's raw version.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: `Transform ${RAW.version_id}` })).toBeInTheDocument();
  });

  test("test_DATA_7_2_problems_map_to_their_operation_and_field", () => {
    const map = problemsByOp({
      status: 422, code: "validation_error", title: "", detail: "", suggested_action: null, request_id: null,
      errors: [
        { loc: ["stages", "transform", "operations", 0, "columns"], msg: "cultivar is the target", code: "target_dropped" },
        { loc: ["stages", "transform", "operations", 1, "rename", "to"], msg: "String should have at least 1 character", code: "string_too_short" },
        { loc: ["stages", "transform", "operations", 2], msg: "scale is fitted on the data", code: "fitted_operation" },
      ],
    });
    expect(map.get(0)?.get("columns")).toBe("cultivar is the target");
    expect(map.get(1)?.get("to")).toBe("required");
    expect(map.get(2)?.get("op")).toBe("scale is fitted on the data");
  });
});
