// Queries for SCR-13, the system monitor (API.md §3.13; MON-2.6, MON-2.7, MON-3.2, MON-3.3,
// MON-4.4; ADR-0020 rule 13). A range that ends now refetches on a timer and on the alert,
// diagnostic and load events of the global stream, so new rows join the top of their tables.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api, call, type Schemas, type StreamEvent } from "./client";
import { useFeedMessage } from "./stream";

export type TelemetryRange = Schemas["TelemetryRange"];
export type RangePoint = Schemas["RangePoint"];
export type RangeJob = Schemas["RangeJob"];
export type SystemAlerts = Schemas["SystemAlerts"];
export type SystemAlert = Schemas["SystemAlert"];
export type SystemDiagnostics = Schemas["SystemDiagnostics"];
export type DiagnosticLive = Schemas["DiagnosticLive"];
export type JobOverlay = Schemas["JobOverlay"];
export type OverlaySide = Schemas["OverlaySide"];
export type MonitorEvent = Schemas["Event"];

/** A time range: a preset that ends now, or fixed ends (ISO). */
export type Range =
  | { kind: "preset"; minutes: number }
  | { kind: "custom"; from: string; to: string };

export const PRESETS: { label: string; minutes: number }[] = [
  { label: "1 h", minutes: 60 },
  { label: "6 h", minutes: 360 },
  { label: "24 h", minutes: 1440 },
  { label: "7 d", minutes: 10080 },
];

export function bounds(range: Range, now = Date.now()): { from: string; to: string } {
  if (range.kind === "custom") return { from: range.from, to: range.to };
  return { from: new Date(now - range.minutes * 60_000).toISOString(), to: new Date(now).toISOString() };
}

const LIVE_MS = 10_000;
const keys = {
  all: ["monitor"] as const,
  telemetry: (r: Range) => ["monitor", "telemetry", r] as const,
  alerts: (r: Range) => ["monitor", "alerts", r] as const,
  diagnostics: (r: Range) => ["monitor", "diagnostics", r] as const,
  models: (r: Range) => ["monitor", "models", r] as const,
  overlay: (a: string, b: string, align: string) => ["monitor", "overlay", a, b, align] as const,
};

const live = (range: Range) => (range.kind === "preset" ? LIVE_MS : false);

export function useTelemetryRange(range: Range) {
  return useQuery({
    queryKey: keys.telemetry(range),
    queryFn: () => call(() => api.GET("/api/v1/telemetry", { params: { query: { ...bounds(range), max_points: 600 } } })),
    refetchInterval: live(range),
    placeholderData: (previous) => previous,
  });
}

export function useAlerts(range: Range) {
  return useQuery({
    queryKey: keys.alerts(range),
    queryFn: () => call(() => api.GET("/api/v1/system/alerts", { params: { query: { ...bounds(range), limit: 50 } } })),
    refetchInterval: live(range),
    placeholderData: (previous) => previous,
  });
}

export function useDiagnostics(range: Range) {
  return useQuery({
    queryKey: keys.diagnostics(range),
    queryFn: () => call(() => api.GET("/api/v1/system/diagnostics", { params: { query: { ...bounds(range), limit: 50 } } })),
    // *Now* is recomputed by the API; the watchdog diagnoses every 5 s (ADR-0020 rule 8).
    refetchInterval: range.kind === "preset" ? 5_000 : false,
    placeholderData: (previous) => previous,
  });
}

export function useModelEvents(range: Range) {
  return useQuery({
    queryKey: keys.models(range),
    queryFn: () => call(() => api.GET("/api/v1/system/models", { params: { query: { ...bounds(range), limit: 50 } } })),
    refetchInterval: live(range),
    placeholderData: (previous) => previous,
  });
}

export function useJobOverlay(a: string | null, b: string | null, align: "time" | "epoch") {
  return useQuery({
    queryKey: keys.overlay(a ?? "", b ?? "", align),
    enabled: Boolean(a && b),
    queryFn: () =>
      call(() => api.GET("/api/v1/system/compare", { params: { query: { a: a ?? "", b: b ?? "", align, max_points: 600 } } })),
  });
}

export type SystemHistory = Schemas["SystemHistory"];
export type HistoryRun = Schemas["HistoryRun"];

export interface HistoryQuery {
  stage: string | null;
  queue: string | null;
  group: string | null; // "all" for every group
  n: number;
  reused: boolean;
}

/** The last N runs of one stage in one group (MON-4.5, SCR-13 History). */
export function useHistory(q: HistoryQuery) {
  return useQuery({
    queryKey: ["monitor", "history", q.stage ?? "", q.queue ?? "", q.group ?? "", q.n, q.reused],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/system/history", {
          params: {
            query: {
              ...(q.stage ? { stage: q.stage } : {}),
              ...(q.queue ? { queue: q.queue } : {}),
              ...(q.group ? { group: q.group } : {}),
              n: q.n,
              reused: q.reused,
            },
          },
        }),
      ),
  });
}

/** Recent jobs for the pickers: training runs first (SCR-13 frame C). */
export function useRecentJobs() {
  return useQuery({
    queryKey: ["monitor", "jobs"],
    queryFn: () => call(() => api.GET("/api/v1/jobs", { params: { query: { limit: 100 } } })),
  });
}

const WATCHED = new Set(["alert", "diagnostic", "model_loaded", "model_unloaded", "action", "stage_failed"]);

/** Refresh the tables when an alert, a diagnosis, a load or an action arrives. */
export function useMonitorLive(): void {
  const client = useQueryClient();
  const onEvent = useCallback(
    (event: StreamEvent) => {
      if (!WATCHED.has(event.type)) return;
      void client.invalidateQueries({ queryKey: ["monitor", "alerts"] });
      void client.invalidateQueries({ queryKey: ["monitor", "diagnostics"] });
      void client.invalidateQueries({ queryKey: ["monitor", "models"] });
    },
    [client],
  );
  useFeedMessage("event", onEvent);
}

export const ALERT_NAMES: Record<string, string> = {
  vram_high: "VRAM high",
  ram_high: "RAM high",
  gpu_hot: "GPU hot",
  no_progress: "No progress",
};

/** "84 °C ≥ 83", "93 % > 90", "300 s" for an alert's value against its threshold. */
export function alertValue(alert: SystemAlert): string {
  const m = (alert.fired.metrics ?? {}) as Record<string, number | string | undefined>;
  const value = Number(m["value"]);
  const threshold = Number(m["threshold"]);
  if (alert.code === "gpu_hot") return `${value.toFixed(0)} °C ≥ ${threshold.toFixed(0)}`;
  if (alert.code === "no_progress") return `${value.toFixed(0)} s`;
  return `${value.toFixed(0)} % > ${threshold.toFixed(0)}`;
}

/** The pill an action draws (pause, stalled, failed) or "warned only". */
export function actionOf(alert: SystemAlert): { state: "paused" | "stalled" | "failed" | null; resumedAt: string | null } {
  const codes = alert.actions.map((a) => a.metrics?.["code"]);
  const resumed = alert.actions.find((a) => a.metrics?.["code"] === "thermal_resume");
  if (codes.includes("thermal_pause")) return { state: "paused", resumedAt: resumed?.ts ?? null };
  if (codes.includes("stalled")) return { state: "stalled", resumedAt: null };
  if (alert.actions.some((a) => a.type === "stage_failed")) return { state: "failed", resumedAt: null };
  return { state: null, resumedAt: null };
}
