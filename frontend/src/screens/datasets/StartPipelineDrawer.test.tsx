// The Start pipeline drawer, type first (M11b; approved hi-fi SCR-02drawer_v1; DATA-5.2,
// DATA-1.4, DATA-7.1): the type before the stages, only the type's stages, one stop.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { emptyList, stubApi } from "../../test/api";
import { StartPipelineDrawer, type StartFrom } from "./StartPipelineDrawer";

const stage = (name: string, optional = false) => ({ name, optional });
const TYPES = [
  { name: "image_caption", title: "Image–caption pairs", input_kind: null, families: [], default_metrics: [], trainable: false, note: "", source: "image files; captioned and scored by the label stage", stages: ["ingest", "clean", "preprocess", "label", "filter", "split", "evaluate"].map((n) => stage(n)) },
  { name: "text_corpus", title: "Text corpus", input_kind: null, families: [], default_metrics: [], trainable: false, note: "", source: ".md and .txt files, at any depth", stages: ["ingest", "clean_text", "chunk", "index"].map((n) => stage(n)) },
  { name: "tabular", title: "Tabular", input_kind: "vector", families: ["mlp"], default_metrics: [], trainable: true, note: "", source: "one .csv or .parquet table", stages: [stage("ingest"), stage("transform", true), stage("clean_table"), stage("split")] },
];
const SOURCES = {
  items: [
    { name: "wine", path: "/sources/wine", files: 1, columns: ["alcohol", "ash", "magnesium", "cultivar"] },
    { name: "coco10k", path: "/sources/coco10k", files: 10_000, columns: null },
    { name: "stratal-docs", path: "/sources/stratal-docs", files: 43, columns: null },
  ],
  next_cursor: null,
};
const RAW = {
  version_id: "ds_b3f4ff09de8a", dataset: "wine", stage: "ingest", parent_version_id: null, content_hash: "a".repeat(64), config_hash: "b".repeat(64),
  config: {}, dtype: "tabular", row_count: 178, counts: {}, manifest_path: "m", manifest_bytes: 1, new_blob_bytes: 0, schema_version: 1, code_version: null,
  created_by_job_id: "job_0061", created_by_job_state: "completed", created_at: "2026-11-19T14:50:53Z",
};

function setup() {
  return stubApi({
    "/api/v1/sources": SOURCES,
    "/api/v1/dataset-types": TYPES,
    "/api/v1/datasets": { items: [{ slug: "wine" }], next_cursor: null },
    "/api/v1/datasets/wine/versions": { items: [RAW], next_cursor: null },
    // A type's own defaults when the drawer names it (a corpus's max_chars, M12).
    "/api/v1/config/defaults": (url: URL) =>
      url.searchParams.get("dataset_type") === "text_corpus"
        ? { ingest: { min_size: 256 }, clean_text: { max_chars: 1_000_000 }, chunk: { chunk_tokens: 256, overlap_tokens: 32, min_tokens: 32 }, index: { embedding_model: "bge-small-en-v1.5", embedding_revision: "5c38ec7c405e" } }
        : { ingest: { min_size: 256 }, split: { seed: 0 } },
    "/api/v1/config/schemas/*": { type: "object", properties: {} },
    "/api/v1/jobs": { id: "job_0100", ...emptyList },
  });
}

function renderDrawer(from: StartFrom) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/", element: <StartPipelineDrawer open from={from} onOpenChange={() => undefined} /> },
      { path: "/jobs/:jobId", element: <p>SCR-04</p> },
      { path: "/versions/:versionId/transform", element: <p>SCR-15</p> },
    ],
    { initialEntries: ["/"] },
  );
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

async function pickFolder(path: string) {
  await screen.findByRole("option", { name: new RegExp(path) });
  fireEvent.change(screen.getByLabelText(/Source folder/), { target: { value: path } });
}

