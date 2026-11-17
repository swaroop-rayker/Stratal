// RUN-1.1, RUN-1.2 slice (SCR-07): the jobs list with its filters, cancel and retry.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Job, JobStage } from "../../api/client";
import { globalFeed } from "../../api/stream";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { JobsScreen } from "./JobsScreen";

function stage(idx: number, name: string, extra: Partial<JobStage> = {}): JobStage {
  return {
    idx, name, queue: name === "label" ? "gpu" : "cpu", status: "pending", config: {}, input_version_id: null,
    output_version_id: null, cache_hit: false, step: null, total: null, throughput: null, eta_seconds: null,
    activity: null, metrics: null, peaks: null, started_at: null, finished_at: null, error: null, ...extra,
  };
}

function job(id: string, state: Job["state"], extra: Partial<Job> = {}): Job {
  return {
    id, kind: "pipeline", state, desired_state: null, attempt: 1, dataset: "coco10k", current_stage: null,
    output_version_id: null, error: null, summary: null, created_at: "2026-11-20T13:54:19Z",
    started_at: "2026-11-20T13:54:20Z", finished_at: state === "running" || state === "queued" ? null : "2026-11-20T13:58:00Z",
    stages: [], ...extra,
  };
}

const running = job("job_0142", "running", {
  current_stage: "label",
  stages: [stage(0, "clean", { status: "completed", output_version_id: "ds_c1" }), stage(1, "label", { status: "running", step: 42, total: 313, eta_seconds: 900 })],
});
const queued = job("job_0146", "queued", { dataset: "birds2k", started_at: null, attempt: 0, stages: [stage(0, "label", { status: "queued" })] });
const failed = job("job_0145", "failed", {
  error: { code: "gpu_oom", reason: "CUDA out of memory at batch 43/298" },
  stages: [stage(0, "label", { status: "failed" })],
});
const done = job("job_0147", "completed", { output_version_id: "ds_b0c3e5a8d217" });
const cancelled = job("job_0140", "cancelled", {
  dataset: "birds2k",
  stages: [stage(0, "ingest", { status: "completed", output_version_id: "ds_i1" }), stage(1, "clean", { status: "cancelled" })],
});
const counts = { queued: 1, running: 1, paused: 0, stalled: 0, completed: 1, failed: 1, cancelled: 1 };

/** GET /jobs answers from the fixtures by its state list and dataset. */
function routes(jobs: Job[] = [running, queued, done, failed, cancelled], extra: Record<string, unknown> = {}) {
  return stubApi({
    ...extra,
    "/api/v1/jobs": (url: URL) => {
      const states = new Set((url.searchParams.get("state") ?? "").split(","));
      const dataset = url.searchParams.get("dataset");
      return {
        items: jobs.filter((j) => states.has(j.state) && (!dataset || j.dataset === dataset)),
        next_cursor: null,
        counts: dataset ? Object.fromEntries(Object.keys(counts).map((s) => [s, jobs.filter((j) => j.state === s && j.dataset === dataset).length])) : counts,
      };
    },
    "/api/v1/datasets": { items: [], next_cursor: null },
  });
}

function Where() {
  return <span data-testid="where">{useLocation().pathname + useLocation().search}</span>;
}

