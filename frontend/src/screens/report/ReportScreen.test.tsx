// EVAL-1.1 (SCR-06): the eval report shows what each stage removed, the CLIP scores against
// the filter threshold, the filter rate and the review's acceptance, each chart with a table.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Job, Schemas } from "../../api/client";
import { globalFeed } from "../../api/stream";
import { stubApi } from "../../test/api";
import { FakeSource } from "../../test/eventsource";
import { ReportScreen } from "./ReportScreen";

type Report = Schemas["Report"];
type Review = Schemas["ReportReview"];
type Summary = Schemas["ReviewSummary"];

const VERSION = "ds_b0c3e5a8d217";
const EDGES = [0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5];
const HISTOGRAM = [0, 0, 2, 7, 80, 310, 400, 150, 40, 5]; // 994 rows, 9 below 0.20

const labelled = {
  count: 994, mean: 0.284, std: 0.04, minimum: 0.12, maximum: 0.47, quantiles: { p50: 0.281 },
  edges: EDGES, histogram: HISTOGRAM, below: 0, above: 0,
};

const reviewed: Review = {
  review_id: "rev_0007", status: "closed", sample_size: 50, seed: 1142, decided: 50,
  acceptance: {
    sampled: 50, undecided: 0,
    overall: { decided: 50, accepted: 45, interval: { estimate: 0.9, low: 0.786, high: 0.957, confidence: 0.95 } },
    by_split: { train: { decided: 40, accepted: 36, interval: { estimate: 0.9, low: 0.77, high: 0.96, confidence: 0.95 } } },
  },
};

function report(review: Review | null = reviewed, id = 5): Report {
  return {
    id, version_id: VERSION, kind: "phase1_dataset", review_session_id: review?.review_id ?? null, job_id: "job_0012",
    created_at: "2026-11-20T19:12:50Z",
    report: {
      kind: "phase1_dataset", version_id: VERSION, stage: "split", algorithm_version: 1, confidence: 0.95,
      rows: { total: 1000, accepted: 985, by_split: { train: 788, val: 98, test: 99 } },
      rejections: { total: 15, accepted: 985, by_stage: { ingest: { below_min_size: 6 }, filter: { clip_below_threshold: 9 } } },
      filter_rate: 9 / 994,
      clip_scores: { edges: EDGES, labelled, accepted: labelled, by_split: null },
      review,
    },
  };
}

function summary(id: string, closedAt: string): Summary {
  return {
    id, version_id: VERSION, sample_size: 50, seed: 1, status: "closed", created_at: closedAt, closed_at: closedAt,
    progress: { sampled: 50, decided: 50, accept: 45, reject: 5, undecided: 0, by_split: {} },
  };
}

const version = {
  version_id: VERSION, dataset: "coco1k", stage: "split", parent_version_id: "ds_filter000001", content_hash: "a".repeat(64),
  config_hash: "b".repeat(64), config: {}, dtype: "image_caption", row_count: 985, counts: { accepted: 985 },
  manifest_path: "m", manifest_bytes: 1, new_blob_bytes: 0, schema_version: 1, code_version: null,
  created_by_job_id: "job_0011", created_by_job_state: "completed", created_at: "2026-11-20T19:00:00Z",
};
const filter = { ...version, version_id: "ds_filter000001", stage: "filter", config: { clip_threshold: 0.2 } };

function routes(latest: unknown, reviews: Summary[] = [summary("rev_0007", "2026-11-20T19:00:00Z")], extra: Record<string, unknown> = {}) {
  return stubApi({
    ...extra,
    [`/api/v1/versions/${VERSION}/reports/latest`]: latest,
    [`/api/v1/versions/${VERSION}/reviews`]: { items: reviews, next_cursor: null },
    [`/api/v1/versions/${VERSION}/lineage`]: { version_id: VERSION, ancestors: [filter], descendants: [], in_progress: [] },
    [`/api/v1/versions/${VERSION}`]: version,
  });
}

function renderReport(path = `/versions/${VERSION}/report`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/versions/:versionId/report" element={<ReportScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function job(state: Job["state"]): Job {
  return {
    id: "job_0021", kind: "evaluate", state, desired_state: null, attempt: 1, dataset: "coco1k", current_stage: "evaluate",
    output_version_id: null, error: null, summary: null, created_at: "2026-11-20T19:20:00Z", started_at: null, finished_at: null,
    stages: [{
      idx: 0, name: "evaluate", queue: "cpu", status: state === "completed" ? "completed" : "running", config: {}, input_version_id: VERSION,
      output_version_id: null, cache_hit: false, step: 2, total: 5, throughput: null, eta_seconds: null, activity: "Scoring the review",
      metrics: null, peaks: null, started_at: null, finished_at: null, error: null,
    }],
  };
}

