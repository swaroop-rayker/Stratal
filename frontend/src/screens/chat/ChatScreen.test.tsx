// SCR-12 chat (LLM-1.1, LLM-2.1, LLM-2.3, LLM-2.4, NFR-6): citations link to their sources,
// uncited sources fold, an abstention names the nearest source and the threshold, image
// results open their rows, Stop keeps the partial answer, a GPU job is named.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { MessageOut, RequestTimings, SessionOut, Source } from "../../api/chat";
import type { Job } from "../../api/client";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { ChatScreen } from "./ChatScreen";

const INDEX = "ds_710b60b40962";
const IMAGES = "ds_ade1d41f91f5";
const SESSION = "cs_4854c69731c4";
const NOW = new Date().toISOString();

const sources = {
  indexes: [{ version_id: INDEX, dataset: "stratal-docs", chunks: 2555, chunk_tokens: 128, created_at: NOW }],
  images: [{ version_id: IMAGES, dataset: "coco10k", stage: "split", images: 9830, created_at: NOW }],
};

function source(n: number, cited: boolean, relevance = 0.8 - n * 0.02): Source {
  return {
    n, row_key: `decisions/ADR-0016-inference-service.md#${n}`, document: "decisions/ADR-0016-inference-service.md",
    section: `ADR-0016 › Decision › Rule ${n}`, char_start: 0, char_end: 120, relevance, snippet: `Rule ${n} text`, cited,
  };
}

function timings(extra: Partial<RequestTimings> = {}): RequestTimings {
  return {
    route: "rag", model: "qwen3-1.7b-q4km", revision: "d7f544eead69a1b2", device: "gpu", index_version_id: INDEX,
    cold_start: true, load_ms: 6700, ttft_ms: 7100, total_ms: 7800, prompt_tokens: 900, output_tokens: 80,
    tokens_per_s: 103, status: "ok", error_code: null, ...extra,
  };
}

let nextId = 1;
function message(role: string, mode: string, content: string, details: Record<string, unknown> | null = null, request: RequestTimings | null = null): MessageOut {
  return { id: nextId++, role, mode, content, citations: null, details, request, created_at: NOW };
}

function session(messages: MessageOut[], id = SESSION): SessionOut {
  return { id, title: messages[0]?.content ?? "", created_at: NOW, updated_at: NOW, messages };
}

function ragTurn(): MessageOut[] {
  return [
    message("user", "rag", "How does inference share the GPU?"),
    message("assistant", "rag", "It takes the GPU lease before loading [1].", {
      sources: [source(1, true), source(2, false), source(3, false), source(4, false)],
    }, timings()),
  ];
}

function gpuJob(): Job {
  return {
    id: "job_0131", kind: "train", state: "running", desired_state: null, attempt: 1, dataset: "coco10k", current_stage: "train",
    output_version_id: null, error: null, summary: null, created_at: NOW, started_at: NOW, finished_at: null,
    stages: [{
      idx: 0, name: "train", queue: "gpu", status: "running", config: {}, input_version_id: IMAGES, output_version_id: null,
      cache_hit: false, step: 3, total: 10, throughput: null, eta_seconds: null, activity: "Epoch 3", metrics: null, peaks: null,
      started_at: NOW, finished_at: null, error: null,
    }],
  };
}

interface Chat {
  /** SSE events the next POST /chat answers with, in order. */
  events: [string, unknown][];
  /** Keep the stream open after the events, until the request is aborted. */
  hold: boolean;
  posted: unknown[];
}

