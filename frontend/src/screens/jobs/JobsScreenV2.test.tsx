// SCR-07 v2 (M8, mockup SCR-07v2_v1): pause and resume (RUN-1.2), training jobs (RUN-1.1),
// and why a job is paused (RUN-1.6) or stalled (RUN-1.7).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Job, JobStage } from "../../api/client";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { JobsScreen } from "./JobsScreen";

function stage(name: string, extra: Partial<JobStage> = {}): JobStage {
  return {
    idx: 0, name, queue: name === "train" ? "gpu" : "cpu", status: "running", config: {}, input_version_id: null,
    output_version_id: null, cache_hit: false, step: null, total: null, throughput: null, eta_seconds: null,
    activity: null, metrics: null, peaks: null, started_at: "2026-11-18T10:00:00Z", finished_at: null, error: null, ...extra,
  };
}

function job(id: string, state: Job["state"], extra: Partial<Job> = {}): Job {
  return {
    id, kind: "pipeline", state, desired_state: null, attempt: 1, dataset: "coco10k", current_stage: null,
    output_version_id: null, error: null, summary: null, created_at: "2026-11-18T10:00:00Z",
    started_at: "2026-11-18T10:00:01Z", finished_at: null, stages: [], ...extra,
  };
}

const TRAIN_CONFIG = { model: { name: "small_cnn" }, epochs: 20 };
const training = job("job_0031", "running", {
  kind: "train", dataset: "cifar10", current_stage: "train",
  stages: [stage("train", { config: TRAIN_CONFIG, step: 2040, total: 6240, eta_seconds: 1120, metrics: { epoch: 7 } })],
});
const heat = job("job_0034", "paused", {
  pause_reason: "thermal", current_stage: "label", stages: [stage("label", { status: "paused" })],
});
const byYou = job("job_0030", "paused", {
  kind: "train", dataset: "cifar10", pause_reason: "user",
  stages: [stage("train", { status: "paused", config: { model: { name: "tiny_mlp" }, epochs: 10 }, metrics: { epoch: 4 } })],
});
const stalled = job("job_0035", "stalled", {
  dataset: "demo", current_stage: "clean", stages: [stage("clean", { last_progress_at: "2026-11-18T10:20:00Z" })],
});
const trained = job("job_0029", "completed", {
  kind: "train", dataset: "cifar10", finished_at: "2026-11-18T11:00:00Z",
  stages: [stage("train", { status: "completed", config: TRAIN_CONFIG, metrics: { val_accuracy: 0.84 } })],
});

function routes(extra: Record<string, unknown> = {}) {
  const all = [training, heat, byYou, stalled, trained];
  return stubApi({
    ...extra,
    "/api/v1/jobs": (url: URL) => {
      const states = new Set((url.searchParams.get("state") ?? "").split(","));
      return {
        items: all.filter((j) => states.has(j.state)),
        next_cursor: null,
        counts: { queued: 0, running: 1, paused: 2, stalled: 1, completed: 1, failed: 0, cancelled: 0 },
      };
    },
    "/api/v1/datasets": { items: [], next_cursor: null },
    "/api/v1/system": {
      gpu_name: "RTX 4050", gpu_error: null, vram_total_mb: 6141, vram_cap_fraction: 0.85, vram_cap_mb: 5220,
      ram_total_mb: 9700, cpu_count: 16, thermal_pause_c: 83, thermal_resume_c: 75, stall_timeout_s: 300,
    },
    "/api/v1/telemetry/latest": { ts: "2026-11-18T10:25:00Z", gpu_temp_c: 79.2 },
  });
}

function renderJobs() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/jobs"]}>
        <Routes>
          <Route path="/jobs" element={<JobsScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  FakeSource.install();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-18T10:25:12Z"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function rowOf(id: string): HTMLElement {
  const cell = screen.getByText(id);
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${id}`);
  return row;
}

describe("SCR-07 v2", () => {
  test("test_RUN_1_2_a_running_job_offers_pause_and_a_paused_one_resume", async () => {
    const calls = routes({
      "/api/v1/jobs/job_0031/pause": { ...training, desired_state: "paused" },
      "/api/v1/jobs/job_0030/resume": { ...byYou, state: "queued" },
    });
    renderJobs();
    await screen.findByText("job_0031");

    fireEvent.click(screen.getByRole("button", { name: "Pause job_0031" }));
    fireEvent.click(screen.getByRole("button", { name: "Resume job_0030" }));

    await vi.waitFor(() => {
      const posts = calls.filter((c) => c.method === "POST").map((c) => c.url.pathname);
      expect(posts).toEqual(["/api/v1/jobs/job_0031/pause", "/api/v1/jobs/job_0030/resume"]);
    });
    expect(screen.queryByRole("button", { name: "Pause job_0034" })).toBeNull(); // paused: no pause
    expect(screen.queryByRole("button", { name: "Resume job_0031" })).toBeNull(); // running: no resume
    expect(screen.getByRole("button", { name: "Cancel job_0034" })).toBeInTheDocument(); // a paused job can be cancelled
  });

  test("test_RUN_1_1_a_training_row_names_its_model_epoch_and_result", async () => {
    routes();
    renderJobs();
    await screen.findByText("job_0031");

    const run = rowOf("job_0031");
    expect(run).toHaveTextContent("small_cnn on cifar10");
    expect(run).toHaveTextContent("epoch 7/20");
    expect(run).toHaveTextContent("32%· ETA 18m 40s");
    expect(rowOf("job_0029")).toHaveTextContent("val acc 84.0%");
  });

  test("test_RUN_1_6_a_job_paused_for_heat_says_when_it_resumes_and_by_you_says_so", async () => {
    routes();
    renderJobs();
    await screen.findByText("job_0034");

    expect(rowOf("job_0034")).toHaveTextContent("heat · resumes below 75 °C (79 °C now)");
    expect(rowOf("job_0030")).toHaveTextContent("by you at epoch 4 · checkpoint kept");
    expect(within(rowOf("job_0034")).getByText("Paused")).toBeInTheDocument(); // icon and word, not colour alone
  });

  test("test_RUN_1_7_a_stalled_job_says_how_long_it_has_been_silent", async () => {
    routes();
    renderJobs();
    await screen.findByText("job_0035");

    expect(rowOf("job_0035")).toHaveTextContent("no progress for 5m 12s");
    expect(within(rowOf("job_0035")).getByText("Stalled")).toBeInTheDocument();
  });
});