function renderJobs(path = "/jobs") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs" element={<><JobsScreen /><Where /></>} />
          <Route path="/jobs/:jobId" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  FakeSource.install();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-07", () => {
  test("test_RUN_1_1_active_jobs_are_pinned_above_finished_ones_with_what_each_is_doing", async () => {
    routes();
    renderJobs();
    const table = await screen.findByRole("table", { name: "Jobs" });
    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toMatch(/^Active2/);
    expect(rows[1]).toMatch(/^job_0142Running/);
    expect(rows[1]).toContain("label");
    expect(rows[1]).toContain("13%· ETA 15m 00s");
    expect(rows[2]).toContain("Waiting for the gpu worker · job_0142 holds the GPU (label)");
    expect(rows[3]).toMatch(/^Finished3/);
    expect(rows[4]).toContain("ds_b0c3e5a8d217");
    expect(rows[5]).toContain("gpu_oom in label· CUDA out of memory at batch 43/298");
    expect(rows[6]).toContain("cancelled in clean · 1 version kept");

    const states = screen.getByRole("group", { name: "State" });
    expect(within(states).getByRole("button", { name: "All, 5 jobs" })).toHaveAttribute("aria-pressed", "true");
    expect(within(states).getByRole("button", { name: "Active, 2 jobs" })).toBeInTheDocument();
    expect(within(states).getByRole("button", { name: "Failed, 1 job" })).toBeInTheDocument();
    // Actions only where they apply: cancel while active, retry when failed.
    expect(screen.getByRole("button", { name: "Cancel job_0142" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel job_0146" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry job_0145" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /job_0147|job_0140/ })).toBeNull();
  });

  test("test_RUN_1_1_the_state_filter_asks_the_api_and_is_kept_in_the_url", async () => {
    const calls = routes();
    renderJobs();
    fireEvent.click(await screen.findByRole("button", { name: "Failed, 1 job" }));
    expect(await screen.findByText("Showing 1 of 1")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/jobs?state=failed");
    expect(calls.some((c) => c.url.searchParams.get("state") === "failed")).toBe(true);
    expect(screen.queryByText("Finished")).toBeNull(); // sections only on All
    expect(screen.getByText("job_0145")).toBeInTheDocument();
  });

  test("test_RUN_1_1_a_filter_with_no_matches_offers_to_clear_it", async () => {
    routes();
    renderJobs("/jobs?state=cancelled&dataset=coco10k");
    expect(await screen.findByText("No cancelled jobs for coco10k")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Clear filters/ }));
    expect(await screen.findByText("job_0140")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/jobs$/);
  });

  test("test_RUN_1_2_cancelling_a_queued_job_asks_first_and_says_nothing_is_lost", async () => {
    const calls = routes(undefined, { "/api/v1/jobs/job_0146/cancel": { ...queued, state: "cancelled" } });
    renderJobs();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel job_0146" }));
    const dialog = await screen.findByRole("dialog", { name: "Cancel job_0146?" });
    expect(dialog).toHaveTextContent("It has not started: nothing is lost and nothing was made.");
    expect(document.activeElement).toHaveTextContent("Keep it queued"); // the safe choice first
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: /Cancel job/ }));
    await vi.waitFor(() => {
      expect(calls.find((c) => c.method === "POST")?.url.pathname).toBe("/api/v1/jobs/job_0146/cancel");
    });
    await vi.waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  test("test_RUN_1_2_retry_queues_a_failed_job_again_and_links_to_it", async () => {
    const calls = routes(undefined, { "/api/v1/jobs/job_0145/retry": { ...failed, state: "queued", error: null } });
    renderJobs();
    fireEvent.click(await screen.findByRole("button", { name: "Retry job_0145" }));
    const toast = await screen.findByText(/queued again · attempt 2/);
    expect(toast).toHaveTextContent("job_0145");
    expect(calls.find((c) => c.method === "POST")?.url.pathname).toBe("/api/v1/jobs/job_0145/retry");
    fireEvent.click(within(toast.closest(".toast") as HTMLElement).getByRole("button", { name: /Open/ }));
    expect(await screen.findByTestId("where")).toHaveTextContent("/jobs/job_0145");
  });

  test("test_RUN_1_1_a_state_change_on_the_stream_moves_the_job_and_is_announced", async () => {
    let jobs = [running, queued, done, failed, cancelled];
    stubApi({
      "/api/v1/jobs": (url: URL) => {
        const states = new Set((url.searchParams.get("state") ?? "").split(","));
        return { items: jobs.filter((j) => states.has(j.state)), next_cursor: null, counts };
      },
      "/api/v1/datasets": { items: [], next_cursor: null },
    });
    renderJobs();
    await screen.findByText("job_0142");
    const finished = { ...running, state: "completed" as const, current_stage: null, output_version_id: "ds_new000000001" };
    jobs = [finished, queued, done, failed, cancelled];
    act(() => {
      globalFeed.dispatch("job", finished);
    });
    expect(await screen.findByText("ds_new000000001")).toBeInTheDocument();
    expect(screen.getByText("job_0142 completed")).toHaveAttribute("aria-live", "polite");
    const rows = within(screen.getByRole("table", { name: "Jobs" })).getAllByRole("row").map((r) => r.textContent);
    expect(rows.findIndex((r) => r.startsWith("job_0142"))).toBeGreaterThan(rows.findIndex((r) => r.startsWith("Finished")));
  });

  test("test_RUN_1_1_j_and_k_move_between_rows_and_enter_opens_the_job", async () => {
    routes();
    renderJobs();
    await screen.findByText("job_0142");
    act(() => {
      fireEvent.keyDown(window, { key: "j" });
    });
    expect(document.activeElement).toHaveTextContent(/^job_0146/);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Enter" });
    expect(await screen.findByTestId("where")).toHaveTextContent("/jobs/job_0146");
  });
});
