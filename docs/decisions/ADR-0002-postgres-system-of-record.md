# ADR-0002 — PostgreSQL is the system of record

**Status:** Accepted, 2026-11-13 (SPEC CL-02).

## Context

The input names two different stores for the same data. MON-1.4, the REG stack line and the monitoring architecture note say SQLite/DuckDB. The STORE stack line and §7.7 say PostgreSQL.

Several processes write at the same time: the API, the CPU and GPU workers, the collector and the sampler. Configs and events are semi-structured. The schema will change across five phases.

## Decision

- **PostgreSQL** holds all transactional state: jobs, stages, transitions, events, telemetry, dataset versions and lineage, the derivation cache, reviews, reports, and later the model registry.
- **Parquet** holds dataset manifests and the downsampled telemetry archive. **DuckDB** queries them.
- **SQLite is not used.**
- Access is through **SQLAlchemy 2.x** with typed mappings, and **Alembic** for migrations. The input offers "SQLAlchemy 2.x / SQLModel"; SQLModel is not added, because typed SQLAlchemy mappings work cleanly under strict type checking and API schemas are separate Pydantic models anyway.
- Optuna (phase 2) stores its studies in the same server, in its own schema.

## Alternatives

| Option | Why not |
|---|---|
| SQLite | One writer at a time. A sampler writing every second alongside workers and the API leads to lock contention |
| DuckDB as the main database | Built for analytics from a single process, not for concurrent writers |
| Events only in files | No live filtering or joining with jobs; the activity log needs queries |

## Consequences

- Concurrent writers and JSONB columns work without workarounds.
- One more service to run, costing a few hundred MB of the memory budget.
- Integration tests run against a real Postgres, as the engineering standards require.
- Backups are a database dump plus the data volume. A backup command is not in scope for phase 1.
