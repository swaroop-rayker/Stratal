// MON-4.1, MON-4.2, MON-4.3 (SCR-04): the live job view, its timeline and its activity log.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Job, JobStage, StreamEvent } from "../../api/client";
import { emptyList, stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { type LogFilters, matches } from "./ActivityLog";
import { JobScreen } from "./JobScreen";

const PHASE1 = ["ingest", "clean", "preprocess", "label", "filter", "split", "evaluate"];

function stage(idx: number, name: string, extra: Partial<JobStage> = {}): JobStage {
  return {
    idx, name, queue: name === "label" ? "gpu" : "cpu", status: "pending", config: {}, input_version_id: null,
    output_version_id: null, cache_hit: false, step: null, total: null, throughput: null, eta_seconds: null,
    activity: null, metrics: null, peaks: null, started_at: null, finished_at: null, error: null, ...extra,
  };
}

function running(): Job {
  const done = { status: "completed" as const, started_at: "2026-11-20T13:54:20Z", finished_at: "2026-11-20T13:58:00Z" };
  return {
    id: "job_0142", kind: "pipeline", state: "running", desired_state: null, attempt: 1, dataset: "coco10k",
    current_stage: "label", output_version_id: null, error: null, summary: null,
    created_at: "2026-11-20T13:54:19Z", started_at: "2026-11-20T13:54:20Z", finished_at: null,
    stages: PHASE1.map((name, i) =>
      i < 3
        ? stage(i, name, { ...done, output_version_id: `ds_00000000000${String(i)}` })
        : i === 3
          ? stage(i, name, { status: "running", started_at: "2026-11-20T14:01:00Z", step: 42, total: 313, throughput: 2.4, eta_seconds: 872, activity: "Captioning batch 42/313 · Florence-2-base · bs=16 · fp16" })
          : stage(i, name),
    ),
  };
}

function event(id: number, extra: Partial<StreamEvent>): StreamEvent {
  return {
    id, event_uid: `u${String(id)}`, ts: "2026-11-20T14:03:11Z", job_id: "job_0142", stage: "label", substage: null,
    version_id: null, type: "activity", level: "INFO", step: null, total: null, message: "m", metrics: null,
    reason: null, suggested_action: null, attempt: 1, ...extra,
  };
}

function renderJob(path = "/jobs/job_0142") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const telemetry = { resource: [], process: [] };
const system = { gpu_name: "RTX 4050", gpu_error: null, vram_total_mb: 6141, vram_cap_fraction: 0.85, vram_cap_mb: 5220, ram_total_mb: 10000, cpu_count: 16 };

beforeEach(() => {
  FakeSource.install();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-04", () => {
  test("test_MON_4_1_live_view_renders_and_updates_during_a_job", async () => {
    let current: Job = running(); // what the API answers; the stream moves it on
    stubApi({
      "/api/v1/jobs/job_0142": () => current,
      "/api/v1/jobs/job_0142/telemetry": telemetry,
      "/api/v1/jobs/job_0142/events": { items: [], next_cursor: null },
      "/api/v1/system": system,
      "/api/v1/jobs": emptyList,
    });
    renderJob();
    expect(await screen.findByText(/Captioning batch 42\/313/)).toBeInTheDocument();
    expect(screen.getByText("13%")).toBeInTheDocument();
    expect(screen.getByText("14m 32s")).toBeInTheDocument();

    const stream = FakeSource.last("/api/v1/jobs/job_0142/stream");
    expect(stream).toBeDefined();
    act(() => {
      stream?.emit("event", event(7, { type: "stage_progress", step: 84, total: 313, message: "Captioning batch 84/313 · Florence-2-base · bs=16 · fp16", metrics: { items_per_s: 2.5, eta_s: 900 } }), "7");
      stream?.emit("sample", { ts: "2026-11-20T14:03:12Z", cpu_percent: 55, ram_used_mb: 7372, ram_total_mb: 10240, gpu_util_percent: 94, vram_used_mb: 3994, vram_total_mb: 6141, gpu_temp_c: 71, gpu_power_w: 48, torch_allocated_mb: 3174, torch_reserved_mb: 3686 });
    });
    expect(screen.getAllByText(/Captioning batch 84\/313/).length).toBeGreaterThan(0); // activity line and log
    expect(screen.getByText("27%")).toBeInTheDocument();
    expect(screen.getByText("15m 00s")).toBeInTheDocument();
    expect(screen.getByText("55")).toBeInTheDocument(); // CPU tile
    expect(screen.getByText("torch alloc 3.1 · rsv 3.6")).toBeInTheDocument(); // MON-2.4

    current = { ...running(), state: "completed", current_stage: null, finished_at: "2026-11-20T15:08:26Z" };
    act(() => {
      stream?.emit("job", current);
    });
    expect(await screen.findByText("Completed")).toBeInTheDocument();
  });

  test("test_MON_4_2_timeline_shows_every_stage_and_opens_a_stage_with_its_events", async () => {
    stubApi({
      "/api/v1/jobs/job_0142": running(),
      "/api/v1/jobs/job_0142/telemetry": telemetry,
      "/api/v1/jobs/job_0142/events": (url: URL) =>
        url.searchParams.get("stage") === "clean"
          ? { items: [event(3, { stage: "clean", message: "Comparing hashes: 128 near-duplicate groups found" })], next_cursor: null }
          : { items: [], next_cursor: null },
      "/api/v1/system": system,
      "/api/v1/jobs": emptyList,
    });
    renderJob();
    const timeline = await screen.findByRole("list", { name: "Stages" });
    const segments = within(timeline).getAllByRole("listitem");
    expect(segments.map((s) => s.getAttribute("aria-label")?.split(",")[0])).toEqual(PHASE1); // MON-4.2.AC1
    expect(segments[0]).toHaveAttribute("aria-label", expect.stringContaining("completed") as string);
    expect(segments[3]).toHaveAttribute("aria-label", expect.stringContaining("running") as string);
    expect(segments[6]).toHaveAttribute("aria-label", expect.stringContaining("pending") as string);

    fireEvent.click(segments[1] as HTMLElement);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("heading", { name: "clean" })).toBeInTheDocument();
    expect(await within(drawer).findByText("Comparing hashes: 128 near-duplicate groups found")).toBeInTheDocument();
    expect(within(drawer).getByText("ds_000000000001")).toBeInTheDocument(); // the version it made
    expect(screen.getByText(/now show/)).toHaveTextContent("Charts and log on this page now show clean only.");
  });

  test("test_MON_4_3_log_filters_by_level_stage_and_job", () => {
    const base: LogFilters = { scope: "job", level: "INFO", stage: null, job: null, text: "" };
    const info = event(1, { level: "INFO", stage: "label" });
    const warn = event(2, { level: "WARN", stage: "clean", reason: "r", suggested_action: "a" });
    const debug = event(3, { level: "DEBUG" });
    const other = event(4, { job_id: "job_0145", level: "ERROR", reason: "r", suggested_action: "a" });
    const keep = (f: Partial<LogFilters>) => [info, warn, debug, other].filter((e) => matches(e, { ...base, ...f }, "job_0142")).map((e) => e.id);

    expect(keep({})).toEqual([1, 2]); // this job, INFO and above
    expect(keep({ level: "WARN" })).toEqual([2]);
    expect(keep({ level: "DEBUG" })).toEqual([1, 2, 3]);
    expect(keep({ stage: "clean" })).toEqual([2]);
    expect(keep({ scope: "all" })).toEqual([1, 2, 4]);
    expect(keep({ scope: "all", job: "job_0145" })).toEqual([4]);
    expect(keep({ text: "M" })).toEqual([1, 2]); // message text, any case
  });

  test("test_MON_4_3_changing_a_filter_asks_the_server_for_that_level_and_stage", async () => {
    const calls = stubApi({
      "/api/v1/jobs/job_0142": running(),
      "/api/v1/jobs/job_0142/telemetry": telemetry,
      "/api/v1/jobs/job_0142/events": { items: [], next_cursor: null },
      "/api/v1/events": { items: [event(9, { job_id: "job_0145", level: "ERROR", message: "CUDA out of memory", reason: "r", suggested_action: "a" })], next_cursor: null },
      "/api/v1/system": system,
      "/api/v1/jobs": emptyList,
    });
    renderJob();
    fireEvent.change(await screen.findByRole("combobox", { name: "Level" }), { target: { value: "WARN" } });
    fireEvent.click(screen.getByRole("button", { name: "All jobs" }));
    expect(await screen.findByText("CUDA out of memory")).toBeInTheDocument();
    const last = calls.filter((c) => c.url.pathname === "/api/v1/events").pop();
    expect(last?.url.searchParams.get("min_level")).toBe("WARN");
    expect(last?.url.searchParams.get("tail")).toBe("true");
  });

  test("test_UI_2_1_a_failed_job_says_why_what_to_do_and_what_was_kept", async () => {
    const failed: Job = {
      ...running(),
      state: "failed",
      current_stage: null,
      finished_at: "2026-11-20T14:31:05Z",
      error: { code: "gpu_oom", reason: "CUDA out of memory at batch 43/298", suggested_action: "reduce batch size from 32 → 16, then retry.", detail: null },
      stages: running().stages.map((s) => (s.name === "label" ? { ...s, status: "failed" as const } : s)),
    };
    stubApi({
      "/api/v1/jobs/job_0142": failed,
      "/api/v1/jobs/job_0142/telemetry": telemetry,
      "/api/v1/jobs/job_0142/events": { items: [], next_cursor: null },
      "/api/v1/system": system,
      "/api/v1/jobs": emptyList,
    });
    renderJob();
    const panel = await screen.findByText(/label failed: CUDA out of memory/);
    const box = panel.closest(".failure") as HTMLElement;
    expect(box).toHaveTextContent("gpu_oom");
    expect(box).toHaveTextContent("Do reduce batch size from 32 → 16, then retry.");
    expect(box).toHaveTextContent("Kept the versions made by ingest, clean, preprocess");
    expect(screen.getAllByRole("button", { name: /Retry/ }).length).toBeGreaterThan(0);
  });

  test("test_UI_2_1_a_queued_job_says_what_it_waits_for", async () => {
    const queued: Job = {
      ...running(),
      id: "job_0146",
      state: "queued",
      started_at: null,
      current_stage: null,
      stages: [stage(0, "label"), stage(1, "filter")],
    };
    const holder = running();
    stubApi({
      "/api/v1/jobs/job_0146": queued,
      "/api/v1/jobs/job_0146/telemetry": telemetry,
      "/api/v1/jobs/job_0146/events": { items: [], next_cursor: null },
      "/api/v1/system": system,
      "/api/v1/jobs": (url: URL) => (url.searchParams.get("state")?.includes("running") ? { ...emptyList, items: [holder] } : emptyList),
    });
    renderJob("/jobs/job_0146");
    expect(await screen.findByText(/Waiting for the gpu worker · job_0142 holds the GPU \(label\)/)).toBeInTheDocument();
  });
});
