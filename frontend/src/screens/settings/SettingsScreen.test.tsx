// SCR-14 (M8, mockup SCR-14_v1): settings from the database (OC-9), checked from the schema
// (UI-2.3), saved per section; lowering keep_intermediate asks first (REG-2.10).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { changes, fieldErrors, SettingsScreen } from "./SettingsScreen";

const TRAINING_SCHEMA = {
  properties: {
    checkpoint_interval_min: { type: "integer", minimum: 1, maximum: 60, default: 5, description: "Longest time between checkpoints, in minutes. 1–60, default 5.", "x-applies": "new jobs only" },
    thermal_pause_c: { type: "integer", minimum: 60, maximum: 100, default: 83, description: "Pause GPU jobs at this temperature, °C.", "x-applies": "at once" },
    thermal_resume_c: { type: "integer", minimum: 40, maximum: 95, default: 75, description: "Resume below this temperature, °C.", "x-applies": "at once" },
    stall_timeout_s: { type: "integer", minimum: 60, maximum: 3600, default: 300, description: "Seconds without progress before a job is marked stalled. 60–3600, default 300.", "x-applies": "at once" },
  },
};

const TRAINING = {
  name: "training", title: "Training", requirements: ["RUN-1.3", "RUN-1.6", "RUN-1.7"],
  values: { checkpoint_interval_min: 5, thermal_pause_c: 83, thermal_resume_c: 75, stall_timeout_s: 300 },
  schema: TRAINING_SCHEMA, version: "tv1", lineages: null,
  context: { gpu_slowdown_c: 87, gpu_temp_high_7d: { celsius: 74, job_id: "job_0026", at: null }, running_jobs: ["job_0031"] },
};

const RETENTION = {
  name: "retention", title: "Retention", requirements: ["REG-2.3", "REG-2.4", "REG-2.10"],
  values: { keep_intermediate: 2, overrides: {} },
  schema: { properties: { keep_intermediate: { description: "Intermediate versions each lineage keeps besides base and latest." } } },
  version: "rv1", context: null,
  lineages: [{ lineage_id: "ml_1", name: "small_cnn · cifar10", keep_intermediate: 2, source: "global", intermediates: 2, intermediates_bytes: 2_516_582 }],
};

export const RETRIEVAL = {
  name: "retrieval", title: "Retrieval", requirements: ["LLM-2.3"],
  values: { embedding_model: "bge-small-en-v1.5", abstention_threshold: null, threshold_source: null, default_k: 5 },
  schema: {
    properties: {
      embedding_model: { type: "string", readOnly: true },
      abstention_threshold: { anyOf: [{ type: "number", minimum: 0, maximum: 1 }, { type: "null" }], description: "Below this top-1 relevance, RAG answers \"I don't know\"." },
      threshold_source: { anyOf: [{ type: "string" }, { type: "null" }], readOnly: true },
      default_k: { type: "integer", minimum: 1, maximum: 20, default: 5, description: "Sources given to the model per question." },
    },
  },
  version: "qv1", lineages: null, context: null,
};

