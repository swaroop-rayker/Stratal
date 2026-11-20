// Queries for HPO studies (SCR-10; API.md §3.12). A study and its trials are read from our
// tables; while a study runs they are refetched every few seconds, and a `job` message
// for its job or one of its trials refreshes them at once.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api, call, type Job, type Schemas } from "./client";
import { useFeedMessage } from "./stream";

export type Study = Schemas["Study"];
export type Trial = Schemas["StudyTrial"];
export type Marker = { path: string; type: "int" | "float" | "log_float" | "categorical"; low?: number; high?: number; step?: number | null; choices?: unknown[] | null };

export const studyKeys = {
  all: ["studies"] as const,
  study: (id: string) => ["studies", id] as const,
  trials: (id: string) => ["studies", id, "trials"] as const,
};

const LIVE_MS = 3000;

/** True while the study can still change: a trial, a registration or its test to come. */
export function isLive(study: Study | undefined): boolean {
  return study?.state === "running" || study?.state === "closed";
}

export function useStudy(id: string) {
  return useQuery({
    queryKey: studyKeys.study(id),
    queryFn: () => call(() => api.GET("/api/v1/studies/{study_id}", { params: { path: { study_id: id } } })),
    refetchInterval: (query) => (isLive(query.state.data) ? LIVE_MS : false),
  });
}

export function useTrials(id: string, live: boolean) {
  return useQuery({
    queryKey: studyKeys.trials(id),
    queryFn: () => call(() => api.GET("/api/v1/studies/{study_id}/trials", { params: { path: { study_id: id } } })),
    refetchInterval: live ? LIVE_MS : false,
  });
}

/** Refresh a study when its job or one of its trials changes state. */
export function useStudyLive(study: Study | undefined): void {
  const client = useQueryClient();
  const onJob = useCallback(
    (job: Job) => {
      if (!study || (job.id !== study.job_id && job.kind !== "hpo_trial")) return;
      void client.invalidateQueries({ queryKey: studyKeys.study(study.id) });
    },
    [client, study],
  );
  useFeedMessage("job", onJob);
}

/** The last part of a marker's path: the field's name ("/config/learning_rate" → learning_rate);
 * a layer field keeps its node ("/config/model/graph/nodes/c1/layer/out_channels" → c1.out_channels). */
export function fieldName(path: string): string {
  const parts = path.split("/").filter(Boolean);
  const node = parts.indexOf("nodes");
  if (node >= 0 && parts[node + 1] && parts[parts.length - 2] === "layer") {
    return `${parts[node + 1] ?? ""}.${parts[parts.length - 1] ?? ""}`;
  }
  return parts[parts.length - 1] ?? path;
}

/** The model's name and kind as the header says them: a network's label or reference name,
 * or a classical estimator. */
export function modelName(study: Study): string {
  const base = study.base as { config?: { model?: { name?: string; graph?: { label?: string } } }; classical?: { model?: { estimator?: string } } };
  if (base.classical?.model?.estimator) return base.classical.model.estimator;
  const model = base.config?.model;
  return model?.graph?.label ?? model?.name ?? "model";
}

/** A fixed (untuned) field of the base worth showing beside the best values: epochs for a
 * network, folds for a classical model. */
export function fixedFields(study: Study): [string, unknown][] {
  const tuned = new Set(study.space.map((m) => (m as Marker).path));
  const base = study.base as { config?: Record<string, unknown> };
  if (study.kind === "classical") return [["cv_folds", study.cv_folds]];
  return tuned.has("/config/epochs") ? [] : [["epochs", base.config?.epochs]];
}

/** A tuned value as the table shows it: 4 significant figures, small numbers in e-notation. */
export function showValue(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (typeof value !== "number") return JSON.stringify(value);
  if (Number.isInteger(value)) return String(value);
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e5)) return value.toExponential(1);
  return Number(value.toPrecision(3)).toString();
}

/** The objective's value: three decimals. */
export function score(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : value.toFixed(3);
}

/** Running best: for each complete trial in order, the best value so far. */
export function bestSoFar(trials: Trial[], direction: Study["direction"]): [number, number][] {
  const out: [number, number][] = [];
  let best: number | null = null;
  for (const t of [...trials].sort((a, b) => a.number - b.number)) {
    if (t.state !== "complete" || t.value === null) continue;
    best = best === null ? t.value : direction === "maximize" ? Math.max(best, t.value) : Math.min(best, t.value);
    out.push([t.number, best]);
  }
  return out;
}

/** The unit a trial's step counts: epochs for a network, folds for a classical model. */
export function stepUnit(study: Study): "epoch" | "fold" {
  return study.kind === "classical" ? "fold" : "epoch";
}

/** How many steps a finished trial would take: the base's epochs, or the folds. */
export function stepsOf(study: Study): number | null {
  if (study.kind === "classical") return study.cv_folds;
  const epochs = (study.base as { config?: { epochs?: number } }).config?.epochs;
  return typeof epochs === "number" ? epochs : null;
}
