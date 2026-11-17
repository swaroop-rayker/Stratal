// MON-1.4: the live feed survives an outage and resumes from its last event.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { FakeSource } from "../test/eventsource";
import { Feed } from "./stream";

beforeEach(() => {
  FakeSource.install();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Feed", () => {
  test("test_MON_1_4_messages_reach_their_listeners_by_name", () => {
    const feed = new Feed("/api/v1/stream");
    const jobs: unknown[] = [];
    feed.on("job", (job) => jobs.push(job.id));
    const release = feed.acquire();
    FakeSource.made[0]?.emit("job", { id: "job_0001" });
    FakeSource.made[0]?.emit("event", { id: 7 }, "7");
    expect(jobs).toEqual(["job_0001"]);
    release();
  });

  test("test_MON_1_4_reopens_after_an_http_error_from_the_last_event", () => {
    const feed = new Feed("/api/v1/stream");
    const release = feed.acquire();
    const first = FakeSource.made[0] as FakeSource;
    first.onopen?.();
    expect(feed.connection).toBe("live");
    first.emit("event", { id: 41 }, "41");

    first.readyState = FakeSource.CLOSED; // the proxy answered 502: the browser gives up
    first.onerror?.();
    expect(feed.connection).toBe("reconnecting");
    expect(FakeSource.made).toHaveLength(1);

    vi.advanceTimersByTime(1000);
    expect(FakeSource.made).toHaveLength(2);
    expect(FakeSource.made[1]?.url).toBe("/api/v1/stream?after=41");
    FakeSource.made[1]?.onopen?.();
    expect(feed.connection).toBe("live");
    release();
  });

  test("test_MON_1_4_a_dropped_connection_is_left_to_the_browser", () => {
    const feed = new Feed("/api/v1/stream");
    const release = feed.acquire();
    const first = FakeSource.made[0] as FakeSource;
    first.onerror?.(); // still CONNECTING: the browser retries with Last-Event-ID itself
    vi.advanceTimersByTime(20_000);
    expect(FakeSource.made).toHaveLength(1);
    expect(feed.connection).toBe("reconnecting");
    release();
  });
});
