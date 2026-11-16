// UI-2.1, DATA-3.2, DATA-3.4 (SCR-02): datasets and versions in each of their states.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { emptyList, stubApi } from "../../test/api";
import { DatasetsScreen } from "./DatasetsScreen";
import { pipelineBody } from "./StartPipelineDrawer";

type Version = Schemas["Version"];

function version(id: string, stage: string, parent: string | null, extra: Partial<Version> = {}): Version {
  return {
    version_id: id, dataset: "coco10k", stage, parent_version_id: parent,
    content_hash: "a".repeat(64), config_hash: "b".repeat(64), config: {}, dtype: null,
    row_count: 10000, counts: { accepted: 9512, rejected: 148, duplicate: 340, filtered: 0 },
    manifest_path: `manifests/${id}.parquet`, manifest_bytes: 1_600_000, new_blob_bytes: 1_181_116_006,
    schema_version: 1, code_version: null, created_by_job_id: "job_0142", created_by_job_state: "completed",
    created_at: "2026-11-14T14:01:00Z", ...extra,
  };
}

const latest = version("ds_c8e21d0f6a3b", "preprocess", "ds_e1aa7c52d940");
const raw = version("ds_0b1f9e77a3c4", "ingest", null, { new_blob_bytes: 4_617_089_843, created_by_job_state: "failed" });
const coco = {
  slug: "coco10k", display_name: null, modality: "image", source_uri: "/sources/coco10k",
  created_at: "2026-11-14T09:00:00Z", updated_at: "2026-11-14T14:01:00Z", version_count: 2,
  stored_bytes: 6_550_000_000, latest_version: latest,
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/datasets" element={<DatasetsScreen />} />
          <Route path="/datasets/:slug" element={<DatasetsScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-02", () => {
  test("test_UI_2_1_no_datasets_shows_the_empty_state_with_ingest", async () => {
    stubApi({ "/api/v1/datasets": emptyList, "/api/v1/jobs": emptyList });
    renderAt("/datasets");
    expect(await screen.findByText("No datasets yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ingest a folder/ })).toBeInTheDocument();
  });

  test("test_DATA_3_4_versions_show_counts_new_files_and_the_creating_job", async () => {
    stubApi({
      "/api/v1/datasets": { items: [coco], next_cursor: null },
      "/api/v1/datasets/coco10k": coco,
      "/api/v1/datasets/coco10k/versions": { items: [latest, raw], next_cursor: null },
      "/api/v1/jobs": emptyList,
    });
    renderAt("/datasets/coco10k");
    const table = await screen.findByRole("table", { name: "Versions of coco10k" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]?.textContent)).toEqual([
      "ds_c8e21d0f6a3b",
      "ds_0b1f9e77a3c4",
    ]);
    const first = rows[0] as HTMLElement;
    expect(within(first).getByLabelText("9,512 accepted, 148 rejected, 340 duplicate, 0 filtered")).toBeInTheDocument();
    expect(first).toHaveTextContent("+1.1 GB"); // only new files count (DATA-3.4)
    expect(first).toHaveTextContent("job_0142");
    expect(within(rows[1] as HTMLElement).getByText("raw")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("failed")).toBeInTheDocument(); // never colour alone
    expect(screen.getByText("6.1 GB")).toBeInTheDocument(); // stored, all versions
  });

  test("test_UI_2_1_a_running_job_on_the_dataset_is_a_pending_row", async () => {
    const job = {
      id: "job_0144", kind: "pipeline", state: "running", desired_state: null, attempt: 1, dataset: "coco10k",
      current_stage: "label", output_version_id: null, error: null, summary: null,
      created_at: "2026-11-14T14:02:00Z", started_at: "2026-11-14T14:02:01Z", finished_at: null,
      stages: [
        { idx: 2, name: "preprocess", queue: "cpu", status: "completed", config: {}, input_version_id: null,
          output_version_id: "ds_c8e21d0f6a3b", cache_hit: false, step: null, total: null, throughput: null,
          eta_seconds: null, activity: null, metrics: null, peaks: null, started_at: null, finished_at: null, error: null },
        { idx: 3, name: "label", queue: "gpu", status: "running", config: {}, input_version_id: null,
          output_version_id: null, cache_hit: false, step: 1300, total: 9512, throughput: 2.4, eta_seconds: 872,
          activity: null, metrics: null, peaks: null, started_at: null, finished_at: null, error: null },
      ],
    };
    stubApi({
      "/api/v1/datasets": { items: [coco], next_cursor: null },
      "/api/v1/datasets/coco10k": coco,
      "/api/v1/datasets/coco10k/versions": { items: [latest], next_cursor: null },
      "/api/v1/jobs": (url: URL) => (url.searchParams.get("state")?.includes("running") ? { ...emptyList, items: [job] } : emptyList),
    });
    renderAt("/datasets/coco10k");
    const pending = (await screen.findByText("Pending")).closest("tr") as HTMLElement;
    expect(pending).toHaveTextContent("label");
    expect(pending).toHaveTextContent("ds_c8e21d0f6a3b");
    expect(pending).toHaveTextContent("1,300 / 9,512");
    expect(pending).toHaveTextContent("job_0144");
  });

  test("test_UI_2_1_versions_that_cannot_load_show_the_problem_and_retry", async () => {
    stubApi({
      "/api/v1/datasets": { items: [coco], next_cursor: null },
      "/api/v1/datasets/coco10k": coco,
      "/api/v1/datasets/coco10k/versions": Response.json(
        { code: "dependency_unavailable", title: "Unavailable", detail: "The data volume is not reachable from the API.",
          suggested_action: "Check that the stratal-data volume is mounted (docker compose ps), then retry.",
          request_id: "req_0a282af102db", errors: [] },
        { status: 503 },
      ),
      "/api/v1/jobs": emptyList,
    });
    renderAt("/datasets/coco10k");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Versions of coco10k could not be loaded");
    expect(alert).toHaveTextContent("dependency_unavailable");
    expect(alert).toHaveTextContent("Do: Check that the stratal-data volume is mounted");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  test("test_UI_2_1_start_pipeline_sends_only_changed_settings", () => {
    const defaults = { ingest: { min_size: 256 }, clean: { phash_max_distance: 8 }, preprocess: { max_side: 768, jpeg_quality: 95 } };
    const all = ["ingest", "clean", "preprocess", "label"];
    const folder = pipelineBody({
      from: { dataset: "flowers5k", path: "/sources/flowers5k" },
      ticked: ["ingest", "clean", "preprocess"],
      runnable: all,
      values: { preprocess: { max_side: 512, jpeg_quality: 95 }, ingest: { min_size: 256 } },
      defaults,
    });
    expect(folder).toEqual({
      kind: "pipeline",
      dataset: "flowers5k",
      source: { kind: "folder", path: "/sources/flowers5k" },
      stages: { preprocess: { max_side: 512 } },
      until_stage: "preprocess",
    });
    const fromVersion = pipelineBody({
      from: { versionId: "ds_e1aa7c52d940" },
      ticked: ["preprocess", "label"],
      runnable: ["preprocess", "label"],
      values: {},
      defaults,
    });
    expect(fromVersion).toEqual({ kind: "pipeline", from_version: "ds_e1aa7c52d940", stages: {}, until_stage: null });
  });
});
