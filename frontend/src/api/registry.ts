// Queries and mutations for the model registry, retention and settings (SCR-11, SCR-14;
// API.md §3.9, §3.10). A `job` message from a training or retention run refreshes them,
// since that is what changes versions, weights and disk use.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { ApiError, api, call, type Job, type Schemas } from "./client";
import { useFeedMessage } from "./stream";

export type Lineage = Schemas["ModelLineage"];
export type LineageDetail = Schemas["ModelLineageDetail"];
export type ModelVersion = Schemas["ModelVersion"];
export type VersionDetail = Schemas["ModelVersionDetail"];
export type DiskReport = Schemas["DiskReport"];
export type RetentionPlan = Schemas["RetentionPlan"];
export type LineagePlan = Schemas["LineagePlan"];
export type Prediction = Schemas["Prediction"];
export type SettingsSection = Schemas["SettingsSection"];
export type VersionState = ModelVersion["state"];
export type Keep = number | "all";

export const registryKeys = {
  all: ["models"] as const,
  list: (state: string | null, q: string) => ["models", "list", state, q] as const,
  lineage: (id: string) => ["models", "lineage", id] as const,
  disk: (id: string) => ["models", "disk", id] as const,
  version: (id: string) => ["models", "version", id] as const,
  settings: ["settings"] as const,
};

/** Refresh registry views when a training, retention or Score on test job changes state. */
export function useRegistryLive(): void {
  const client = useQueryClient();
  const onJob = useCallback(
    (job: Job) => {
      if (job.kind !== "train" && job.kind !== "retention" && job.kind !== "score_test") return;
      void client.invalidateQueries({ queryKey: registryKeys.all });
      void client.invalidateQueries({ queryKey: registryKeys.settings });
    },
    [client],
  );
  useFeedMessage("job", onJob);
}

export function useLineages(state: VersionState | null, q: string) {
  return useQuery({
    queryKey: registryKeys.list(state, q),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/models", {
          params: { query: { state: state ?? undefined, q: q || undefined, limit: 500 } },
        }),
      ),
  });
}

export function useLineage(id: string) {
  return useQuery({
    queryKey: registryKeys.lineage(id),
    queryFn: () =>
      call(() => api.GET("/api/v1/models/{lineage_id}", { params: { path: { lineage_id: id } } })),
  });
}

export function useDisk(id: string) {
  return useQuery({
    queryKey: registryKeys.disk(id),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/models/{lineage_id}/disk", { params: { path: { lineage_id: id } } }),
      ),
  });
}

export function useVersionDetail(id: string | null) {
  return useQuery({
    queryKey: registryKeys.version(id ?? ""),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/model-versions/{version_id}", {
          params: { path: { version_id: id ?? "" } },
        }),
      ),
    enabled: id !== null,
  });
}

export function usePin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) =>
      call(() =>
        pinned
          ? api.POST("/api/v1/model-versions/{version_id}/pin", {
              params: { path: { version_id: id } },
            })
          : api.DELETE("/api/v1/model-versions/{version_id}/pin", {
              params: { path: { version_id: id } },
            }),
      ),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: registryKeys.all });
    },
  });
}

export function useRetrain() {
  return useMutation({
    mutationFn: (id: string) =>
      call(() =>
        api.POST("/api/v1/model-versions/{version_id}/retrain", {
          params: { path: { version_id: id } },
        }),
      ),
  });
}

/** The dry run (REG-2.9): what retention would do, with saved or proposed settings. */
export function fetchPlan(lineageId: string | null, values?: Schemas["RetentionValues"]) {
  return call(() =>
    api.POST("/api/v1/retention/plan", {
      body: { lineage_id: lineageId, values: values ?? null },
    }),
  );
}

export function useRetentionRun() {
  return useMutation({
    mutationFn: (lineageId: string | null) =>
      call(() =>
        api.POST("/api/v1/retention/run", { body: { lineage_id: lineageId, dry_run: false } }),
      ),
  });
}

export function usePredict(versionId: string) {
  return useMutation({
    mutationFn: (body: { image_b64?: string; blob?: string; record?: Record<string, unknown>; text?: string }) =>
      call(() =>
        api.POST("/api/v1/models/{version_id}/predict", {
          params: { path: { version_id: versionId } },
          body,
        }),
      ),
  });
}

export function useSettings() {
  return useQuery({
    queryKey: registryKeys.settings,
    queryFn: () => call(() => api.GET("/api/v1/settings")),
  });
}

/** A section save. A 409 ``confirmation_required`` carries the plan to confirm (REG-2.10). */
export interface SaveRequest {
  section: "training" | "retention" | "monitoring" | "retrieval";
  values: Record<string, unknown>;
  version: string;
  confirm?: string | null;
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SaveRequest) => call(() => api.PUT("/api/v1/settings", { body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: registryKeys.settings });
      void client.invalidateQueries({ queryKey: registryKeys.all });
    },
  });
}

/** The plan inside a ``confirmation_required`` problem, if that is what the error is. */
export function planOf(error: unknown): RetentionPlan | null {
  if (!(error instanceof ApiError) || error.problem.code !== "confirmation_required") return null;
  const plan = (error.problem as unknown as { plan?: RetentionPlan }).plan;
  return plan ?? null;
}

/** keep_intermediate as words: "0", "3", "all". */
export function keepText(keep: Keep): string {
  return keep === "all" ? "all" : String(keep);
}

/** Two to six versions on one test split (EVAL-1.3, SCR-11 v2). */
export function useComparison(split: string | null, ids: string[]) {
  return useQuery({
    queryKey: ["models", "compare", split, ids],
    queryFn: () => call(() => api.GET("/api/v1/models/compare", { params: { query: { split: split ?? "", v: ids } } })),
    enabled: split !== null && ids.length >= 2,
    retry: false,
  });
}

/** The versions trained on a split, for the comparison's picker (SCR-11 v2 frame B). */
export function useCompareCandidates(split: string | null) {
  return useQuery({
    queryKey: ["models", "compare", "candidates", split],
    queryFn: () => call(() => api.GET("/api/v1/models/compare/candidates", { params: { query: { split: split ?? "" } } })),
    enabled: split !== null,
  });
}

/** Score on test, once, a version without test metrics (K8's exception). */
export function useScoreTest() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      call(() => api.POST("/api/v1/model-versions/{version_id}/score-test", { params: { path: { version_id: id } } })),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["models", "compare"] }); },
  });
}
