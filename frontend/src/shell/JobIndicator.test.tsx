// UI-2.1 (SCR-01 region 3): the active-job indicator in each of its states.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Job } from "../api/client";
import { JobIndicator } from "./JobIndicator";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job_0142",
    kind: "pipeline",
    state: "running",
    desired_state: null,
    attempt: 1,
    dataset: "coco10k",
    current_stage: "label",
    stages: [
      {
        idx: 3, name: "label", queue: "gpu", status: "running", config: {},
        input_version_id: null, output_version_id: null, cache_hit: false,
        step: 42, total: 313, throughput: 2.4, eta_seconds: 872, activity: null,
        metrics: null, peaks: null, started_at: null, finished_at: null, error: null,
      },
    ],
    output_version_id: null,
    error: null,
    summary: null,
    created_at: "2026-11-20T13:57:00Z",
    started_at: "2026-11-20T13:57:01Z",
    finished_at: null,
    ...overrides,
  };
}

function respond(byState: Record<string, Job[] | "down">) {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const state = new URL(request.url, "http://x").searchParams.get("state") ?? "";
      const items = byState[state] ?? [];
      if (items === "down") return Promise.resolve(new Response("", { status: 502 }));
      return Promise.resolve(
        Response.json({ items, next_cursor: null, counts: {} }, { status: 200 }),
      );
    }),
  );
}

function renderIndicator() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <JobIndicator />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("JobIndicator", () => {
  test("test_UI_2_1_one_running_job_shows_stage_percent_and_eta", async () => {
    respond({ "running,stalled": [job()] });
    renderIndicator();
    const button = await screen.findByRole("button", { name: /Active job job_0142/ });
    expect(button).toHaveTextContent("label");
    expect(button).toHaveTextContent("13%");
    expect(button).toHaveTextContent("ETA 14m 32s");
  });

  test("test_UI_2_1_idle_names_the_last_finished_job", async () => {
    respond({
      "running,stalled": [],
      "completed,failed,cancelled": [job({ id: "job_0141", state: "completed", current_stage: null, finished_at: new Date().toISOString() })],
    });
    renderIndicator();
    const id = await screen.findByText("job_0141"); // the second query has answered
    expect(id.parentElement).toHaveTextContent("No job running · last job_0141 completed just now");
  });

  test("test_UI_2_1_unreadable_jobs_are_not_shown_as_idle", async () => {
    respond({ "running,stalled": "down" });
    renderIndicator();
    expect(await screen.findByText("Jobs unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/No job running/)).toBeNull();
  });

  test("test_UI_2_1_several_jobs_list_the_gpu_job_first", async () => {
    const [label] = job().stages;
    if (label === undefined) throw new Error("fixture has a stage");
    const ingest = { ...label, name: "ingest", queue: "cpu" as const, step: 61, total: 100 };
    const cpu = job({ id: "job_0143", current_stage: "ingest", stages: [ingest] });
    respond({ "running,stalled": [cpu, job()] });
    renderIndicator();
    const button = await screen.findByRole("button", { name: /2 jobs running/ });
    expect(button).toHaveTextContent("label 13% · ingest 61%");
  });
});
