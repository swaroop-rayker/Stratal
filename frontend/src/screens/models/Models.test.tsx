// SCR-11 (M8, mockup SCR-11_v1): lineages and versions with their states (REG-1.2), pins
// (REG-2.5), disk use (REG-2.11), the typed confirmation (REG-2.10) and Try it (REG-1.4).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { DiskReport, LineageDetail, ModelVersion, RetentionPlan } from "../../api/registry";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { diskName } from "./DiskCard";
import { filterVersions, LineageScreen } from "./LineageScreen";
import { ModelsScreen } from "./ModelsScreen";
import { ConfirmDeletion, confirms } from "./parts";
import { servedText } from "./TryIt";

function version(id: string, state: ModelVersion["state"], extra: Partial<ModelVersion> = {}): ModelVersion {
  return {
    id, lineage_id: "ml_319245b543d4", state, is_latest: false, weights_status: "present", weights_bytes: 1_163_901,
    pinned: false, parent_version_id: state === "base" ? null : "mv_3462e579d7dd", job_id: "job_0030", job_state: "completed",
    dataset_version_id: "ds_f8e67211dd94", config_hash: "ab".repeat(32), main_metric: state === "base" ? null : 0.84,
    note: null, created_at: "2026-11-18T05:30:00Z", trained_at: state === "base" ? null : "2026-11-18T05:31:00Z",
    weights_deleted_by_job_id: null, retention_action: "keep", retention_reason: "latest (REG-2.2)", ...extra,
  };
}

const VERSIONS = [
  version("mv_e80834ce01b0", "trained", { is_latest: true }),
  version("mv_c52659b5bd12", "trained", { weights_status: "weights_deleted", retention_action: "none" }),
  version("mv_9a9a9a9a9a9a", "partial", { weights_bytes: null, job_state: "paused", main_metric: null }),
  version("mv_3462e579d7dd", "base"),
];

const LINEAGE: LineageDetail = {
  id: "ml_319245b543d4", name: "small_cnn · cifar10", task: "classification", model: { name: "small_cnn" },
  dataset: "cifar10", input_shape: [3, 32, 32], classes: Array.from({ length: 10 }, (_, i) => `c${String(i)}`),
  parameters: 288_746, counts: { base: 1, partial: 1, trained: 2 }, latest_version_id: "mv_e80834ce01b0",
  latest_state: "trained", main_metric: 0.84354, weights_bytes: 2_326_000, checkpoint_bytes: 9_800_000,
  created_at: "2026-11-18T05:30:00Z", updated_at: "2026-11-18T10:30:00Z", versions: VERSIONS,
  retention: { keep_intermediate: 0, source: "global", last_run: null },
};

