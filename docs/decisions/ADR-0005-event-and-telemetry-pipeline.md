# ADR-0005 — Event and telemetry pipeline

**Status:** Accepted by the owner on 2026-11-14, as proposed, before M2 started. The separate `monitor` service and the split of telemetry sources had been approved at kickoff (SPEC CL-07, CL-11).

## Context

- MON-1.4: events are stored durably, reach the UI within 1 second, and survive a restart.
- NFR-13: a monitoring crash must not crash jobs, and the reverse.
- NFR-11: monitoring uses under 2% CPU and adds under 1% to job runtime.
- The input's diagram has jobs sending events to a collector, which writes to the database and feeds the live stream.
- PyTorch allocator figures can only be read inside the process that owns them.

## Decision

1. **Job to collector: a Redis Stream.** The job's emitter appends events from a background thread with a bounded queue. Emitting never blocks the job and never raises into it. A stream keeps events until they are acknowledged, so a collector restart loses nothing.
2. **Local spool.** Every event is also written to a file in the job's folder. If Redis is down the job continues, and the file is the source for a later backfill.
3. **Collector.** Reads the stream in batches, inserts into Postgres (idempotent on a per-event unique ID), acknowledges, publishes for live viewers, and keeps each stage's progress row current.
4. **To the browser: server-sent events.** A reconnecting client sends the last event ID and the API replays the gap from Postgres.
5. **Two telemetry sources.** A sampler in `monitor` reads psutil and NVML and writes samples directly. A reporter thread in each job child reports that process's RSS and PyTorch allocator figures.
6. **One `monitor` service** runs the collector, the sampler and the watchdog.

## Points settled by the owner (2026-11-14)

- **SSE for job streams (OC-2): accepted.** SPEC §7.3 lists WebSockets for job streams, and MON-1.4 allows either. SSE was chosen because the feed is one-way and resumes by itself after a disconnect.
- **One process inside `monitor`: accepted.** Collector, sampler and watchdog share one process, for memory. Three processes would isolate failures better; revisit if NFR-13 testing shows one part taking the others down.
- **Related, OC-1: accepted.** MON-3 "Staging" is the commit step at the end of every stage, so its events are emitted by each stage's commit, not by a separate stage.

## Alternatives

| Option | Why not |
|---|---|
| Redis pub/sub for job-to-collector | Messages published while the collector is down are lost |
| Jobs insert events straight into Postgres | Durable and simple, but ties every job to database latency and removes the collector the input describes |
| Postgres LISTEN/NOTIFY for live fan-out | Workable, but Redis is already present for this purpose (§7.7) |
| WebSocket for everything | The client never needs to send; reconnection and replay would have to be hand-built |

## Consequences

- Events survive a crash of any single component.
- There are two copies of each event for a short time (stream and Postgres) and a third in the spool file.
- Ordering in the UI follows insertion order in Postgres, which can differ slightly from emit order across processes. Events carry their own timestamps.
- The NFR-11 overhead benchmark in M7 decides whether the sampling and emit costs are acceptable.

## As built in M2 (2026-11-14)

- Stream entries have a `kind` (`event` or `process_sample`). The job's reporter thread sends its memory samples on the same stream, so the job writes nothing to PostgreSQL for monitoring.
- Job state changes are written to `events` directly by whoever makes the transition, in the same transaction, not through the stream. The state and its event cannot disagree.
- Pub/sub messages are notifications, not payloads. The SSE endpoint reads events from PostgreSQL when notified, so replay and live delivery are the same query. Samples are the exception: they are forwarded as published.
- An entry that can never be valid (bad JSON, a WARN without a reason) is logged, acknowledged and dropped, so it cannot block the entries behind it.
- Measured on the real stack: the monitor killed mid-job, the job completed, and the events that had waited in the stream were stored when the monitor came back (4 stored before, 14 after).

## As built in M7 (2026-11-17)

- **Backfill (decision 2).** After every stage the supervisor reads the job's spool (`jobs/<job_id>/events.jsonl`) and inserts the events PostgreSQL lacks, on the same `event_uid` key the collector uses. A job whose events never reached Redis therefore has them all when the stage ends, and the collector's late copies are stored once. A worker that died is covered by the next attempt's backfill, since the spool is per job. A job failed by the watchdog after its last worker died is backfilled by hand: `python -m stratal.cli events backfill --job ID` (or `--all`). `monitor` does not mount the data volume, so it does not backfill.
- **Telemetry retention (NFR-12).** The `beat` service averages raw samples older than 7 days into `resource_samples_1m` and `process_samples_1m` hourly (DATA_MODEL.md §4.8a).
