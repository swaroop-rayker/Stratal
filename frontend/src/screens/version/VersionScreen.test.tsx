// DATA-3.3, DATA-2.2, UI-2.1 (SCR-03): lineage, duplicates and the version's states.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { emptyList, stubApi } from "../../test/api";
import { type CorpusKind, CorpusCells, neighbour, sectionTail } from "./Corpus";
import { LineageStrip } from "./LineageStrip";
import { hexDiff, phashDistance, type Row } from "./rows";
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

function renderVersion(id: string, search = "") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/versions/${id}${search}`]}>
        <Routes>
          <Route path="/versions/:versionId" element={<VersionScreen />} />
          <Route path="/versions/:versionId/report" element={<p>report screen</p>} />
          <Route path="/chat" element={<p>chat screen</p>} />
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

// SCR-03 for corpora (mockup SCR-03corpora_v1; DATA-5.3, LLM-2.1, LLM-2.2).
const INGEST = "ds_4a91744afd05";
const CLEAN = "ds_b7e033270eb7";
const CHUNK = "ds_d2ee5466c407";
const INDEX = "ds_710b60b40962";
const corpus = { dataset: "stratal-docs", dtype: "text_corpus" };

const stats = (id: string, accepted: number) => ({
  version_id: id, row_count: accepted, by_status: { accepted, rejected: 0, duplicate: 0, filtered: 0 }, reasons: [], by_stage: {},
  by_split: null, clip_score: null,
  size_bytes: { count: 0, mean: null, std: null, minimum: null, maximum: null, quantiles: {}, edges: [1, 2], histogram: [0], below: 0, above: 0 },
});

const chunkRow = (n: number, start: number, end: number, text: string) => ({
  row_key: `DATA_MODEL.md#${String(n).padStart(4, "0")}`, status: "accepted", document: "DATA_MODEL.md",
  section: "Stratal — Data Model › 3. Identifiers", char_start: start, char_end: end, token_count: 241, text,
});
const CHUNKS = [
  chunkRow(11, 2000, 2351, "Version IDs are derived. They are stored with the version."),
  chunkRow(12, 2318, 3402, "They are stored with the version.\n| Entity | Format |"),
  chunkRow(13, 3402, 3600, "Jobs are numbered."),
];

function indexRoutes() {
  const ingest = version(INGEST, "ingest", null, { ...corpus, counts: { accepted: 36, rejected: 7, duplicate: 0, filtered: 0 } });
  const clean = version(CLEAN, "clean_text", INGEST, { ...corpus, counts: { accepted: 36, rejected: 0, duplicate: 0, filtered: 0 } });
  const chunk = version(CHUNK, "chunk", CLEAN, { ...corpus, config: { chunk_tokens: 256, overlap_tokens: 32, min_tokens: 32 } });
  const index = version(INDEX, "index", CHUNK, {
    ...corpus, row_count: 970, counts: { accepted: 970, rejected: 0, duplicate: 0, filtered: 0 }, created_by_job_id: "job_0131",
    config: { embedding_model: "bge-small-en-v1.5", embedding_revision: "5c38ec7c405e" },
  });
  return stubApi({
    [`/api/v1/versions/${INDEX}`]: index,
    [`/api/v1/versions/${INDEX}/lineage`]: { version_id: INDEX, ancestors: [chunk, clean, ingest], descendants: [], in_progress: [] },
    [`/api/v1/versions/${INDEX}/stats`]: stats(INDEX, 970),
    [`/api/v1/versions/${INDEX}/index`]: {
      version_id: INDEX, vectors: 970, dimension: 384, model: "bge-small-en-v1.5", revision: "5c38ec7c405e", query_prefix: "Represent…",
      metric: "cosine", sha256: "c".repeat(64), size_bytes: 1_572_864, documents: 36, characters: 752_552,
    },
    [`/api/v1/versions/${INDEX}/reports/latest`]: Response.json({ code: "not_found", detail: "none" }, { status: 404 }),
    [`/api/v1/versions/${INDEX}/reports`]: { id: "job_0140" },
    [`/api/v1/versions/${INDEX}/rows`]: (url: URL) => {
      const q = url.searchParams.get("q");
      const items = q ? CHUNKS.filter((r) => r.row_key.includes(q)) : CHUNKS;
      return { items, matched: q ? items.length : 970, next_cursor: null };
    },
    [`/api/v1/versions/${CLEAN}/rows`]: (url: URL) => {
      const doc = { row_key: "DATA_MODEL.md", status: "accepted", title: "Stratal — Data Model", text: "x".repeat(39_730) };
      return { items: url.searchParams.get("q") === "API.md" ? [] : [doc], matched: 1, next_cursor: null };
    },
    "/api/v1/jobs/job_0131": { id: "job_0131", stages: [{ name: "index", output_version_id: INDEX, metrics: { cached: 912 } }] },
    "/api/v1/jobs": emptyList,
  });
}

