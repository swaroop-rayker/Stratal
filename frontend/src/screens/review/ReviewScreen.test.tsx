// LABEL-1.5 (SCR-05): decide from the keyboard, decisions saved as made, close with care.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { Schemas } from "../../api/client";
import { stubApi } from "../../test/api";
import { ReviewScreen } from "./ReviewScreen";

type Review = Schemas["Review"];
type Item = Schemas["ReviewItem"];

function item(position: number, extra: Partial<Item> = {}): Item {
  return {
    position, row_key: `train/00000000${String(position)}.jpg`, split: "train", decision: null, note: null, decided_at: null,
    row: { sha256: "a".repeat(64), derived_sha256: "b".repeat(64), caption: `Caption ${String(position)}`, clip_score: 0.284,
      split_stratum: "q3|landscape|.", caption_model: "florence-2-base", caption_model_revision: "5ca5edf0", width: 640,
      height: 427, size_bytes: 192_000, derived_width: 640, derived_height: 427 },
    ...extra,
  };
}

function review(items: Item[], status: "open" | "closed" = "open"): Review {
  const accept = items.filter((i) => i.decision === "accept").length;
  const reject = items.filter((i) => i.decision === "reject").length;
  return {
    id: "rev_0007", version_id: "ds_b0c3e5a8d217", sample_size: items.length, seed: 1142, status,
    created_at: "2026-11-20T15:20:00Z", closed_at: status === "closed" ? "2026-11-20T15:41:00Z" : null,
    progress: { sampled: items.length, decided: accept + reject, accept, reject, undecided: items.length - accept - reject, by_split: {} },
    items,
  };
}

const version = {
  version_id: "ds_b0c3e5a8d217", dataset: "coco10k", stage: "split", parent_version_id: "ds_filter000001", content_hash: "a".repeat(64),
  config_hash: "b".repeat(64), config: {}, dtype: "image_caption", row_count: 10000, counts: { accepted: 9431 },
  manifest_path: "m", manifest_bytes: 1, new_blob_bytes: 0, schema_version: 1, code_version: null,
  created_by_job_id: "job_0142", created_by_job_state: "completed", created_at: "2026-11-20T15:00:00Z",
};
const filter = { ...version, version_id: "ds_filter000001", stage: "filter", config: { clip_threshold: 0.2 } };

function routes(r: Review, put?: unknown) {
  return stubApi({
    "/api/v1/reviews/rev_0007": r,
    "/api/v1/reviews/rev_0007/items/*": put ?? ((url: URL) => ({ ...item(Number(url.pathname.split("/").pop())), decision: "accept", row: undefined })),
    "/api/v1/reviews/rev_0007/close": { ...r, status: "closed", acceptance: null, acceptance_unavailable: null },
    "/api/v1/versions/ds_b0c3e5a8d217": version,
    "/api/v1/versions/ds_b0c3e5a8d217/lineage": { version_id: version.version_id, ancestors: [filter], descendants: [], in_progress: [] },
  });
}

function renderReview(path = "/reviews/rev_0007") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/reviews/:reviewId" element={<ReviewScreen />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SCR-05", () => {
  test("test_LABEL_1_5_a_key_saves_the_decision_and_moves_to_the_next_item", async () => {
    const calls = routes(review([item(1), item(2), item(3)]));
    renderReview();
    expect(await screen.findByText("Caption 1")).toBeInTheDocument();
    expect(await screen.findByLabelText(/CLIP score 0.284, threshold 0.2/)).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(window, { key: "a" });
    });
    expect(await screen.findByText("Caption 2")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url.pathname).toBe("/api/v1/reviews/rev_0007/items/1");
    expect(put?.body).toEqual({ decision: "accept", note: null });
    expect(await screen.findByText("1 accepted")).toHaveClass("k-acc"); // the progress line
    const strip = screen.getByRole("listbox", { name: "Sampled items" });
    expect(within(strip).getByRole("option", { name: "Item 1, accepted" })).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(window, { key: "k" });
    });
    expect(await screen.findByText("Caption 1")).toBeInTheDocument();
  });

  test("test_LABEL_1_5_a_note_goes_with_the_decision", async () => {
    const calls = routes(review([item(1), item(2)]));
    renderReview();
    const note = await screen.findByLabelText(/Note, optional/);
    fireEvent.change(note, { target: { value: "it is dusk, not sunset" } });
    fireEvent.click(screen.getByRole("button", { name: /Reject/ }));
    await vi.waitFor(() => {
      expect(calls.some((c) => c.method === "PUT")).toBe(true);
    });
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ decision: "reject", note: "it is dusk, not sunset" });
  });

  test("test_LABEL_1_5_a_failed_save_says_so_and_offers_retry", async () => {
    routes(
      review([item(1), item(2)]),
      Response.json({ code: "dependency_unavailable", title: "x", detail: "Postgres is unreachable.", suggested_action: null, request_id: "r", errors: [] }, { status: 503 }),
    );
    renderReview();
    await screen.findByText("Caption 1");
    act(() => {
      fireEvent.keyDown(window, { key: "a" });
    });
    const alert = await screen.findByText("Decision for item 1 not saved");
    expect(alert.closest(".toast")).toHaveTextContent("dependency_unavailable");
    expect(within(alert.closest(".toast") as HTMLElement).getByRole("button", { name: /Retry/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Item 1, accepted, not saved" })).toBeInTheDocument();
  });

  test("test_LABEL_1_5_closing_with_undecided_items_asks_first", async () => {
    routes(review([item(1, { decision: "accept" }), item(2), item(3)]));
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Close session" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Close rev_0007 with 2 undecided?");
    expect(dialog).toHaveTextContent("Only the 1 decided items count");
    expect(within(dialog).getByRole("button", { name: "Close with 1 decided" })).toBeInTheDocument();
    expect(document.activeElement).toHaveTextContent("Keep reviewing"); // the safe choice first
  });

  test("test_LABEL_1_5_a_closed_review_shows_its_summary_and_rejected_items", async () => {
    routes(review([item(1, { decision: "accept" }), item(2, { decision: "reject", note: "it is a donkey" }), item(3, { decision: "accept" })], "closed"));
    renderReview();
    expect(await screen.findByText("Closed")).toBeInTheDocument();
    expect(screen.getByText("of 3 reviewed · 66.7%")).toBeInTheDocument();
    expect(screen.getByText("1 with a note")).toBeInTheDocument();
    expect(screen.getByText("it is a donkey")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build the eval report/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept/ })).toBeNull(); // read-only
  });
});
