// SCR-03 v2 (M11; mockup SCR-03v2_v1): distributions raw against here (UI-2.2), the diff of
// two versions (DATA-3.5), models trained on a version (UI-2.1) and semantic clusters
// (DATA-2.3, DATA-4.8).
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
    version_id: id, dataset: "coco10k", stage, parent_version_id: parent, content_hash: "a".repeat(64),
    config_hash: "b".repeat(64), config: {}, dtype: "image_caption", row_count: 10_000,
    counts: { accepted: 9838, rejected: 60, duplicate: 8, filtered: 94 }, manifest_path: `manifests/${id}.parquet`,
    manifest_bytes: 10_000, new_blob_bytes: 0, schema_version: 1, code_version: null,
    created_by_job_id: "job_0013", created_by_job_state: "completed", created_at: "2026-11-16T14:01:00Z", ...extra,
  };
}

const SPLIT = version("ds_3f6fcc420a27", "split", "ds_db6bcccdb054");
const LABEL = version("ds_047b184c1f70", "label", "ds_f81d0d552a1a", {
  counts: { accepted: 9932, rejected: 60, duplicate: 8, filtered: 0, semantic: { clusters: 105, rows_in_clusters: 290, largest: 22, threshold: 0.9 } },
});
const STATS = {
  version_id: SPLIT.version_id, row_count: 10_000, by_status: { accepted: 9838, rejected: 60, duplicate: 8, filtered: 94 },
  reasons: [], by_stage: {}, by_split: { train: 7870, val: 984, test: 984 }, clip_score: null,
  size_bytes: { count: 0, mean: null, std: null, minimum: null, maximum: null, quantiles: {}, edges: [1, 2], histogram: [0], below: 0, above: 0 },
};
const five = (min: number, p10: number, median: number, p90: number, max: number, n: number) => ({ n, min, p10, median, p90, max, mean: median });