const PLAN = {
  lineages: [{
    lineage_id: "ml_1", name: "small_cnn · cifar10", keep_intermediate: 0, source: "global", latest_version_id: "mv_c",
    delete: [
      { version_id: "mv_aaaaaaaaaaaa", reason: "beyond", weights_bytes: 1_258_291, main_metric: 0.749, created_at: null },
      { version_id: "mv_bbbbbbbbbbbb", reason: "beyond", weights_bytes: 1_258_291, main_metric: 0.712, created_at: null },
    ],
    keep: [], blocked: [], checkpoints: [], bytes_to_free: 2_516_582,
  }],
  versions_to_delete: 2, bytes_to_free: 2_516_582, token: "plan-token", settings_version: "x",
};

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/settings", element: <SettingsScreen /> }], { initialEntries: ["/settings"] });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-14", () => {
  test("test_RUN_1_6_resume_must_stay_5_degrees_below_pause_and_pause_below_slowdown", () => {
    const values = { checkpoint_interval_min: 5, thermal_pause_c: 80, thermal_resume_c: 78, stall_timeout_s: 300 };
    expect(fieldErrors("training", values, TRAINING_SCHEMA, 87)).toEqual({
      thermal_resume_c: "Must be at most 75: 5 °C below thermal_pause_c (80).",
    });
    expect(fieldErrors("training", { ...values, thermal_pause_c: 90, thermal_resume_c: 70 }, TRAINING_SCHEMA, 87)).toEqual({
      thermal_pause_c: "At most 87: the card's slowdown point.",
    });
    expect(fieldErrors("training", { ...values, stall_timeout_s: 30 }, TRAINING_SCHEMA, null)["stall_timeout_s"]).toBe("At least 60.");
  });

  test("test_RUN_1_7_a_changed_field_shows_old_to_new_and_saves_its_section", async () => {
    const calls = stubApi({
      "/api/v1/settings": (url: URL) => (url.pathname ? { sections: [TRAINING, RETENTION] } : null),
      "/api/v1/telemetry/latest": { ts: "2026-11-18T10:25:00Z", gpu_temp_c: 64 },
    });
    renderSettings();
    const input = await screen.findByLabelText(/stall_timeout_s/);

    fireEvent.change(input, { target: { value: "600" } });

    const bar = screen.getByRole("status");
    expect(bar.textContent).toContain("1 unsaved change");
    expect(bar.textContent).toContain("stall_timeout_s 300 → 600");
    expect(screen.getByText("job_0031")).toBeTruthy();
    expect(screen.getByText("changed")).toBeTruthy();
    expect(screen.getAllByText("1 unsaved change").length).toBeGreaterThan(0);
    fireEvent.click(within(bar).getByRole("button", { name: "Save training" }));
    await vi.waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put?.body).toMatchObject({ section: "training", version: "tv1", values: { stall_timeout_s: 600 } });
    });
  });

  test("test_LLM_2_3_retrieval_section_saves_threshold", async () => {
    const calls = stubApi({
      "/api/v1/settings": { sections: [TRAINING, RETENTION, RETRIEVAL] },
      "/api/v1/telemetry/latest": { ts: "2026-11-27T10:25:00Z", gpu_temp_c: 44 },
    });
    renderSettings();
    const section = (await screen.findByRole("heading", { name: "Retrieval" })).closest("section") as HTMLElement;
    expect(within(section).getByText(/Not set: pick one from an index's retrieval report/)).toBeTruthy();
    expect(within(section).getByText("bge-small-en-v1.5")).toBeTruthy();
    const threshold = within(section).getByLabelText(/abstention_threshold/);
    expect(threshold).toHaveValue("");

    fireEvent.change(threshold, { target: { value: "1.5" } });
    expect(within(section).getByText("A number from 0 to 1, or empty.")).toBeTruthy();
    fireEvent.change(threshold, { target: { value: "0.65" } });
    const bar = within(section).getByRole("status");
    expect(bar.textContent).toContain("abstention_threshold null → 0.65");
    fireEvent.click(within(bar).getByRole("button", { name: "Save retrieval" }));
    await vi.waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put?.body).toMatchObject({ section: "retrieval", version: "qv1", values: { abstention_threshold: 0.65, default_k: 5 } });
    });
  });

  test("test_REG_2_10_lowering_the_global_n_asks_with_the_versions_and_the_phrase", async () => {
    let puts = 0;
    stubApi({
      "/api/v1/settings": () => {
        puts += 1;
        return puts === 1
          ? { sections: [TRAINING, RETENTION] }
          : Response.json({ code: "confirmation_required", detail: "Saving this deletes the weights of 2 versions, 2.4 MB.", title: "Confirmation required", status: 409, suggested_action: null, request_id: null, errors: [], plan: PLAN }, { status: 409 });
      },
      "/api/v1/telemetry/latest": { ts: "2026-11-18T10:25:00Z", gpu_temp_c: 64 },
    });
    renderSettings();
    const input = await screen.findByLabelText(/^keep_intermediate/);
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save retention" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Keep 0 intermediate versions by default (now 2)?");
    expect(within(dialog).getByText("mv_aaaaaaaaaaaa")).toBeTruthy();
    expect(within(dialog).getByText("delete 2 versions")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: /Delete 2 versions' weights/ })).toHaveProperty("disabled", true);
  });

  test("test_REG_2_3_changes_lists_each_field_as_old_to_new", () => {
    expect(changes({ keep_intermediate: 2, overrides: {} }, { keep_intermediate: "all", overrides: { ml_1: 3 } })).toEqual([
      "keep_intermediate 2 → all",
      "overrides",
    ]);
  });
});
