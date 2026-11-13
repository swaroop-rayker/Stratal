# ADR-0011 — Error model

**Status:** Accepted, 2026-11-13 (SPEC CL-10).

## Context

- The input's engineering standards leave the error-handling pattern as an unfilled placeholder: "typed errors → consistent JSON error shape".
- MON-1.3: warnings and errors include a reason and a suggested action.
- RUN-1.5: an out-of-memory failure must carry a clear reason and a suggested smaller batch size.
- Errors surface in three places: HTTP responses, failed jobs, and events. They should mean the same thing in all three.

## Decision

1. **One exception hierarchy.** Every expected failure is a subclass of `StratalError` with a stable `code`, a title, an HTTP status, and optionally a `suggested_action`.
2. **One HTTP shape.** A single FastAPI handler turns any `StratalError` into RFC 9457 problem details (`application/problem+json`) with the extra members `code`, `suggested_action`, `request_id` and `errors`. Request-validation failures are mapped into the same shape.
3. **The same codes for jobs.** A failed job stores `{code, reason, suggested_action, detail}`, and the ERROR event that announces it carries the same code.
4. **Unexpected exceptions** become `internal_error` with the request ID. The stack trace goes to the structured log, never to the client.
5. **The code catalogue** is listed in [API.md §2](../API.md#2-errors) and grows only by adding codes.

## Alternatives

| Option | Why not |
|---|---|
| FastAPI's default `{"detail": …}` | Validation errors and application errors come out in different shapes |
| Error payloads with HTTP 200 | Hides failures from HTTP clients and tooling |
| Ad-hoc errors per endpoint | The frontend would need a special case for each |

## Consequences

- The frontend handles errors in one place and can show the suggested action directly.
- Codes are part of the public contract. Renaming one is a breaking change.
- Stage code raises typed errors instead of returning status values, which keeps the owner-implemented logic free of plumbing concerns.
