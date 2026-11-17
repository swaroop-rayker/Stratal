// A stand-in for the browser's EventSource, so tests can push SSE messages (API.md §4).
import { vi } from "vitest";

export class FakeSource {
  static CLOSED = 2;
  static made: FakeSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private readonly handlers = new Map<string, (m: MessageEvent<string>) => void>();

  constructor(readonly url: string) {
    FakeSource.made.push(this);
  }

  addEventListener(kind: string, handler: (m: MessageEvent<string>) => void) {
    this.handlers.set(kind, handler);
  }

  close() {
    this.readyState = FakeSource.CLOSED;
  }

  /** Deliver one SSE message of this name. */
  emit(kind: string, data: unknown, lastEventId = "") {
    this.handlers.get(kind)?.({ data: JSON.stringify(data), lastEventId } as MessageEvent<string>);
  }

  static install() {
    FakeSource.made = [];
    vi.stubGlobal("EventSource", FakeSource);
  }

  /** The newest source opened on a URL starting with `prefix`. */
  static last(prefix: string): FakeSource | undefined {
    return [...FakeSource.made].reverse().find((s) => s.url.startsWith(prefix));
  }
}