const DIST = {
  version_id: SPLIT.version_id, stage: "split", dtype: "image_caption", row_count: 10_000, accepted: 9838, removed: 162,
  funnel: [
    { stage: "ingest", version_id: "ds_5c8d4e6a081b", rows_in: 10_000, removed: [{ reason_code: "below_min_size", status: "rejected", rows: 60 }], rows_out: 9940, parts: null },
    { stage: "clean", version_id: "ds_3c8ee080a4c9", rows_in: 9940, removed: [{ reason_code: "near_duplicate", status: "duplicate", rows: 8 }], rows_out: 9932, parts: null },
    { stage: "label", version_id: "ds_047b184c1f70", rows_in: 9932, removed: [], rows_out: 9932, parts: null },
    { stage: "filter", version_id: "ds_db6bcccdb054", rows_in: 9932, removed: [{ reason_code: "clip_below_threshold", status: "filtered", rows: 94 }], rows_out: 9838, parts: null },
    { stage: "split", version_id: "ds_3f6fcc420a27", rows_in: 9838, removed: [], rows_out: 9838, parts: { train: 7870, val: 984, test: 984 } },
  ],
  columns: [
    {
      key: "width", label: "Width", unit: "px", raw_label: "raw", raw: five(150, 427, 640, 640, 640, 10_000), here: five(263, 428, 640, 640, 640, 9838),
      removed: five(150, 200, 226, 600, 640, 162),
      bins: [{ low: 100, high: 200, raw: 6, here: 0 }, { low: 200, high: 300, raw: 24, here: 17 }, { low: 600, high: 700, raw: 6956, here: 6852 }],
    },
    { key: "clip_score", label: "CLIP score", unit: "", raw_label: "with a value", raw: five(0.115, 0.258, 0.313, 0.364, 0.459, 9932), here: five(0.2, 0.261, 0.314, 0.364, 0.459, 9838), removed: null, bins: [{ low: 0.1, high: 0.15, raw: 7, here: 0 }, { low: 0.15, high: 0.2, raw: 87, here: 0 }, { low: 0.2, high: 0.25, raw: 626, here: 626 }] },
  ],
  classes: null, formats: { JPEG: 10_000 }, markers: { clip_score: 0.2 }, token_cut: null,
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/versions/:versionId", element: <VersionScreen /> }], { initialEntries: [path] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

function routes(v: Version, extra: Record<string, unknown>) {
  return {
    [`/api/v1/versions/${v.version_id}`]: v,
    [`/api/v1/versions/${v.version_id}/lineage`]: { version_id: v.version_id, ancestors: v === SPLIT ? [LABEL] : [], descendants: [], in_progress: [] },
    [`/api/v1/versions/${v.version_id}/stats`]: { ...STATS, version_id: v.version_id },
    [`/api/v1/versions/${v.version_id}/rows`]: { items: [], matched: 0, next_cursor: null },
    "/api/v1/jobs": emptyList,
    ...extra,
  };
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-03 v2", () => {
  test("test_UI_2_2_the_funnel_and_a_column_raw_against_here", async () => {
    stubApi(routes(SPLIT, { [`/api/v1/versions/${SPLIT.version_id}/distributions`]: DIST, [`/api/v1/versions/${SPLIT.version_id}/models`]: { items: [] } }));
    const router = renderAt(`/versions/${SPLIT.version_id}?tab=distributions`);

    const funnel = await screen.findByRole("table", { name: "Rows by stage from raw to this version" });
    const split = within(within(funnel).getByText("ds_3f6fcc420a27").closest("tr") as HTMLElement);
    expect(split.getByText("train 7,870 · val 984 · test 984")).toBeInTheDocument();
    expect(within(funnel).getByText("none · captions and CLIP scores added")).toBeInTheDocument();
    expect(screen.getByText(/162 of 10,000 rows \(1.6 %\) were removed/)).toBeInTheDocument();

    const hist = screen.getByRole("img", { name: /Width histogram, raw against here/ });
    expect(hist).toHaveTextContent("6,956 · 6,852");
    const widths = screen.getByRole("table", { name: "Width, five numbers" });
    expect(within(widths).getByText("here · 9,838").closest("tr")).toHaveTextContent("263");
    expect(screen.getByText(/The filter's threshold/)).toHaveTextContent("cuts the left tail: 94 rows");
    expect(screen.getByText("All 10,000 are JPEG, so format has no chart.")).toBeInTheDocument();

    fireEvent.click(within(funnel).getByRole("button", { name: /below_min_size, 60 rows/ }));
    expect(router.state.location.search).toContain("reason=below_min_size");
    expect(router.state.location.search).not.toContain("tab=");
  });

  test("test_DATA_3_5_the_diff_shows_the_config_change_and_the_changed_row", async () => {
    const pre = version("ds_bad290a73446", "preprocess", "ds_4af45ec55276", { row_count: 10_000 });
    stubApi(routes(pre, {
      "/api/v1/datasets/coco10k/versions": { items: [pre, version("ds_f81d0d552a1a", "preprocess", "ds_3c8ee080a4c9")], next_cursor: null },
      [`/api/v1/versions/${pre.version_id}/diff`]: {
        a: { version_id: "ds_f81d0d552a1a", stage: "preprocess", created_at: null }, b: { version_id: pre.version_id, stage: "preprocess", created_at: null },
        stages_differ: false, config_diff: [{ stage: "ingest", field: "min_size", a: 256, b: 255 }],
        added: 0, removed: 0, changed: 1, wording_only: 59, same: 9940, by_field: { status: 1, reason_code: 1 }, only_in_a: [], only_in_b: [],
        changed_rows: [{ row_key: "000000471773.jpg", fields: [{ field: "status", a: "rejected", b: "accepted" }, { field: "reason_code", a: "below_min_size", b: null }] }],
        wording_rows: [{ row_key: "000000000139.jpg", a: "below the minimum 256px", b: "below the minimum 255px" }], shown_limit: 200,
      },
    }));
    renderAt(`/versions/${pre.version_id}?tab=compare&with=ds_f81d0d552a1a`);

    const lead = await screen.findByText(/Their lineages differ at ingest:/);
    expect(lead.closest(".callout")).toHaveTextContent("min_size 256 → 255");
    const rows = screen.getByRole("table", { name: "Changed rows, A and B" });
    expect(within(rows).getByText("000000471773.jpg")).toBeInTheDocument();
    expect(within(rows).getByText("rejected")).toBeInTheDocument();
    expect(within(rows).getByText(/59 rows: reason wording only/)).toBeInTheDocument();
    fireEvent.click(within(rows).getByRole("button", { name: "Show" }));
    expect(within(rows).getByText("000000000139.jpg")).toBeInTheDocument();
    expect(screen.getByText("the same").nextElementSibling).toHaveTextContent("9,940");
  });

  test("test_UI_2_1_models_trained_on_a_version_with_the_split_named", async () => {
    stubApi(routes(SPLIT, {
      [`/api/v1/versions/${SPLIT.version_id}/models`]: {
        items: [
          { id: "mv_7f950100ab48", lineage_id: "ml_7d806d9897fa", lineage_name: "small_cnn · cifar10", state: "trained", kind: "fine-tune", parent_version_id: "mv_a014046ec3b5", metric: { split: "test", name: "accuracy", value: 0.831 }, weights_status: "present", trained_at: null, study_id: null },
          { id: "mv_2a7c0b4c8b72", lineage_id: "ml_7d806d9897fa", lineage_name: "small_cnn · cifar10", state: "trained", kind: "run", parent_version_id: null, metric: { split: "val", name: "accuracy", value: 0.839 }, weights_status: "weights_deleted", trained_at: null, study_id: null },
        ],
      },
    }));
    renderAt(`/versions/${SPLIT.version_id}?tab=models`);
    const table = await screen.findByRole("table", { name: "Model versions trained on this version" });
    expect(within(table).getByText("test 0.831")).toBeInTheDocument();
    expect(within(table).getByText("val 0.839")).toBeInTheDocument();
    expect(within(table).getByText("deleted")).toHaveClass("gone");
    expect(screen.getByRole("button", { name: /2 models trained on this version/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Compare on this test split/ })).toHaveAttribute("href", `/models/compare?split=${SPLIT.version_id}`);
  });

  test("test_DATA_2_3_the_semantic_tile_filters_rows_and_the_drawer_shows_the_pair", async () => {
    const member = (key: string, sha: string) => ({
      row_key: key, sha256: sha, status: "accepted", derived_sha256: `d${sha.slice(1)}`, split: "train", split_group: "g1", semantic_cluster_id: "1e4b".padEnd(64, "0"),
    });
    const a = member("000000185291.jpg", "1e4b".padEnd(64, "0"));
    const b = member("000000452782.jpg", "9a00".padEnd(64, "0"));
    const calls = stubApi(routes(LABEL, {
      [`/api/v1/versions/${LABEL.version_id}/rows`]: { items: [a, b], matched: 2, next_cursor: null },
      [`/api/v1/versions/${LABEL.version_id}/clusters/*`]: { cluster_id: a.semantic_cluster_id, rows: [a, b], pairs: [{ a: a.row_key, b: b.row_key, similarity: 0.985, phash_distance: 30 }], split_groups: ["g1"] },
      "/api/v1/blobs/*": new Response("", { status: 200 }),
    }));
    const router = renderAt(`/versions/${LABEL.version_id}`);

    const tile = await screen.findByRole("button", { name: /In a semantic cluster, 290 rows in 105 clusters/ });
    expect(tile).toHaveTextContent("largest 22");
    fireEvent.click(tile);
    await vi.waitFor(() => {
      expect(calls.some((c) => c.url.pathname.endsWith("/rows") && c.url.searchParams.get("semantic") === "true")).toBe(true);
    });
    // The rows table is virtualised (no layout in jsdom): the drawer opens by its deep link.
    router.navigate(`/versions/${LABEL.version_id}?semantic=1&row=000000185291.jpg`).catch(() => undefined);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("sc 1e4b")).toBeInTheDocument();
    expect(await within(drawer).findByText("0.985")).toBeInTheDocument();
    expect(within(drawer).getByText("one split group")).toBeInTheDocument();
    expect(within(drawer).getByText(/detected, not removed/)).toBeInTheDocument();
  });
});
