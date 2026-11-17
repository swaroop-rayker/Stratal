// Test helper: answer the API's fetches by path from fixtures, without a server.
import { vi } from "vitest";

// JSON to answer with, a Response, or a function of the request URL giving either.
type Answer = unknown;

/** Stub fetch: the first route whose path matches (exactly, or as a prefix ending in "*")
 * answers; a Response is returned as is, anything else as JSON 200. Unknown paths are 404. */
export function stubApi(routes: Record<string, Answer>) {
  const calls: { method: string; url: URL; body: unknown }[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const body: unknown = request.method === "GET" ? undefined : await request.clone().json().catch(() => undefined);
    calls.push({ method: request.method, url, body });
    const key = Object.keys(routes).find((path) =>
      path.endsWith("*") ? url.pathname.startsWith(path.slice(0, -1)) : url.pathname === path,
    );
    if (key === undefined) {
      return Response.json({ code: "not_found", detail: `no fixture for ${url.pathname}` }, { status: 404 });
    }
    const answer = routes[key];
    const value = typeof answer === "function" ? (answer as (u: URL) => unknown)(url) : answer;
    return value instanceof Response ? value : Response.json(value);
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

export const emptyList = { items: [], next_cursor: null, counts: {} };
