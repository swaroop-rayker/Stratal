// SCR-13 (M11, mockup SCR-13_v1; MON-2.6, MON-2.7, MON-3.2, MON-3.3, MON-4.4; ADR-0020):
// alerts with the actions they caused, diagnostics, loads and unloads, two runs overlaid;
// History (MON-4.5, mockup SCR-13history_v1): the last N runs of a stage.
// The canvas is replaced: jsdom has none, and the lanes' figures are checked through the
// labels and the tables.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { fieldErrors } from "../settings/SettingsScreen";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { SystemScreen } from "./SystemScreen";

vi.mock("./Lanes", () => ({
  LANE_H: 56,
  LanesChart: ({ lanes }: { lanes: { label: string; series: { name: string; dashed?: boolean }[] }[] }) => (
    <ul data-testid="lanes">
      {lanes.map((l) => (
        <li key={l.label}>{l.label}: {l.series.map((s) => `${s.name || "line"}${s.dashed ? " dashed" : ""}`).join(", ")}</li>
      ))}
    </ul>
  ),
}));

const T = (s: number) => new Date(Date.UTC(2026, 10, 17, 11, 30, s)).toISOString();

function event(over: Record<string, unknown>) {
  return {
    id: 1, event_uid: "u", ts: T(0), job_id: "job_0026", stage: "label", substage: null, version_id: null,
    type: "action", level: "INFO", step: null, total: null, message: "", metrics: null, reason: null,
    suggested_action: null, attempt: 1, caused_by: null, ...over,
  };
}

const FIRED = event({
  id: 10, event_uid: "a1", type: "alert", level: "WARN", ts: T(14),
  message: "GPU at 84 °C for 5 s: paused job_0026",
  metrics: { code: "gpu_hot", state: "fired", value: 84, threshold: 83, rearm_below: 75 },
  reason: "The GPU reached 84 °C; the limit is 83 °C.",
  suggested_action: "None needed: paused GPU jobs resume by themselves below 75 °C. Check the laptop's airflow.",
});
const PAUSE = event({ id: 11, event_uid: "p1", ts: T(14), level: "WARN", message: "GPU at 84 °C: pausing job_0026 (limit 83 °C)", metrics: { code: "thermal_pause" }, caused_by: "a1" });
const RESUME = event({ id: 13, event_uid: "r1", ts: T(211), message: "GPU cooled to 74 °C: resuming job_0026", metrics: { code: "thermal_resume" }, caused_by: "a1" });
const CLEARED = event({ id: 12, event_uid: "c1", type: "alert", ts: T(211), message: "GPU back below 75 °C (74 °C)", metrics: { code: "gpu_hot", state: "cleared", value: 74 }, caused_by: "a1" });

const ALERTS = {
  items: [
    { code: "gpu_hot", firing: false, fired: FIRED, cleared: CLEARED, actions: [PAUSE, RESUME], lasted_s: 197 },
    {
      code: "ram_high", firing: false, lasted_s: 12, actions: [], cleared: event({ id: 3, event_uid: "c2", type: "alert", job_id: null, caused_by: "a2" }),
      fired: event({ id: 2, event_uid: "a2", type: "alert", level: "WARN", job_id: null, stage: null, message: "RAM at 87% of the VM", metrics: { code: "ram_high", state: "fired", value: 87, threshold: 85 }, reason: "x", suggested_action: "y" }),
    },
  ],
  thresholds: { vram_percent: 90, ram_percent: 85, pause_c: 83, resume_c: 75, stall_s: 300 },
};

const DIAGNOSTICS = {
  logical_cpus: 16,
  live: [{
    job_id: "job_0026", stage: "label", queue: "gpu", expected: ["gpu"], running_for_s: 300,
    observed: { samples: 30, gpu_percent: 22, cpu_cores: 0.97, capacity_cores: 1, loader_workers: 0, disk_read_mb_s: 4 },
    thresholds: { gpu_low_percent: 30, cpu_saturated_share: 0.85, disk_mb_s: 50 },
    diagnosis: { code: "diag_data_loading", message: "Data loading is the bottleneck: GPU at 22% while the job's CPU is at 0.97 of 1 core", reason: "x", suggested_action: "Increase DataLoader workers (now 0)." },
  }],
  items: [{ fired: event({ id: 20, event_uid: "d1", type: "diagnostic", level: "WARN", message: "Data loading: GPU 21 %, CPU 1.0 of 1", metrics: { state: "fired", code: "diag_data_loading" } }), cleared: null, lasted_s: null }],
};

