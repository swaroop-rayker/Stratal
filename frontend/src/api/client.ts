// Typed API client generated from the backend's OpenAPI document (SPEC §7.3).
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// fetch is looked up per call (not captured once), so tests can stub it.
export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  fetch: (request: Request) => globalThis.fetch(request),
});

export type Schemas = components["schemas"];
export type Job = Schemas["Job"];
export type JobStage = Schemas["JobStage"];
export type JobState = Job["state"];
export type StreamEvent = Schemas["Event"];
export type StreamSample = Schemas["StreamSample"];
export type System = Schemas["System"];
export type ResourceSample = Schemas["ResourceSample"];

/** An RFC 9457 problem from the API (ADR-0011), or a network failure dressed as one. */
export interface Problem {
  status: number;
  code: string;
  title: string;
  detail: string;
  suggested_action: string | null;
  request_id: string | null;
  errors: { loc: (string | number)[]; msg: string; code: string }[];
}

export class ApiError extends Error {
  readonly problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail);
    this.problem = problem;
  }
}

function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "code" in value && "detail" in value;
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** A fetch that never reached the API (it is down or restarting). */
export function unreachable(): ApiError {
  return new ApiError({
    status: 0,
    code: "dependency_unavailable",
    title: "API unreachable",
    detail: "The Stratal API did not answer.",
    suggested_action: "Check that the api container is running (docker compose ps), then retry.",
    request_id: null,
    errors: [],
  });
}

/** Run an openapi-fetch request; return its data or throw an ApiError with the problem. */
export async function call<T>(request: () => Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request();
  } catch {
    throw unreachable();
  }
  const { response } = result;
  if (result.error !== undefined || !response.ok) {
    if (isProblem(result.error)) throw new ApiError({ ...result.error, status: response.status });
    // The dev server's proxy answers 502–504 without a problem body when the API is down.
    if (response.status >= 502 && response.status <= 504) throw unreachable();
    throw new ApiError({
      status: response.status,
      code: response.status >= 500 ? "dependency_unavailable" : "http_error",
      title: "HTTP error",
      detail: `The API answered ${response.status} ${response.statusText}.`,
      suggested_action: null,
      request_id: response.headers.get("X-Request-ID"),
      errors: [],
    });
  }
  return result.data as T;
}
