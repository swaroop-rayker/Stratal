// SCR-11's task metrics (M10, EVAL-1.2; mockup SCR-11eval_v1): the right metric set for each
// task (EVAL-1.2.AC1), the confusion matrix or the largest confusions, and versions with none.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, test } from "vitest";

import type { VersionDetail } from "../../api/registry";
import { figure, noMetricsReason, TaskMetrics, topConfusions } from "./TaskMetrics";

function version(metrics: Record<string, unknown> | null, extra: Partial<VersionDetail> = {}): VersionDetail {
  return {
    id: "mv_a4a0fb846812", lineage_id: "ml_319245b543d4", state: "trained", is_latest: true, weights_status: "present",
    weights_bytes: 1_163_271, pinned: false, parent_version_id: "mv_3462e579d7dd", job_id: "job_0096", job_state: "completed",
    dataset_version_id: "ds_f8e67211dd94", config_hash: "e0ef".repeat(16), main_metric: 0.804, note: null,
    created_at: "2026-11-20T12:00:00Z", trained_at: "2026-11-20T12:01:00Z", weights_deleted_by_job_id: null,
    retention_action: "keep", retention_reason: "latest (REG-2.2)", lineage_name: "small_cnn · cifar10",
    config: {}, metrics, history: [], weights_path: "registry/x", weights_deleted_at: null, study_id: null, children: [], ...extra,
  };
}

const LABELS = ["cat", "dog", "bird"];
const CLASSIFICATION = {
  task: "classification", val_accuracy: 0.80379, test_accuracy: 0.809827, val_macro_f1: 0.80352, test_macro_f1: 0.809404, val_loss: 0.56742,
  detail: {
    task: "classification",
    val: { rows: 30, labels: LABELS, per_class_f1: { cat: 0.64, dog: 0.7, bird: 0.73 }, confusion_matrix: [[8, 2, 0], [1, 9, 0], [0, 1, 9]] },
    test: { rows: 30, labels: LABELS, per_class_f1: { cat: 0.6, dog: 0.75, bird: 0.8 }, confusion_matrix: [[7, 3, 0], [1, 9, 0], [0, 0, 10]] },
  },
};

function show(v: VersionDetail) {
  return render(<MemoryRouter><div className="scr11"><TaskMetrics version={v} onRetrain={() => undefined} /></div></MemoryRouter>);
}

function rowNames(): string[] {
  const table = screen.getByRole("table", { name: "Task metrics by split" });
  return within(table).getAllByRole("row").slice(1).map((r) => r.querySelector("td")?.textContent ?? "");
}

describe("SCR-11 task metrics", () => {
  test("test_EVAL_1_2_the_right_metric_set_appears_for_each_task", () => {
    const { unmount } = show(version(CLASSIFICATION));
    expect(rowNames()).toEqual(["accuracy", "macro F1", "loss", "rows"]);
    expect(screen.getByText("0.810")).toBeInTheDocument();
    expect(screen.getByRole("grid", { name: "Confusion matrix, validation" })).toBeInTheDocument();
    unmount();

    const regression = show(version({ task: "regression", val_mse: 4730.47, test_mse: 2929.6, val_rmse: 68.78, test_rmse: 54.13, val_mae: 51.4, test_mae: 41.3, val_r2: 0.2915, test_r2: 0.5558, detail: { val: { rows: 44 }, test: { rows: 44 } } }));
    expect(rowNames()).toEqual(["MSE", "RMSE", "MAE", "R²", "rows"]);
    expect(screen.getByText("4,730")).toBeInTheDocument();
    expect(screen.queryByRole("grid")).toBeNull();
    regression.unmount();

    show(version({ task: "clustering", silhouette: 0.412, davies_bouldin: 0.931, clusters: 3, noise_points: 0, detail: { train: { clusters: 3 } } }));
    expect(rowNames()).toEqual(["silhouette", "Davies–Bouldin", "clusters", "noise points"]);
    expect(screen.getByRole("columnheader", { name: "Train" })).toBeInTheDocument();
  });

  test("test_EVAL_1_2_the_matrix_follows_the_split_and_arrow_keys_move_between_cells", () => {
    show(version(CLASSIFICATION));
    const grid = screen.getByRole("grid", { name: "Confusion matrix, validation" });
    const first = within(grid).getByRole("gridcell", { name: /cat predicted as cat: 8 of 10/ });
    first.focus();
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(within(grid).getByRole("gridcell", { name: /cat predicted as dog: 2 of 10/ })).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("cat predicted as dog: 2 of 10 (20.0 %)");

    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(screen.getByRole("grid", { name: "Confusion matrix, test" })).toBeInTheDocument();
  });

  test("test_EVAL_1_2_a_study_test_is_marked_computed_once_and_many_classes_list_the_confusions", () => {
    const labels = Array.from({ length: 25 }, (_, i) => `c${String(i)}`);
    const matrix = labels.map((_, i) => labels.map((__, j) => (i === j ? 40 : i === 3 && j === 7 ? 9 : 0)));
    const f1 = Object.fromEntries(labels.map((l, i) => [l, i / 25]));
    show(version({ ...CLASSIFICATION, detail: { val: { rows: 1000, labels, per_class_f1: f1, confusion_matrix: matrix }, test: { rows: 1000, labels, per_class_f1: f1, confusion_matrix: matrix } } }, { study_id: "st_8293da6ebe0e" }));

    expect(screen.getByText("computed once")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "st_8293da6ebe0e" })).toHaveAttribute("href", "/studies/st_8293da6ebe0e");
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.getByRole("table", { name: "Most confused, validation" })).toHaveTextContent("c3c79");
  });

  test("test_EVAL_1_2_a_version_without_metrics_says_why", () => {
    expect(noMetricsReason(version(null, { state: "base" }))).toMatch(/never trained or scored/);
    expect(noMetricsReason(version(null, { state: "partial" }))).toBe("Scored when the run completes.");
    expect(noMetricsReason(version({ val_accuracy: 0.75 }))).toMatch(/before task metrics existed/);
    expect(noMetricsReason(version(CLASSIFICATION))).toBeNull();
    show(version({ val_accuracy: 0.75 }));
    expect(screen.getByRole("button", { name: /Re-train/ })).toBeInTheDocument();
  });

  test("figures and the largest confusions", () => {
    expect(figure(0.80379)).toBe("0.804");
    expect(figure(68.778)).toBe("68.8");
    expect(figure(4730.47)).toBe("4,730");
    expect(figure(null)).toBe("–");
    expect(topConfusions(["a", "b"], [[5, 3], [1, 9]])).toEqual([["a", "b", 3, 3 / 8], ["b", "a", 1, 0.1]]);
  });
});