function routes(stored: SessionOut | null, extra: Record<string, unknown> = {}): Chat {
  const chat: Chat = { events: [], hold: false, posted: [] };
  stubApi({
    "/api/v1/chat/sources": sources,
    "/api/v1/chat/sessions": { items: stored ? [{ id: stored.id, title: stored.title, mode: "rag", created_at: NOW, updated_at: NOW }] : [], next_cursor: null },
    ...(stored ? { [`/api/v1/chat/sessions/${stored.id}`]: () => stored } : {}),
    "/api/v1/jobs": { items: [], next_cursor: null, counts: {} },
    ...extra,
  });
  // openapi-fetch passes a Request; streamChat passes a URL and init with its AbortSignal.
  const inner = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: Request | string, init?: RequestInit) => {
      if (typeof input !== "string") return inner(input);
      chat.posted.push(JSON.parse(init?.body as string));
      const signal = init?.signal;
      const encoder = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const [name, data] of chat.events) {
            controller.enqueue(encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`));
          }
          if (!chat.hold) controller.close();
          else signal?.addEventListener("abort", () => { controller.error(new DOMException("aborted", "AbortError")); });
        },
      });
      return Promise.resolve(new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
    }),
  );
  return chat;
}

function renderChat(path = `/chat/${SESSION}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/chat/:sessionId?" element={<ChatScreen />} />
          <Route path="/versions/:versionId" element={<p>version screen</p>} />
          <Route path="/jobs/:jobId" element={<p>job screen</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  FakeSource.install();
  nextId = 1;
  Element.prototype.scrollIntoView = vi.fn(); // jsdom has no layout
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-12", () => {
  test("test_LLM_2_1_citations_link_to_their_sources", async () => {
    routes(session(ragTurn()));
    renderChat();
    const cite = await screen.findByRole("link", { name: "Source 1" });
    const sourcesBox = screen.getByText("Sources").closest("div") as HTMLElement;
    expect(sourcesBox).toHaveTextContent("1 cited of 4 given");
    const row = within(sourcesBox).getByRole("link", { name: /Rule 1/ });
    // The chunk opens "cited as [1]" with the way back here (SCR-03 for corpora, region 5).
    expect(row).toHaveAttribute("href", `/versions/${INDEX}?row=${encodeURIComponent("decisions/ADR-0016-inference-service.md#1")}&cited=1&chat=${SESSION}`);
    fireEvent.click(cite);
    await waitFor(() => { expect(document.activeElement).toBe(row); });
    expect(row).toHaveClass("is-hover");
  });

  test("test_LLM_2_1_uncited_sources_folded", async () => {
    routes(session(ragTurn()));
    renderChat();
    const more = await screen.findByRole("button", { name: /3 more sources given, not cited/ });
    expect(more).toHaveTextContent("2–4");
    expect(screen.queryByRole("link", { name: /Rule 3/ })).not.toBeInTheDocument();
    fireEvent.click(more);
    expect(screen.getByRole("link", { name: /Rule 3/ })).toHaveClass("unc");
  });

  test("test_LLM_2_3_abstention_shows_nearest_source_and_threshold", async () => {
    const nearest = source(1, false, 0.61);
    routes(session([
      message("user", "rag", "What is the capital of Peru?"),
      message("assistant", "rag", "I don't know.", { abstained: true, nearest, threshold: 0.65, sources: [] },
        timings({ model: null, revision: null, device: null, cold_start: false, load_ms: null, ttft_ms: null, total_ms: 40, tokens_per_s: null })),
    ]));
    renderChat();
    expect(await screen.findByText("I don't know.")).toBeInTheDocument();
    const text = screen.getByText(/don't seem to cover this/);
    expect(text).toHaveTextContent("relevance 0.61, below the threshold 0.65.");
    expect(text).toHaveTextContent("The model was not asked.");
    expect(within(text).getByRole("link")).toHaveAttribute("href", expect.stringContaining(`/versions/${INDEX}?row=`) as string);
    expect(screen.getByText("RAG · abstained")).toBeInTheDocument();
  });

  test("test_LLM_2_4_image_results_open_their_rows", async () => {
    const images = [
      { row_key: "000000021374.jpg", sha256: "a".repeat(64), derived_sha256: "b".repeat(64), similarity: 0.36 },
      { row_key: "000000450607.jpg", sha256: "c".repeat(64), derived_sha256: "d".repeat(64), similarity: 0.33 },
    ];
    routes(session([
      message("user", "images", "a red bus on a city street"),
      message("assistant", "images", "", { images, searched: 9830, version_id: IMAGES },
        timings({ route: "images", model: "clip-vit-b-32-laion2b", device: "cpu", index_version_id: IMAGES, cold_start: false, load_ms: null, ttft_ms: null, total_ms: 22, tokens_per_s: null })),
    ]));
    renderChat();
    const first = await screen.findByRole("img", { name: "000000021374.jpg, similarity 0.36" });
    expect(first).toHaveAttribute("src", `/api/v1/blobs/${"b".repeat(64)}?w=224`);
    expect(first.closest("a")).toHaveAttribute("href", `/versions/${IMAGES}?row=000000021374.jpg`);
    expect(screen.getByText(/2 images in/)).toHaveTextContent(`2 images in ${IMAGES} nearest to “a red bus on a city street”`);
    expect(screen.getByText("images searched").parentElement).toHaveTextContent("9,830 images searched");
    fireEvent.click(first);
    expect(await screen.findByText("version screen")).toBeInTheDocument();
  });

  test("test_LLM_1_1_stop_keeps_the_partial_answer", async () => {
    const chat = routes(null);
    chat.hold = true;
    chat.events = [
      ["session", { session_id: SESSION, message_id: 1 }],
      ["meta", { route: "llm", model: "qwen3-1.7b-q4km", revision: "d7f544eead69", device: "gpu", cold_start: false }],
      ["token", { text: "B-trees were invented" }],
      ["token", { text: " in 1970" }],
    ];
    renderChat("/chat");
    const box = await screen.findByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "Write about B-trees." } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByText(/B-trees were invented in 1970/)).toBeInTheDocument();
    expect(chat.posted[0]).toMatchObject({ mode: "llm", message: "Write about B-trees." });

    // What the API stores once it notices the closed stream.
    const stored = session([
      message("user", "llm", "Write about B-trees."),
      message("assistant", "llm", "B-trees were invented in 1970", { stopped: true },
        timings({ route: "llm", index_version_id: null, cold_start: false, load_ms: null, ttft_ms: 21, total_ms: 1400, tokens_per_s: null, status: "stopped" })),
    ]);
    stubApi({
      "/api/v1/chat/sources": sources,
      "/api/v1/chat/sessions": { items: [], next_cursor: null },
      [`/api/v1/chat/sessions/${SESSION}`]: stored,
      "/api/v1/jobs": { items: [], next_cursor: null, counts: {} },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      await Promise.resolve();
    });
    // The live copy shows "Stopped" at once; the stored copy, with the API's timing, replaces it.
    await waitFor(() => { expect(screen.getByText("stopped at")).toHaveTextContent("stopped at 1.4 s"); });
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.getAllByText(/B-trees were invented in 1970/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  test("test_LLM_1_1_gpu_busy_names_the_job", async () => {
    const chat = routes(null, { "/api/v1/jobs": { items: [gpuJob()], next_cursor: null, counts: {} } });
    chat.events = [
      ["session", { session_id: SESSION, message_id: 1 }],
      ["error", { code: "gpu_busy", detail: "The GPU is in use by job_0131 (train).", suggested_action: "Wait for it to finish, or pause it." }],
    ];
    renderChat("/chat");
    const banner = await screen.findByText(/LLM and RAG answers/);
    expect(banner).toHaveTextContent("The GPU is in use by job_0131 (train).");
    expect(within(banner).getByRole("link", { name: "job_0131" })).toHaveAttribute("href", "/jobs/job_0131");

    const box = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("gpu_busy");
    expect(alert).toHaveTextContent("Wait for it to finish, or pause it.");
    expect(within(alert).getByRole("link", { name: "Open job_0131" })).toHaveAttribute("href", "/jobs/job_0131");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  test("test_NFR_6_footer_shows_the_timings", async () => {
    routes(session(ragTurn()));
    renderChat();
    await screen.findByRole("link", { name: "Source 1" });
    const footer = screen.getByText("cold start 6.7 s").parentElement as HTMLElement;
    expect(footer).toHaveTextContent("RAG");
    expect(footer).toHaveTextContent("qwen3-1.7b · d7f544e");
    expect(footer).toHaveTextContent("GPU");
    expect(footer).toHaveTextContent("first token 7.1 s");
    expect(footer).toHaveTextContent("total 7.8 s");
    expect(footer).toHaveTextContent("103 tok/s");
    expect(footer).toHaveTextContent(`index ${INDEX}`);
  });
});
