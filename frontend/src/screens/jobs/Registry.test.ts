// SCR-07 v2 with the registry (M8 part B): what a retention job freed (REG-2.9).
import { describe, expect, test } from "vitest";

import type { Job } from "../../api/client";
import { retentionOutcome } from "./JobsScreen";

function retention(metrics: Record<string, number>): Job {
  return {
    id: "job_0036", kind: "retention", state: "completed", desired_state: null, attempt: 1, dataset: null,
    current_stage: null, output_version_id: null, error: null, summary: null, created_at: "2026-11-18T11:27:00Z",
    started_at: null, finished_at: null,
    stages: [{
      idx: 0, name: "retention", queue: "cpu", status: "completed", config: {}, input_version_id: null,
      output_version_id: null, cache_hit: false, step: null, total: null, throughput: null, eta_seconds: null,
      activity: null, metrics, peaks: null, started_at: null, finished_at: null, error: null,
    }],
  };
}

describe("SCR-07 v2 retention rows", () => {
  test("test_REG_2_9_a_retention_row_says_what_it_freed", () => {
    expect(retentionOutcome(retention({ versions_deleted: 3, bytes_freed: 412 * 2 ** 20, dry_run: 0 }))).toBe(
      "retention · freed 412 MB from 3 versions",
    );
    expect(retentionOutcome(retention({ versions_deleted: 1, checkpoints_removed: 1, bytes_freed: 2 ** 20, dry_run: 0 }))).toBe(
      "retention · freed 1.0 MB from 1 version and 1 checkpoint",
    );
    expect(retentionOutcome(retention({ versions_deleted: 0, bytes_freed: 0, dry_run: 0 }))).toBe("retention · nothing to delete");
    expect(retentionOutcome(retention({ dry_run: 1, would_delete: 2, bytes_to_free: 2 * 2 ** 20 }))).toBe(
      "retention dry run · would delete 2 versions, 2.0 MB",
    );
  });
});
