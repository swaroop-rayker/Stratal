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
    job.stages.find((s) => s.status === "paused") ??
    job.stages.find((s) => s.status === "queued") ??
    null
  );
}

/** What a queued job waits for: its worker, and the job holding the GPU if one does. */
export function waitingFor(job: Job, active: Job[]): string {
  const stage = runningStage(job) ?? job.stages[0];
  const queue = stage?.queue ?? "cpu";
  const holder = queue === "gpu" ? active.find((j) => j.id !== job.id && runningStage(j)?.queue === "gpu" && j.state === "running") : undefined;
  const hs = holder ? runningStage(holder) : null;
  return `Waiting for the ${queue} worker${holder ? ` · ${holder.id} holds the GPU (${hs?.name ?? "–"})` : ""}`;
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

/** A run that trains a network: a `train` job, or an HPO study's trial (M10). */
export function isTraining(job: Pick<Job, "kind">): boolean {
  return job.kind === "train" || job.kind === "hpo_trial";
}

/** The model a training job trains (`kind: train`), or null for other jobs. */
export function trainingModel(job: Job): string | null {
  if (!isTraining(job)) return null;
  const model = (job.stages[0]?.config as { model?: { name?: unknown } } | undefined)?.model;
  return typeof model?.name === "string" ? model.name : null;
}

export interface TrainingPosition {
  epoch: number | null;
  epochs: number | null;
  /** The step within the epoch, from the global step and the steps per epoch. */
  step: number | null;
}

/** Where a training stage stands: epoch of epochs, and the step within the epoch. */
export function trainingPosition(stage: JobStage | null): TrainingPosition {
  const metrics = stage?.metrics ?? {};
  const epoch = num(metrics.epoch);
  const epochs = num((stage?.config as { epochs?: unknown } | undefined)?.epochs);
  const perEpoch = stage?.total && epochs ? stage.total / epochs : null;
  const step = stage?.step && epoch && perEpoch ? Math.round(stage.step - (epoch - 1) * perEpoch) : null;
  return { epoch, epochs, step };
}

/** A training run's final validation accuracy, when it has one. */
export function valAccuracy(job: Job): number | null {
  return num(job.stages[0]?.metrics?.val_accuracy);
}

/** The batch a training run that ran out of GPU memory suggests (RUN-1.5), read from its
 * error ("reduce batch size from 128 → 64"); ``null`` for anything else. */
export function suggestedBatch(job: Job): number | null {
  if (!isTraining(job)) return null;
  const error = job.error ?? failedStage(job)?.error ?? null;
  if (error?.code !== "gpu_oom") return null;
  const found = /reduce batch size from \d+ → (\d+)/.exec(error.reason ?? "");
  return found ? Number(found[1]) : null;
}