describe("SCR-03 for corpora", () => {
  test("test_DATA_5_3_corpus_versions_hide_columns_models_and_train", async () => {
    const pairs = version("ds_9a1b2c3d4e5f", "split", "ds_8a1b2c3d4e5f", {
      dataset: "stratal-pairs", dtype: "instruction_pairs", row_count: 3, counts: { accepted: 2, rejected: 1, duplicate: 0, filtered: 0 },
    });
    const pair = (n: number, instruction: string, output: string, extra = {}) => ({
      row_key: `pairs.jsonl#000000${String(n)}`, status: "accepted", record: JSON.stringify({ instruction, input: "", output }),
      chat_format: `<|im_start|>user\n${instruction}<|im_end|>\n<|im_start|>assistant\n${output}<|im_end|>\n`, ...extra,
    });
    stubApi({
      [`/api/v1/versions/${pairs.version_id}`]: pairs,
      [`/api/v1/versions/${pairs.version_id}/lineage`]: { version_id: pairs.version_id, ancestors: [], descendants: [], in_progress: [] },
      [`/api/v1/versions/${pairs.version_id}/stats`]: stats(pairs.version_id, 2),
      [`/api/v1/versions/${pairs.version_id}/rows`]: {
        items: [
          pair(1, "Summarise what ADR-0006 decides.", "Every GPU job runs in its own process."),
          pair(3, "Explain hit@5.", " ", { status: "rejected", reason_code: "empty_output", chat_format: null }),
        ],
        matched: 2, next_cursor: null,
      },
      "/api/v1/jobs": emptyList,
    });
    renderVersion(pairs.version_id, "?row=pairs.jsonl%230000001");
    expect(await screen.findByText(/instruction pairs · made/)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Rows, 2", hidden: true }); // behind the modal drawer
    expect(within(table).getAllByRole("columnheader", { hidden: true }).map((h) => h.textContent)).toEqual(["Row", "Instruction", "Output", "Status"]);
    for (const gone of [/Train a model/, /Start a review/]) expect(screen.queryByRole("button", { name: gone, hidden: true })).not.toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "Version detail", hidden: true });
    expect(within(tabs).getAllByRole("tab", { hidden: true }).map((t) => t.textContent)).toEqual(["Rows 3", "Reasons 0", "Distributions", "Compare", "Config"]);

    const drawer = await screen.findByRole("dialog", { name: /^Pair\s*pairs\.jsonl#0000001/ });
    const format = within(drawer).getByLabelText("Chat format");
    expect(format).toHaveTextContent("Every GPU job runs in its own process.");
    expect([...format.querySelectorAll(".tok")].map((t) => t.textContent)).toEqual([
      "<|im_start|>user\n", "<|im_end|>\n", "<|im_start|>assistant\n", "<|im_end|>\n",
    ]);
  });

  test("test_LLM_2_1_citation_opens_the_chunk_drawer", async () => {
    indexRoutes();
    renderVersion(INDEX, `?row=${encodeURIComponent("DATA_MODEL.md#0012")}&cited=1&chat=cs_4854c69731c4`);
    const drawer = await screen.findByRole("dialog");
    // Its name, from the heading: "Chunk DATA_MODEL.md#0012, cited as 1" (the a11y notes).
    expect(document.getElementById(drawer.getAttribute("aria-labelledby") ?? "")).toHaveTextContent("Chunk DATA_MODEL.md#0012, cited as 1");
    expect(within(drawer).getByText(/in a chat answer/)).toHaveTextContent("Cited as 1 in a chat answer · Back to the conversation");
    expect(within(drawer).getByRole("link", { name: "Back to the conversation" })).toHaveAttribute("href", "/chat/cs_4854c69731c4");
    expect(await within(drawer).findByText("2,318–3,402 of 39,730 characters")).toBeInTheDocument();
    const text = within(drawer).getByLabelText("Chunk text");
    expect(text.querySelector(".ovtag")).toHaveTextContent("overlap");
    expect(text.querySelector(".ov")).toHaveTextContent("They are stored with the version."); // 2,351 − 2,318 = 33 characters
    expect(within(drawer).getByText(/241 · 32 shared with the previous chunk/)).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: /Previous chunk/ })).toBeEnabled();
    expect(within(drawer).getByRole("button", { name: /Next chunk/ })).toBeEnabled();
    // The drawer takes the room of the Document column.
    const table = screen.getByRole("table", { name: "Rows, 970", hidden: true });
    expect(within(table).getAllByRole("columnheader", { hidden: true }).map((h) => h.textContent)).toEqual(["Chunk", "Section", "Tokens", "Text"]);
  });

  test("test_LLM_2_1_index_actions_open_chat_and_report", async () => {
    const calls = indexRoutes();
    renderVersion(INDEX);
    await waitFor(() => { expect(screen.getByText("Vectors").parentElement).toHaveTextContent("Vectors970384 dimensions · 912 from cache"); });
    expect(screen.getByText("Documents").parentElement).toHaveTextContent("Documents36752,552 characters");
    expect(screen.getByText("Chunks").parentElement).toHaveTextContent("Chunks970256 tokens, 32 overlap");
    expect(screen.getByText("Index on disk").parentElement).toHaveTextContent("1.5 MB");
    expect(screen.getByText("Embedding model").parentElement).toHaveTextContent("revision 5c38ec7 · query prefix set");
    const table = await screen.findByRole("table", { name: "Rows, 970" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Chunk", "Document", "Section", "Tokens", "Text"]);

    const build = await screen.findByRole("button", { name: /Build the retrieval report/ });
    fireEvent.click(build);
    expect(await screen.findByText("report screen")).toBeInTheDocument();
    const posted = calls.find((c) => c.method === "POST");
    expect(posted?.url.pathname).toBe(`/api/v1/versions/${INDEX}/reports`);
    expect(posted?.body).toEqual({ kind: "retrieval" });
  });

  test("test_DATA_5_3_corpus_cells_show_sections_reasons_and_pairs", () => {
    // The rows table is virtualised (no layout in jsdom), so its cells are checked here.
    const cells = (kind: CorpusKind, row: object, withDocument = true) =>
      render(<table><tbody><tr><CorpusCells kind={kind} row={row as Row} withDocument={withDocument} /></tr></tbody></table>).container.querySelector("tr");
    expect(cells("chunks", CHUNKS[1] ?? {})).toHaveTextContent("DATA_MODEL.md#0012DATA_MODEL.md3. Identifiers241They are stored with the version.");
    expect(cells("chunks", CHUNKS[1] ?? {}, false)).toHaveTextContent("DATA_MODEL.md#00123. Identifiers241");
    expect(cells("documents", { row_key: "spikes/S-06.json", status: "rejected", reason_code: "unsupported_format" })).toHaveTextContent("spikes/S-06.json––rejected unsupported_format");
    expect(cells("pairs", { row_key: "pairs.jsonl#0000002", status: "accepted", record: JSON.stringify({ instruction: "Turn this caption into JSON", output: "{}" }) })).toHaveTextContent("pairs.jsonl#0000002Turn this caption into JSON{}accepted");
    expect(neighbour("DATA_MODEL.md#0012", -1)).toBe("DATA_MODEL.md#0011");
    expect(neighbour("DATA_MODEL.md#0000", -1)).toBeNull();
    expect(sectionTail("Stratal — Data Model › 4. Schema › 4.1 datasets")).toBe("4. Schema › 4.1 datasets");
  });

  test("test_LLM_2_1_ask_this_index_opens_chat_in_rag", async () => {
    indexRoutes();
    renderVersion(INDEX);
    fireEvent.click(await screen.findByRole("button", { name: /Ask this index/ }));
    expect(await screen.findByText("chat screen")).toBeInTheDocument();
  });
});
