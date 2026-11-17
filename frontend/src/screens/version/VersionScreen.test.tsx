// DATA-3.3, DATA-2.2, UI-2.1 (SCR-03): lineage, duplicates and the version's states.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { emptyList, stubApi } from "../../test/api";
import { LineageStrip } from "./LineageStrip";
import { hexDiff, phashDistance } from "./rows";
import { VersionScreen } from "./VersionScreen";

type Version = Schemas["Version"];

function version(id: string, stage: string, parent: string | null, extra: Partial<Version> = {}): Version {
  return {
    version_id: id, dataset: "coco10k", stage, parent_version_id: parent, content_hash: "a".repeat(64),
    config_hash: "b".repeat(64), config: {}, dtype: null, row_count: 100,
    counts: { accepted: 90, rejected: 4, duplicate: 6, filtered: 0 }, manifest_path: `manifests/${id}.parquet`,
    manifest_bytes: 10_000, new_blob_bytes: 0, schema_version: 1, code_version: null,
    created_by_job_id: "job_0001", created_by_job_state: "completed", created_at: "2026-11-14T14:01:00Z", ...extra,
  };
}

function renderVersion(id: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/versions/${id}`]}>
        <Routes>
          <Route path="/versions/:versionId" element={<VersionScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-03", () => {
  test("test_DATA_3_3_lineage_strip_links_back_to_raw_and_forks_into_rows", () => {
    const raw = version("ds_raw000000001", "ingest", null);
    const current = version("ds_cln000000001", "clean", raw.version_id);
    const a = version("ds_pre00000000a", "preprocess", current.version_id, { config: { max_side: 768 } });
    const b = version("ds_pre00000000b", "preprocess", current.version_id, { config: { max_side: 512 } });
    render(
      <MemoryRouter>
        <LineageStrip
          current={current}
          lineage={{
            version_id: current.version_id,
            ancestors: [raw],
            descendants: [a, b],
            in_progress: [{ job_id: "job_0142", state: "running", stage: "label", step: 13, total: 100 }],
          }}
        />
      </MemoryRouter>,
    );
    const strip = screen.getByRole("navigation", { name: "Lineage" });
    const links = within(strip).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/versions/ds_raw000000001",
      "/versions/ds_pre00000000a",
      "/versions/ds_pre00000000b",
    ]);
    expect(within(strip).getByText("ds_cln000000001").closest("[aria-current]")).toHaveAttribute("aria-current", "true");
    expect(strip.querySelectorAll(".fork > .row")).toHaveLength(3); // two children and the running job
    expect(within(strip).getByLabelText("label, running, will add a child version")).toHaveTextContent("job_0142 · 13%");
    expect(within(strip).getByText("max 512")).toBeInTheDocument();
  });

  test("test_DATA_2_2_phash_distance_and_the_differing_digits", () => {
    expect(phashDistance("6c1e93a0f4b2d785", "6c1e93a0f4b2d7c6")).toBe(3);
    expect(phashDistance("ffff", "0000")).toBe(16);
    expect(phashDistance(null, "ab")).toBeNull();
    expect(hexDiff("6c1e85", "6c1ec6")).toEqual([
      { text: "6c1e", differs: false },
      { text: "85", differs: true },
    ]);
  });

  test("test_UI_2_1_every_row_rejected_shows_why_and_the_rejected_rows", async () => {
    const scans = version("ds_5a90c3e1f27d", "ingest", null, {
      dataset: "scans", row_count: 214, counts: { accepted: 0, rejected: 214, duplicate: 0, filtered: 0 },
      config: { min_size: 256 },
    });
    const calls = stubApi({
      "/api/v1/versions/ds_5a90c3e1f27d": scans,
      "/api/v1/versions/ds_5a90c3e1f27d/lineage": { version_id: scans.version_id, ancestors: [], descendants: [], in_progress: [] },
      "/api/v1/versions/ds_5a90c3e1f27d/stats": {
        version_id: scans.version_id, row_count: 214, by_status: { accepted: 0, rejected: 214, duplicate: 0, filtered: 0 },
        reasons: [{ reason_code: "below_min_size", decided_at_stage: "ingest", status: "rejected", rows: 214, share: 1 }],
        by_stage: { ingest: { below_min_size: 214 } }, by_split: null, clip_score: null,
        size_bytes: { count: 0, mean: null, std: null, minimum: null, maximum: null, quantiles: {}, edges: [1, 2], histogram: [0], below: 0, above: 0 },
      },
      "/api/v1/versions/ds_5a90c3e1f27d/rows": {
        items: [{ row_key: "page_001.png", status: "rejected", reason_code: "below_min_size", decided_at_stage: "ingest",
          reason_detail: "shorter side 180px is below the minimum 256px", width: 240, height: 180, size_bytes: 9000 }],
        matched: 214, next_cursor: null,
      },
      "/api/v1/jobs": emptyList,
    });
    renderVersion("ds_5a90c3e1f27d");
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("No row was accepted. All 214 rows were rejected as below_min_size.");
    expect(notice).toHaveTextContent("Do: ingest again with a smaller min_size");
    expect(screen.getByRole("button", { name: /Rejected, 214 rows, filtering the rows/ })).toHaveAttribute("aria-pressed", "true");
    await screen.findByRole("table", { name: "Rows, 214" });
    const rowsCall = calls.find((c) => c.url.pathname.endsWith("/rows"));
    expect(rowsCall?.url.searchParams.get("status")).toBe("rejected");
    expect(screen.getByRole("button", { name: /Continue the pipeline/ })).toHaveAttribute("aria-disabled", "true");
  });

  test("test_UI_2_1_an_unknown_version_says_so_and_links_to_datasets", async () => {
    stubApi({
      "/api/v1/versions/ds_000000000000": Response.json(
        { code: "not_found", title: "Not found", detail: "No dataset version 'ds_000000000000'.", suggested_action: null, request_id: "req_1", errors: [] },
        { status: 404 },
      ),
      "/api/v1/versions/ds_000000000000/*": Response.json({ code: "not_found", detail: "x" }, { status: 404 }),
    });
    renderVersion("ds_000000000000");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Version ds_000000000000 not found");
    expect(within(alert).getByRole("button", { name: "Go to datasets" })).toBeInTheDocument();
  });
});