const MODELS = {
  items: [event({
    id: 30, event_uid: "m1", type: "model_unloaded", ts: T(5),
    message: "Unloaded Florence-2-base: VRAM 0.44 → 0.00 GB (PyTorch), card 4.9 → 1.1 GB; 0.44 GB freed, allocated VRAM back to its baseline",
    metrics: { allocated_mb_before: 452.1, allocated_mb_after: 0, reserved_mb_before: 3870, reserved_mb_after: 0, card_used_mb_before: 4969, card_used_mb_after: 1099 },
  })],
  next_cursor: null,
};

const TELEMETRY = {
  from: T(0), to: T(59), bucket_s: 20,
  points: [
    { ts: T(0), minute_average: false, cpu_percent: 9, ram_used_mb: 3500, ram_total_mb: 9944, gpu_util_percent: 62, vram_used_mb: 4200, vram_total_mb: 6141, gpu_temp_c: 67, disk_read_mb_s: 2 },
    { ts: T(20), minute_average: false, cpu_percent: 7, ram_used_mb: 3400, ram_total_mb: 9944, gpu_util_percent: 40, vram_used_mb: 4100, vram_total_mb: 6141, gpu_temp_c: 66, disk_read_mb_s: 1 },
  ],
  jobs: [{ job_id: "job_0026", kind: "pipeline", state: "completed", started_at: T(0), finished_at: T(50), stages: [{ name: "label", status: "completed", started_at: T(0), finished_at: T(50) }] }],
};

