// The global live feed (API.md §4, MON-1.4): one EventSource shared by the whole app.
// EventSource reconnects by itself and sends Last-Event-ID, so the server replays the gap.
import { useEffect, useSyncExternalStore } from "react";

import type { Job, StreamEvent, StreamSample } from "./client";

export type Connection = "connecting" | "live" | "reconnecting";

interface Messages {
  job: Job;
  event: StreamEvent;
  sample: StreamSample;
}
type Kind = keyof Messages;
type Listener<K extends Kind> = (value: Messages[K]) => void;

export class Feed {
  private source: EventSource | null = null;
  private readonly connectionListeners = new Set<() => void>();
  private readonly listeners: { [K in Kind]: Set<Listener<K>> } = {
    job: new Set(),
    event: new Set(),
    sample: new Set(),
  };
  private users = 0;
  private lastEventId: string | null = null;
  private retryTimer: number | undefined;
  private retryDelay = 1000;
  connection: Connection = "connecting";

  constructor(private readonly url: string) {}

  private setConnection(next: Connection): void {
    if (this.connection === next) return;
    this.connection = next;
    this.connectionListeners.forEach((listener) => {
      listener();
    });
  }

  /** Open the stream for one more user; the returned function releases it. */
  acquire(): () => void {
    this.users += 1;
    if (this.source === null) this.open();
    return () => {
      this.users -= 1;
      if (this.users === 0) {
        window.clearTimeout(this.retryTimer);
        this.source?.close();
        this.source = null;
        this.setConnection("connecting");
      }
    };
  }

  private open(): void {
    // A new EventSource cannot send Last-Event-ID, so the cursor goes in the query (API.md §4).
    const url = this.lastEventId ? `${this.url}?after=${this.lastEventId}` : this.url;
    const source = new EventSource(url);
    this.source = source;
    source.onopen = () => {
      this.retryDelay = 1000;
      this.setConnection("live");
    };
    source.onerror = () => {
      this.setConnection("reconnecting");
      // The browser retries a dropped connection itself, but gives up for good on an HTTP
      // error (the dev proxy answers 502 while the API is down). Then reopen with backoff.
      if (source.readyState === EventSource.CLOSED && this.source === source) {
        this.source = null;
        this.retryTimer = window.setTimeout(() => {
          if (this.users > 0 && this.source === null) this.open();
        }, this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 2, 15_000);
      }
    };
    (Object.keys(this.listeners) as Kind[]).forEach((kind) => {
      source.addEventListener(kind, (message: MessageEvent<string>) => {
        if (message.lastEventId) this.lastEventId = message.lastEventId;
        this.dispatch(kind, JSON.parse(message.data) as Messages[typeof kind]);
      });
    });
  }

  dispatch<K extends Kind>(kind: K, value: Messages[K]): void {
    this.listeners[kind].forEach((listener) => {
      listener(value);
    });
  }

  on<K extends Kind>(kind: K, listener: Listener<K>): () => void {
    this.listeners[kind].add(listener);
    return () => {
      this.listeners[kind].delete(listener);
    };
  }

  subscribeConnection = (listener: () => void): (() => void) => {
    this.connectionListeners.add(listener);
    return () => {
      this.connectionListeners.delete(listener);
    };
  };

  getConnection = (): Connection => this.connection;
}

export const globalFeed = new Feed("/api/v1/stream");

/** Keep the global stream open while the calling component is mounted. */
export function useGlobalFeed(): Connection {
  useEffect(() => globalFeed.acquire(), []);
  return useSyncExternalStore(globalFeed.subscribeConnection, globalFeed.getConnection);
}

/** Call ``listener`` for each message of one kind on the global stream. */
export function useFeedMessage<K extends Kind>(kind: K, listener: Listener<K>): void {
  useEffect(() => globalFeed.on(kind, listener), [kind, listener]);
}