async function pickWineAsTable() {
  await pickFolder("/sources/wine");
  fireEvent.click(await screen.findByRole("radio", { name: /Tabular/ }));
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("Start pipeline drawer", () => {
  test("test_DATA_5_2_table_source_sends_tabular_and_only_table_stages", async () => {
    const calls = setup();
    const router = renderDrawer({ mode: "folder" });
    expect(await screen.findByText(/The dataset name and the stages follow from it/)).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: /Stages/ })).not.toBeInTheDocument();
    await pickWineAsTable();

    const stages = await screen.findByRole("radiogroup", { name: /Stages/ });
    const names = within(stages).getAllByText(/^(ingest|transform|clean_table|split|label|filter)$/).map((n) => n.textContent);
    expect(names).toEqual(["ingest", "transform", "clean_table", "split"]);
    fireEvent.change(screen.getByLabelText(/table.target/), { target: { value: "cultivar" } });
    fireEvent.click(screen.getByRole("button", { name: "Start job · to split" }));

    await waitFor(() => { expect(router.state.location.pathname).toBe("/jobs/job_0100"); });
    const job = calls.find((c) => c.url.pathname === "/api/v1/jobs" && c.method === "POST");
    expect(job?.body).toEqual({
      kind: "pipeline", dataset: "wine", source: { kind: "folder", path: "/sources/wine" }, dataset_type: "tabular",
      stages: { ingest: { table: { target: "cultivar" } } }, until_stage: null,
    });
  });

  test("test_DATA_5_2_moving_the_stop_changes_no_other_stage", async () => {
    setup();
    renderDrawer({ mode: "folder" });
    await pickWineAsTable();
    fireEvent.change(await screen.findByLabelText(/table.target/), { target: { value: "cultivar" } });
    fireEvent.click(screen.getByRole("radio", { name: "Stop after clean_table" }));

    expect(screen.getByRole("radio", { name: "Stop after clean_table" })).toBeChecked();
    const split = screen.getByText("split").closest(".sp-st") as HTMLElement;
    expect(split).toHaveClass("notrun");
    expect(within(split).getByRole("radio", { name: "Stop after split" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Start job · to clean_table" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Stop after split" }));
    expect(split).not.toHaveClass("notrun");
  });

  test("test_DATA_5_3_corpus_types_offer_their_stages", async () => {
    const calls = setup();
    renderDrawer({ mode: "folder" });
    await pickFolder("/sources/stratal-docs");
    fireEvent.click(await screen.findByRole("radio", { name: /Text corpus/ }));

    const stages = await screen.findByRole("radiogroup", { name: /Stages/ });
    const names = within(stages).getAllByText(/^(ingest|clean_text|chunk|index|split)$/).map((n) => n.textContent);
    expect(names).toEqual(["ingest", "clean_text", "chunk", "index"]);
    const row = (name: string) => within(stages).getByText(name).closest(".sp-st") as HTMLElement;
    expect(row("ingest")).toHaveTextContent("one row per document");
    expect(within(row("ingest")).getByRole("button", { name: /ingest settings/ })).toBeDisabled(); // nothing to set
    expect(await within(stages).findByText("1000000")).toBeInTheDocument(); // the corpus's max_chars, not 10,000
    expect(row("chunk")).toHaveTextContent("at headings, then by size · chunk_tokens 256");
    // One embedding model (M12 decision 4): shown fixed, not an input.
    expect(row("index")).toHaveTextContent("embed, build the index · embedding bge-small-en-v1.5 · 5c38ec7 fixed");
    expect(within(row("index")).getByRole("button", { name: /index settings/ })).toBeDisabled();
    expect(within(row("index")).getByRole("radio", { name: "Stop after index" })).toBeChecked();
    expect(calls.some((c) => c.url.searchParams.get("dataset_type") === "text_corpus")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Start job · to index" }));
    await waitFor(() => { expect(calls.some((c) => c.url.pathname === "/api/v1/jobs" && c.method === "POST")).toBe(true); });
    const job = calls.find((c) => c.url.pathname === "/api/v1/jobs" && c.method === "POST");
    expect(job?.body).toEqual({
      kind: "pipeline", dataset: "stratal-docs", source: { kind: "folder", path: "/sources/stratal-docs" }, dataset_type: "text_corpus",
      stages: {}, until_stage: null,
    });
  });

  test("test_DATA_5_2_image_source_runs_the_image_pipeline", async () => {
    const calls = setup();
    renderDrawer({ mode: "folder" });
    await pickFolder("/sources/coco10k");
    fireEvent.click(await screen.findByRole("radio", { name: /Image–caption pairs/ }));
    fireEvent.click(await screen.findByRole("radio", { name: "Stop after preprocess" }));
    fireEvent.click(screen.getByRole("button", { name: "Start job · to preprocess" }));

    await waitFor(() => { expect(calls.some((c) => c.url.pathname === "/api/v1/jobs")).toBe(true); });
    const job = calls.find((c) => c.url.pathname === "/api/v1/jobs" && c.method === "POST");
    expect(job?.body).toMatchObject({ dataset_type: "image_caption", until_stage: "preprocess", dataset: "coco10k" });
  });

  test("test_DATA_7_1_transform_toggle_opens_the_editor", async () => {
    setup();
    const router = renderDrawer({ mode: "version", dataset: "wine", versionId: RAW.version_id });
    const toggle = await screen.findByRole("checkbox", { name: /transform: open the transform editor/ });
    fireEvent.click(toggle);
    expect(router.state.location.pathname).toBe(`/versions/${RAW.version_id}/transform`);
  });

  test("test_DATA_7_1_from_a_folder_transform_offers_stop_after_ingest", async () => {
    setup();
    renderDrawer({ mode: "folder" });
    await pickWineAsTable();
    fireEvent.click(await screen.findByRole("button", { name: "Stop after ingest" }));
    expect(screen.getByRole("radio", { name: "Stop after ingest" })).toBeChecked();
  });
});
