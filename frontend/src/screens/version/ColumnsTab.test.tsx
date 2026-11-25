// SCR-03 v4 Columns tab (M11b; approved hi-fi SCR-03columns_v1; DATA-7.5, DATA-7.3).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { emptyList, stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { VersionScreen } from "./VersionScreen";

type Version = Schemas["Version"];

function version(id: string, stage: string, parent: string | null, extra: Partial<Version> = {}): Version {
  return {
    version_id: id, dataset: "wine", stage, parent_version_id: parent, content_hash: "a".repeat(64),
    config_hash: "b".repeat(64), config: {}, dtype: "tabular", row_count: 178,
    counts: { accepted: 178, rejected: 0, duplicate: 0, filtered: 0 }, manifest_path: `manifests/${id}.parquet`,
    manifest_bytes: 36_493, new_blob_bytes: 0, schema_version: 1, code_version: null,
    created_by_job_id: "job_0061", created_by_job_state: "completed", created_at: "2026-11-19T14:50:53Z", ...extra,
  };
}

const RAW = version("ds_b3f4ff09de8a", "ingest", null);
const OPS = [
  { op: "drop", columns: ["ash"] },
  { op: "set_kind", column: "magnesium", kind: "categorical" },
  { op: "set_encoding", column: "magnesium", encoding: "embedding" },
];
const TRANSFORMED = version("ds_7a1e00c0ffee", "transform", RAW.version_id, { config: { operations: OPS, algorithm_version: 1 } });
const SPLIT = version("ds_9b2f00c0ffee", "split", "ds_8c3e00c0ffee");
const CLEAN = version("ds_8c3e00c0ffee", "clean_table", TRANSFORMED.version_id);

const numeric = (name: string, min: number, median: number, max: number, distinct: number) => ({
  name, kind: "numeric", role: "feature", encoding: null, order: null, missing: 0, distinct, distinct_capped: false,
  distribution: {
    type: "numeric", min, median, max, sparkline: [1, 9, 9, 22, 23, 18, 23, 22, 25, 13, 11, 2],
    histogram: { edges: Array.from({ length: 21 }, (_, k) => min + ((max - min) / 20) * k), counts: [1, 3, 7, 36, 16, 24, 29, 14, 14, 10, 11, 4, 3, 2, 1, 1, 0, 1, 0, 1] },
  },
});
const COLUMNS = {
  version_id: RAW.version_id, task: "classification", target: "cultivar", counted: 178, not_counted: {}, distinct_cap: 10_000,
  columns: [
    numeric("alcohol", 11.03, 13.05, 14.83, 126),
    numeric("magnesium", 70, 98, 162, 53),
    { name: "cultivar", kind: "categorical", role: "target", encoding: null, order: null, missing: 0, distinct: 3, distinct_capped: false, distribution: { type: "categorical", top: [{ value: "class_1", count: 71 }, { value: "class_0", count: 59 }, { value: "class_2", count: 48 }], more: 0 } },
  ],
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [{ path: "/versions/:versionId", element: <VersionScreen /> }, { path: "/versions/:versionId/transform", element: <p>SCR-15</p> }],
    { initialEntries: [path] },
  );
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

function routes(v: Version, ancestors: Version[], columns: unknown) {
  return {
    [`/api/v1/versions/${v.version_id}`]: v,
    [`/api/v1/versions/${v.version_id}/lineage`]: { version_id: v.version_id, ancestors, descendants: [], in_progress: [] },
    [`/api/v1/versions/${v.version_id}/stats`]: { version_id: v.version_id, row_count: 178, by_status: { accepted: 178 }, reasons: [], by_stage: {}, by_split: null, clip_score: null, size_bytes: null },
    [`/api/v1/versions/${v.version_id}/rows`]: { items: [], matched: 0, next_cursor: null },
    [`/api/v1/versions/${v.version_id}/columns`]: columns,
    [`/api/v1/versions/${v.version_id}/models`]: { items: [] },
    "/api/v1/jobs": emptyList,
  };
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-03 Columns tab", () => {
  test("test_DATA_7_5_columns_tab_shows_each_column_and_expands_one", async () => {
    stubApi(routes(RAW, [], COLUMNS));
    renderAt(`/versions/${RAW.version_id}?tab=columns`);

    const table = await screen.findByRole("table", { name: /Columns of ds_b3f4ff09de8a over 178 accepted rows/ });
    expect(screen.getByText((_, el) => el?.tagName === "SPAN" && el.textContent === "over 178 accepted rows")).toBeInTheDocument();
    const magnesium = within(table).getByText("magnesium").closest("tr") as HTMLElement;
    expect(magnesium).toHaveTextContent("70 · 98 · 162");
    expect(within(magnesium).getByRole("img", { name: /magnesium: min 70, median 98, max 162/ })).toBeInTheDocument();
    const target = within(table).getByText("cultivar").closest("tr") as HTMLElement;
    expect(target).toHaveTextContent("target");
    expect(target).toHaveTextContent("class_1");

    fireEvent.click(magnesium);
    expect(magnesium).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("img", { name: /magnesium, 20 bins from 70 to 162/ })).toBeInTheDocument();
    expect(screen.getByText("median 98")).toBeInTheDocument();
    expect(screen.getAllByRole("table").length).toBeGreaterThan(2); // the bins as tables
  });

  test("test_DATA_7_5_columns_tab_only_on_tabular_versions", async () => {
    const images = version("ds_3f6fcc420a27", "split", null, { dtype: "labelled_images" });
    stubApi(routes(images, [], COLUMNS));
    renderAt(`/versions/${images.version_id}`);
    await screen.findByRole("tab", { name: /Rows/ });
    expect(screen.queryByRole("tab", { name: "Columns" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Transform…/ })).not.toBeInTheDocument();
  });

  test("test_DATA_7_5_a_transformed_version_lists_its_operations_and_edits_them_on_scr15", async () => {
    stubApi(routes(SPLIT, [CLEAN, TRANSFORMED, RAW], { ...COLUMNS, version_id: SPLIT.version_id }));
    const router = renderAt(`/versions/${SPLIT.version_id}?tab=columns`);

    expect(await screen.findByText("drop ash")).toBeInTheDocument();
    expect(screen.getByText("set_encoding magnesium embedding")).toBeInTheDocument();
    const edits = screen.getAllByRole("button", { name: "Edit the transform…" });
    fireEvent.click(edits[edits.length - 1] as HTMLElement);
    expect(router.state.location.pathname).toBe(`/versions/${RAW.version_id}/transform`);
    expect(router.state.location.search).toBe(`?from=${TRANSFORMED.version_id}`);
  });

  test("test_DATA_7_5_rows_not_counted_link_filters_rows", async () => {
    stubApi(routes(RAW, [], { ...COLUMNS, counted: 177, not_counted: { missing_target: 1 } }));
    const router = renderAt(`/versions/${RAW.version_id}?tab=columns`);

    const link = await screen.findByRole("button", { name: "Show them" });
    expect(link.closest("span")).toHaveTextContent("1 rows not counted (missing_target)");
    fireEvent.click(link);
    expect(router.state.location.search).toContain("reason=missing_target");
    expect(router.state.location.search).not.toContain("tab=");
  });

  test("test_DATA_7_5_a_raw_table_offers_transform", async () => {
    stubApi(routes(RAW, [], COLUMNS));
    const router = renderAt(`/versions/${RAW.version_id}`);
    const button = (await screen.findAllByRole("button", { name: "Transform…" }))[0] as HTMLElement;
    fireEvent.click(button);
    expect(router.state.location.pathname).toBe(`/versions/${RAW.version_id}/transform`);
  });
});