function side(id: string, label: string, perEpoch: number, gpu: number) {
  return {
    job_id: id, kind: "train", state: "completed", label, started_at: T(0), running_s: perEpoch * 10, epochs: 10,
    machine: [{ x: 0.5, gpu_util_percent: gpu, vram_used_mb: 750, gpu_temp_c: 62, cpu_percent: 20, ram_used_mb: 3000, minute_average: false }],
    process: [{ x: 0.5, rss_mb: 6000, torch_allocated_mb: 146, cpu_cores: 1.1 }],
    summary: { seconds_per_epoch: perEpoch, gpu_mean_percent: gpu, vram_peak_mb: 758, torch_allocated_peak_mb: 146, rss_mean_mb: 6758, cpu_cores_mean: 1.1, gpu_temp_max_c: 63 },
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/system", element: <SystemScreen /> }, { path: "/jobs/:id", element: <p>job</p> }], { initialEntries: [path] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

const ROUTES = {
  "/api/v1/telemetry": TELEMETRY,
  "/api/v1/telemetry/latest": { ts: new Date().toISOString() },
  "/api/v1/system/alerts": ALERTS,
  "/api/v1/system/diagnostics": DIAGNOSTICS,
  "/api/v1/system/models": MODELS,
};

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-13 system monitor", () => {
  test("test_MON_2_7_an_alert_and_its_action_are_shown_together", async () => {
    stubApi(ROUTES);
    renderAt("/system?from=2026-11-17T11:30:00Z&to=2026-11-17T11:31:00Z");

    const alerts = await screen.findByRole("table", { name: "Alerts in the range" });
    const hot = within(alerts).getByText("GPU hot").closest("tr");
    expect(hot).not.toBeNull();
    const row = within(hot as HTMLElement);
    expect(row.getByText("84 °C ≥ 83")).toBeInTheDocument();
    expect(row.getByText("Paused")).toBeInTheDocument();
    expect(row.getByText(/resumed/)).toBeInTheDocument();
    expect(row.getByText("3m 17s")).toBeInTheDocument();
    const ram = within(within(alerts).getByText("RAM high").closest("tr") as HTMLElement);
    expect(ram.getByText("no job")).toBeInTheDocument();
    expect(ram.getByText("warned only")).toBeInTheDocument();

    fireEvent.click(hot as HTMLElement);
    const drawer = await screen.findByRole("dialog", { name: /GPU hot/ });
    expect(within(drawer).getByText("What it caused")).toBeInTheDocument();
    expect(within(drawer).getByText(/pausing job_0026/)).toBeInTheDocument();
    expect(within(drawer).getByText(/resuming job_0026/)).toBeInTheDocument();
    expect(within(drawer).getByText(/back below 75/)).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: /Open job_0026's log/ })).toHaveAttribute("href", "/jobs/job_0026");
  });

  test("test_MON_3_2_the_diagnosis_and_the_three_observed_figures", async () => {
    stubApi(ROUTES);
    renderAt("/system?from=2026-11-17T11:30:00Z&to=2026-11-17T11:31:00Z");
    expect(await screen.findByText(/Data loading is the bottleneck: GPU at 22%/)).toBeInTheDocument();
    expect(screen.getByText("Increase DataLoader workers (now 0).")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Job CPU: 0.97 of 1 core" })).toBeInTheDocument();
    expect(screen.getByText("expected GPU")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Earlier diagnoses" })).toHaveTextContent("ongoing");
  });

  test("test_MON_3_3_an_unload_shows_vram_before_and_after", async () => {
    stubApi(ROUTES);
    renderAt("/system?from=2026-11-17T11:30:00Z&to=2026-11-17T11:31:00Z");
    const loads = await screen.findByRole("table", { name: "Model loads and unloads in the range" });
    const row = within(loads).getByText("Florence-2-base").closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("0.44 → 0.00 GB");
    expect(row).toHaveTextContent("4.9 → 1.1 GB");
    expect(row).toHaveAttribute("title", "reserved 3.8 → 0.0 GB");
  });

  test("test_MON_2_1_the_lanes_and_the_jobs_strip", async () => {
    stubApi(ROUTES);
    renderAt("/system?from=2026-11-17T11:30:00Z&to=2026-11-17T11:31:00Z");
    expect(await screen.findByTestId("lanes")).toHaveTextContent("GPU temp");
    expect(screen.getByText("max 3.4 / 9.7 GB")).toBeInTheDocument();
    expect(screen.getByText("card · max 4.1 / 6.0 GB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /job_0026 · label/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nov/ })).toHaveAttribute("aria-pressed", "true");
  });

  test("test_MON_4_4_two_runs_overlay_with_a_solid_and_a_dashed_line", async () => {
    stubApi({
      "/api/v1/jobs": { items: [], next_cursor: null, counts: {} },
      "/api/v1/system/compare": (url: URL) => ({
        align: url.searchParams.get("align"), common_epochs: 10, epoch_unavailable: null,
        a: side("job_0030", "small_cnn · 10 epochs", 4.9, 73),
        b: side("job_0051", "small_cnn · 30 epochs", 8.8, 58),
      }),
    });
    renderAt("/system?tab=compare&a=job_0030&b=job_0051&align=epoch");
    expect(await screen.findByText("Epochs 1–10, the epochs both ran")).toBeInTheDocument();
    expect(screen.getByTestId("lanes")).toHaveTextContent("GPU: A, B dashed");
    const summary = screen.getByRole("table", { name: "Means and peaks over the aligned part" });
    const perEpoch = within(within(summary).getByText("Seconds per epoch").closest("tr") as HTMLElement);
    expect(perEpoch.getByText("4.9 s")).toBeInTheDocument();
    expect(perEpoch.getByText("8.8 s")).toBeInTheDocument();
  });

  test("test_MON_4_4_by_epoch_is_disabled_with_its_reason_for_a_pipeline", async () => {
    stubApi({
      "/api/v1/jobs": { items: [], next_cursor: null, counts: {} },
      "/api/v1/system/compare": {
        align: "time", common_epochs: null, epoch_unavailable: "By epoch needs two training runs: job_0026 is a pipeline.",
        a: side("job_0030", "small_cnn · 10 epochs", 4.9, 73), b: { ...side("job_0026", "pipeline", 0, 40), kind: "pipeline" },
      },
    });
    renderAt("/system?tab=compare&a=job_0030&b=job_0026");
    const epoch = await screen.findByRole("button", { name: "By epoch" });
    await vi.waitFor(() => { expect(epoch).toHaveAttribute("aria-disabled", "true"); });
    expect(epoch).toHaveAttribute("title", "By epoch needs two training runs: job_0026 is a pipeline.");
  });

  test("test_MON_2_6_the_monitoring_thresholds_are_checked_on_settings", () => {
    const schema = {
      properties: {
        sample_interval_active_s: { type: "number", minimum: 0.5, maximum: 10 },
        sample_interval_idle_s: { type: "number", minimum: 2, maximum: 60 },
        vram_alert_percent: { type: "integer", minimum: 50, maximum: 99 },
      },
    };
    expect(fieldErrors("monitoring", { sample_interval_active_s: 1.5, sample_interval_idle_s: 10, vram_alert_percent: 90 }, schema, null)).toEqual({});
    expect(fieldErrors("monitoring", { sample_interval_active_s: 5, sample_interval_idle_s: 3, vram_alert_percent: 100 }, schema, null)).toEqual({
      sample_interval_idle_s: "At least 5: the active interval.",
      vram_alert_percent: "At most 99.",
    });
  });

  const GROUP = "g_cnn";
  function hrun(id: string, minute: number, over: Record<string, unknown> = {}) {
    return {
      job_id: id, kind: "train", started_at: new Date(Date.UTC(2026, 10, 22, 11, minute)).toISOString(), group: GROUP, reused: false,
      attempts: 1, running_s: 62.9, epochs: 10, time: 6.29, rate: 10543, items: null, computed: null,
      peaks: { vram_used_mb: 758.2, torch_allocated_mb: 149, ram_used_mb: 4362, gpu_temp_c: 69, rss_mb: 7430 }, notes: [], ...over,
    };
  }
  const HISTORY = {
    stages: [{ stage: "train", queue: "gpu", runs: 30, reused: 0, last_at: T(0) }, { stage: "label", queue: "gpu", runs: 8, reused: 1, last_at: T(0) }],
    stage: "train", queue: "gpu", rate_unit: "samples/s", time_unit: "s/epoch",
    groups: [{ key: GROUP, label: "small_cnn · cifar10 · batch 128", runs: 3 }, { key: "g_mlp", label: "mlp (reference) · cifar10 · batch 128", runs: 5 }],
    group: GROUP, n: 20,
    runs: [
      hrun("job_0118", 40),
      hrun("job_0114", 36, { attempts: 2, running_s: 109.6, time: 10.96, rate: 10834, notes: ["resumed"] }),
      hrun("job_0106", 20, { epochs: 3, time: 18.87, rate: 8804, notes: ["fine_tune"] }),
    ],
  };

  test("test_MON_4_5_trends_are_shown_for_the_last_n_runs_of_a_stage", async () => {
    const calls = stubApi({ ...ROUTES, "/api/v1/system/history": HISTORY });
    renderAt("/system?tab=history");
    expect(await screen.findAllByText("Time per epoch")).toHaveLength(2); // the lane and the column
    for (const lane of ["Rate", "VRAM peak", "PyTorch allocated", "RAM peak", "GPU temp"]) expect(screen.getByText(lane)).toBeInTheDocument();
    expect(screen.getByText("all 3 runs of this group · oldest left · dashed: the group's median")).toBeInTheDocument();
    expect(screen.getByText("11.0 s · worker lost, resumed")).toBeInTheDocument();
    expect(screen.getByText("18.9 s · fine-tune")).toBeInTheDocument();
    expect(screen.getByText("median 11.0 s · latest 6.3 s")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /Runs of train in small_cnn/ });
    const lost = within(within(table).getByText("job_0114").closest("tr") as HTMLElement);
    expect(lost.getByText("10,834")).toBeInTheDocument();
    expect(lost.getByText("2 attempts · worker lost, resumed")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Include stages reused/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "50" }));
    await vi.waitFor(() => { expect(calls.some((c) => c.url.pathname === "/api/v1/system/history" && c.url.searchParams.get("n") === "50")).toBe(true); });
  });

  test("test_MON_4_5_two_ticked_runs_open_compare_with_the_older_as_a", async () => {
    stubApi({ ...ROUTES, "/api/v1/system/history": HISTORY, "/api/v1/jobs": { items: [], next_cursor: null, counts: {} } });
    const router = renderAt("/system?tab=history");
    const compare = await screen.findByRole("button", { name: "Compare these two" });
    expect(compare).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select job_0118" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select job_0106" }));
    expect(compare).toBeEnabled();
    fireEvent.click(compare);
    const params = new URLSearchParams(router.state.location.search);
    expect([params.get("tab"), params.get("a"), params.get("b")]).toEqual(["compare", "job_0106", "job_0118"]);
  });

  test("test_MON_4_5_all_groups_put_each_run_against_its_groups_median", async () => {
    const all = {
      ...HISTORY, group: null,
      runs: [hrun("job_0118", 40), hrun("job_0047", 10, { group: "g_mlp", rate: 4000, time: 10 }), hrun("job_0042", 5, { group: "g_mlp", rate: 4400, time: 11 })],
    };
    stubApi({ ...ROUTES, "/api/v1/system/history": all });
    renderAt("/system?tab=history&group=all");
    expect(await screen.findByText("Rate, against its group")).toBeInTheDocument();
    expect(screen.getAllByText("100 % = the group's median").length).toBeGreaterThan(0);
    expect(screen.getByText("mlp (reference) · cifar10 · batch 128")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /job_0047, .*: 95 %/ }).length).toBeGreaterThan(0);
  });
});
