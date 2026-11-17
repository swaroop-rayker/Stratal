// Job helpers for SCR-04 and SCR-07: durations, the stage running now, live progress.
import type { Job, JobStage, StreamEvent } from "../../api/client";

export const ACTIVE = new Set(["queued", "running", "stalled"]);
export const FINISHED = new Set(["completed", "failed", "cancelled"]);

/** Seconds a stage has run: to its end, or to now while it runs; null before it starts. */
export function stageSeconds(stage: JobStage, now: number): number | null {
  if (!stage.started_at) return null;
  const end = stage.finished_at ? new Date(stage.finished_at).getTime() : now;
  return Math.max(0, (end - new Date(stage.started_at).getTime()) / 1000);
}

export function jobSeconds(job: Job, now: number): number | null {
  const start = job.started_at ?? null;
  if (!start) return null;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  return Math.max(0, (end - new Date(start).getTime()) / 1000);
}

export function runningStage(job: Job): JobStage | null {
  return (
    job.stages.find((s) => s.name === job.current_stage) ??
    job.stages.find((s) => s.status === "running") ??
    job.stages.find((s) => s.status === "queued") ??
    null
  );
}

export function failedStage(job: Job): JobStage | null {
  return job.stages.find((s) => s.status === "failed") ?? null;
}

/** The input version: the job's own, or the first stage's. */
export function inputVersion(job: Job): string | null {
  return job.stages[0]?.input_version_id ?? null;
}

export interface Live {
  step: number | null;
  total: number | null;
  rate: number | null;
  eta: number | null;
  activity: string | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Progress for a stage from its row, updated by the newest event of that stage. Progress
 * events carry `items_per_s` and `eta_s` in their metrics (MON-1.5). */
export function liveProgress(stage: JobStage | null, latest: StreamEvent | null): Live {
  const base: Live = {
    step: stage?.step ?? null,
    total: stage?.total ?? null,
    rate: stage?.throughput ?? null,
    eta: stage?.eta_seconds ?? null,
    activity: stage?.activity ?? null,
  };
  if (!stage || !latest || latest.stage !== stage.name) return base;
  const metrics = latest.metrics ?? {};
  return {
    step: latest.step ?? base.step,
    total: latest.total ?? base.total,
    rate: num(metrics.items_per_s) ?? base.rate,
    eta: num(metrics.eta_s) ?? base.eta,
    activity: latest.message,
  };
}