const DISK: DiskReport = {
  lineage_id: LINEAGE.id, parts: { base: 1_162_386, latest: 1_165_396, intermediate: 0, pinned: 0, checkpoints: 0 },
  disk_bytes: 2_327_782, registry_bytes: 2_327_782, matches: true, orphans: [], missing: [],
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/models", element: <ModelsScreen /> },
      { path: "/models/:lineageId", element: <LineageScreen /> },
    ],
    { initialEntries: [path] },
  );
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { FakeSource.install(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("SCR-11", () => {
  test("test_REG_1_2_the_three_states_are_shown_and_filterable_on_a_lineage", async () => {
    stubApi({
      [`/api/v1/models/${LINEAGE.id}`]: LINEAGE,
      [`/api/v1/models/${LINEAGE.id}/disk`]: DISK,
      "/api/v1/settings": { sections: [] },
    });
    renderAt(`/models/${LINEAGE.id}`);
    const table = await screen.findByRole("table");

    expect(within(table).getAllByText("Trained")).toHaveLength(2);
    expect(within(table).getByText("Partial")).toBeTruthy();
    expect(within(table).getByText("Base")).toBeTruthy();
    expect(within(table).getByText("deleted")).toBeTruthy();
    expect(within(table).getByText("latest")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^Base/ }));
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.textContent).toContain("mv_3462e579d7dd");
    fireEvent.click(screen.getByRole("button", { name: /^Partial/ }));
    expect(screen.getByRole("table").textContent).toContain("mv_9a9a9a9a9a9a");
    expect(screen.getByRole("table").textContent).not.toContain("mv_e80834ce01b0");
  });

  test("test_REG_1_2_versions_filter_by_state_and_by_weights", () => {
    expect(filterVersions(VERSIONS, "trained", null).map((v) => v.id)).toEqual(["mv_e80834ce01b0", "mv_c52659b5bd12"]);
    expect(filterVersions(VERSIONS, null, "deleted").map((v) => v.id)).toEqual(["mv_c52659b5bd12"]);
    expect(filterVersions(VERSIONS, "trained", "present").map((v) => v.id)).toEqual(["mv_e80834ce01b0"]);
  });

  test("test_REG_1_2_the_lineage_list_filters_by_state_through_the_api", async () => {
    const calls = stubApi({ "/api/v1/models": { items: [LINEAGE], next_cursor: null } });
    renderAt("/models");
    await screen.findByText("small_cnn · cifar10");

    fireEvent.click(screen.getByRole("button", { name: /^Partial/ }));

    expect(calls.some((c) => c.url.searchParams.get("state") === "partial")).toBe(true);
  });

  test("test_REG_2_5_the_pin_is_a_toggle_named_with_its_version", async () => {
    const calls = stubApi({
      [`/api/v1/models/${LINEAGE.id}`]: LINEAGE,
      [`/api/v1/models/${LINEAGE.id}/disk`]: DISK,
      "/api/v1/settings": { sections: [] },
      "/api/v1/model-versions/mv_e80834ce01b0/pin": { ...VERSIONS[0], pinned: true },
    });
    renderAt(`/models/${LINEAGE.id}`);
    const pin = await screen.findByRole("button", { name: "Pin mv_e80834ce01b0" });

    expect(pin.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: "Pin mv_c52659b5bd12" })).toHaveProperty("disabled", true);
    fireEvent.click(pin);
    await vi.waitFor(() => { expect(calls.some((c) => c.method === "POST" && c.url.pathname.endsWith("/pin"))).toBe(true); });
  });

  test("test_REG_2_11_the_disk_bar_is_named_with_its_five_parts", () => {
    expect(diskName(DISK)).toBe("base 1.1 MB, latest 1.1 MB, intermediate 0, pinned 0, checkpoints 0");
  });

  test("test_REG_1_4_try_it_says_which_device_served_it", () => {
    expect(
      servedText({ version_id: "mv_1", lineage_id: "ml_1", device: "cpu", fallback_reason: "job_0031/train holds the GPU", top: [], load_ms: null, total_ms: 38.4 }),
    ).toBe("Served on the CPU: job_0031/train holds the GPU · 38 ms");
  });
});

const PLAN: RetentionPlan = {
  lineages: [{
    lineage_id: LINEAGE.id, name: LINEAGE.name, keep_intermediate: 0, source: "lineage", latest_version_id: "mv_e80834ce01b0",
    delete: [
      { version_id: "mv_aaaaaaaaaaaa", reason: "beyond keep_intermediate", weights_bytes: 1_258_291, main_metric: 0.749, created_at: null },
      { version_id: "mv_bbbbbbbbbbbb", reason: "beyond keep_intermediate", weights_bytes: 1_258_291, main_metric: 0.712, created_at: null },
    ],
    keep: [], blocked: [], checkpoints: [], bytes_to_free: 2_516_582,
  }],
  versions_to_delete: 2, bytes_to_free: 2_516_582, token: "t0k", settings_version: "v1",
};

describe("SCR-11 frame D", () => {
  test("test_REG_2_10_the_confirmation_lists_versions_and_sizes_and_needs_the_name_typed", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDeletion
        open
        onOpenChange={() => undefined}
        title="Keep 0 intermediate versions of small_cnn · cifar10?"
        plan={PLAN}
        phrase="small_cnn · cifar10"
        kept="Kept: the base and the latest."
        onConfirm={onConfirm}
      />,
    );
    const dialog = screen.getByRole("alertdialog");

    expect(dialog.textContent).toContain("2 versions, 2.4 MB");
    expect(within(dialog).getByText("mv_aaaaaaaaaaaa")).toBeTruthy();
    expect(within(dialog).getByText("74.9%")).toBeTruthy();
    expect(within(dialog).getAllByText("1.2 MB")).toHaveLength(2);
    expect(document.activeElement?.textContent).toBe("Keep them");
    const remove = within(dialog).getByRole("button", { name: "Delete 2 versions' weights" });
    expect(remove).toHaveProperty("disabled", true);
    const input = within(dialog).getByRole("textbox");
    fireEvent.change(input, { target: { value: "small_cnn · cif" } });
    expect(remove).toHaveProperty("disabled", true);
    fireEvent.change(input, { target: { value: "small_cnn · cifar10" } });
    expect(remove).toHaveProperty("disabled", false);
    fireEvent.click(remove);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  test("test_REG_2_10_only_the_exact_phrase_confirms", () => {
    expect(confirms("delete 2 versions", "delete 2 versions")).toBe(true);
    expect(confirms("Delete 2 versions", "delete 2 versions")).toBe(false);
    expect(confirms("delete 2 versions ", "delete 2 versions")).toBe(false);
  });
});
