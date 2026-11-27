// Chat on SCR-12 (LLM-1.1, LLM-2.1, LLM-2.3, LLM-2.4; ADR-0021 rule 8). The answer arrives as
// server-sent events over a POST, so it is read with fetch and a stream reader, not EventSource
// (which only GETs). Stopping aborts the fetch; the API stores the partial answer as "stopped".
import { useQuery } from "@tanstack/react-query";

import { ApiError, api, call, unreachable, type Problem, type Schemas } from "./client";

export type ChatMode = "llm" | "rag" | "images";
export type SessionSummary = Schemas["SessionSummary"];
export type SessionOut = Schemas["SessionOut"];
export type MessageOut = Schemas["MessageOut"];
export type RequestTimings = Schemas["RequestTimings"];
export type ChatSources = Schemas["ChatSources"];

export interface Source {
  n: number;
  row_key: string;
  document: string;
  section: string;
  char_start: number | null;
  char_end: number | null;
  relevance: number;
  snippet: string;
  cited?: boolean;
}

export interface ImageHit {
  row_key: string;
  sha256: string;
  derived_sha256: string;
  similarity: number;
}

/** What one answer carries besides its text, from its events (live) or its stored message. */
export interface AnswerDetails {
  citations?: Source[];
  sources?: Source[];
  removed?: number[];
  uncited?: boolean;
  abstained?: boolean;
  nearest?: Source | null;
  threshold?: number | null;
  images?: ImageHit[];
  searched?: number;
  version_id?: string;
  stopped?: boolean;
  error?: Problem;
}

export interface ChatRequest {
  session_id?: string;
  mode: ChatMode;
  message: string;
  index_version_id?: string;
  labelled_version_id?: string;
}

export type ChatEvent =
  | { name: "session"; data: { session_id: string; message_id: number } }
  | { name: "meta"; data: Partial<RequestTimings> & { display?: string } }
  | { name: "token"; data: { text: string } }
  | { name: "citations"; data: AnswerDetails }
  | { name: "images"; data: AnswerDetails }
  | { name: "done"; data: Partial<RequestTimings> }
  | { name: "error"; data: Problem };

export const chatKeys = {
  sessions: ["chat", "sessions"] as const,
  session: (id: string) => ["chat", "sessions", id] as const,
  sources: ["chat", "sources"] as const,
};

export function useChatSessions() {
  return useQuery({
    queryKey: chatKeys.sessions,
    queryFn: () => call(() => api.GET("/api/v1/chat/sessions", { params: { query: { limit: 100 } } })),
  });
}

/** ``poll``: refetch every second, while an answer that was just stopped is being stored. */
export function useChatSession(id: string | undefined, poll = false) {
  return useQuery({
    queryKey: chatKeys.session(id ?? ""),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/chat/sessions/{session_id}", { params: { path: { session_id: id ?? "" } } }),
      ),
    enabled: id !== undefined,
    refetchInterval: poll ? 1000 : false,
  });
}

export function useChatSources() {
  return useQuery({
    queryKey: chatKeys.sources,
    queryFn: () => call(() => api.GET("/api/v1/chat/sources")),
  });
}

export async function deleteSession(id: string): Promise<void> {
  await call(() =>
    api.DELETE("/api/v1/chat/sessions/{session_id}", { params: { path: { session_id: id } } }),
  );
}

/** Parse SSE text into events; returns the events and the unfinished remainder. */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const events: ChatEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let name = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7);
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (name && data) events.push({ name, data: JSON.parse(data) as unknown } as ChatEvent);
  }
  return { events, rest };
}

/** Send a message and call ``onEvent`` for each event until the answer ends or is aborted. */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await globalThis.fetch(`${globalThis.location.origin}/api/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    if (signal.aborted) return;
    throw unreachable();
  }
  if (!response.ok) {
    let problem: Problem;
    try {
      problem = { ...((await response.json()) as Problem), status: response.status };
    } catch {
      throw unreachable();
    }
    throw new ApiError(problem);
  }
  const reader = response.body?.getReader();
  if (reader === undefined) return;
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSse(buffer);
      buffer = parsed.rest;
      parsed.events.forEach(onEvent);
    }
  } catch (error) {
    if (!signal.aborted) throw error;
  }
}