beforeEach(() => {
  FakeSource.install();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-06", () => {
  test("test_EVAL_1_1_headlines_funnel_and_acceptance_interval", async () => {
    routes(report());
    renderReport();
    expect((await screen.findByText("Rows kept")).parentElement).toHaveTextContent("Rows kept985of 1,000 raw · 98.5% · after 6 stages");
    expect(screen.getByText("0.91")).toBeInTheDocument(); // filter rate, 9 of 994
    expect(await screen.findByText(/9 of 994 captioned rows/)).toHaveTextContent("below CLIP 0.20");
    expect(screen.getByText("90.0")).toBeInTheDocument();
    expect(screen.getByText(/45 of 50 · 95% Wilson interval/)).toHaveTextContent("78.6% – 95.7%");
    expect(screen.getByLabelText("Acceptance per split")).toHaveTextContent("train 36/40 · 77%–96%");

    const funnel = screen.getByRole("img", { name: /^ingest:/ });
    expect(funnel).toHaveAccessibleName(expect.stringContaining("ingest: 994 kept, 6 removed") as string);
    expect(funnel).toHaveAccessibleName(expect.stringContaining("filter: 985 kept, 9 removed") as string);
    const reasons = screen.getByRole("table", { name: "Rows removed per stage and reason" });
    expect(within(reasons).getByText("below_min_size").closest("tr")).toHaveTextContent("ingest");
    expect(within(reasons).getByText("clip_below_threshold").closest("tr")).toHaveTextContent("0.90%");

    expect(screen.getByRole("img", { name: /Histogram of CLIP scores/ })).toHaveAccessibleName(
      "Histogram of CLIP scores of 994 rows; 9 below the threshold 0.2; mean 0.284",
    );
    expect(screen.getByRole("link", { name: /Open rev_0007/ })).toHaveAttribute("href", "/reviews/rev_0007");
  });

  test("test_EVAL_1_1_the_clip_chart_has_a_table_with_the_same_numbers", async () => {
    routes(report());
    renderReport();
    const group = await screen.findByRole("group", { name: "CLIP score distribution: chart or table" });
    fireEvent.click(within(group).getByRole("button", { name: /Table/ }));
    const bins = screen.getByRole("table", { name: "CLIP score bins" });
    const rows = within(bins).getAllByRole("row").slice(1); // empty bins are left out
    expect(rows).toHaveLength(8);
    expect(rows[0]).toHaveTextContent(/0\.1000\.1502\s*0\.20%yes/);
    expect(rows[2]).toHaveTextContent(/0\.2000\.25080.*no/);
    expect(within(group).getByRole("button", { name: /Table/ })).toHaveAttribute("aria-pressed", "true");
  });

  test("test_EVAL_1_1_without_a_review_acceptance_is_not_measured", async () => {
    routes(report(null), []);
    renderReport();
    expect(await screen.findByText("— not measured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start a review/ })).toBeInTheDocument();
    expect(screen.getByText(/· no review/)).toBeInTheDocument();
    expect(screen.queryByText(/Wilson interval/)).toBeNull();
  });

  test("test_EVAL_1_1_a_newer_closed_review_offers_a_rebuild_with_it", async () => {
    const calls = routes(report(), [summary("rev_0009", "2026-11-21T09:00:00Z"), summary("rev_0007", "2026-11-20T19:00:00Z")], {
      [`/api/v1/versions/${VERSION}/reports`]: job("queued"),
      "/api/v1/jobs/job_0021": job("running"),
    });
    renderReport();
    const callout = await screen.findByText("A newer review exists.");
    fireEvent.click(within(callout.closest(".callout") as HTMLElement).getByRole("button", { name: /Rebuild with rev_0009/ }));
    await vi.waitFor(() => {
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({ kind: "phase1_dataset", review_id: "rev_0009" });
    });
    expect(await screen.findByText("Building")).toBeInTheDocument();
  });

  test("test_EVAL_1_1_no_report_yet_builds_one_and_follows_the_job", async () => {
    let latest = (): unknown =>
      Response.json(
        { code: "not_found", title: "Not found", status: 404, detail: `Version ${VERSION} has no phase1_dataset report yet.`, request_id: "r", errors: [] },
        { status: 404 },
      );
    const calls = routes(() => latest(), [], {
      [`/api/v1/versions/${VERSION}/reports`]: job("queued"),
      "/api/v1/jobs/job_0021": job("running"),
    });
    renderReport();
    expect(await screen.findByText(`No report for ${VERSION} yet`)).toBeInTheDocument();
    expect(screen.getByText(/No review of this version is closed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Build report/ }));

    expect(await screen.findByText("Scoring the review")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Building");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ kind: "phase1_dataset" });

    latest = () => report(null, 10);
    act(() => {
      globalFeed.dispatch("job", job("completed"));
    });
    expect(await screen.findByText(/#10 · built/)).toBeInTheDocument();
  });
});
