// SCR-04 stage drawer (frame B; MON-4.2, MON-2.5, MON-3): times, versions, the stage's own
// metrics, peaks, its resource curves and its events.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, call, type Job, type JobStage } from "../../api/client";
import { Drawer } from "../../components/Drawer";
import { LiveChart, type Series } from "../../components/LiveChart";
import { StatePill } from "../../components/ui";
import { clock, count, duration, gb } from "../../lib/format";
import { stageSeconds } from "./jobs";

const STATUS_PILL: Partial<Record<JobStage["status"], "completed" | "failed" | "running" | "cancelled" | "queued">> = {
  completed: "completed",
  failed: "failed",
  running: "running",
  cancelled: "cancelled",
  pending: "queued",
  queued: "queued",
  skipped: "cancelled",
};

function metricLine(metrics: Record<string, unknown> | null): string | null {
  if (!metrics) return null;
  const parts = Object.entries(metrics)
    .filter(([, v]) => typeof v === "number" || typeof v === "string" || typeof v === "boolean")
    .slice(0, 6)
    .map(([k, v]) => `${k} ${typeof v === "number" ? count(v) : String(v as string | boolean)}`);
  return parts.length ? parts.join(" · ") : null;
}

export function StageDrawer({ job, stage, now, onClose }: { job: Job; stage: JobStage; now: number; onClose: () => void }) {
  const telemetry = useQuery({
    queryKey: ["jobs", job.id, "telemetry", stage.name],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/jobs/{job_id}/telemetry", {
          params: { path: { job_id: job.id }, query: { stage: stage.name, max_points: 300 } },
        }),
      ),
  });
  const events = useQuery({
    queryKey: ["jobs", job.id, "events", stage.name],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/jobs/{job_id}/events", {
          params: { path: { job_id: job.id }, query: { stage: stage.name, tail: true, limit: 100 } },
        }),
      ),
  });
  const seconds = stageSeconds(stage, now);
  const peaks = (stage.peaks ?? {}) as Record<string, number | undefined>;
  const resource = telemetry.data?.resource ?? [];
  const process = telemetry.data?.process ?? [];
  const at = (ts: string) => new Date(ts).getTime();
  const gpu = stage.queue === "gpu";
  const series: Series[] = [
    { name: "CPU %", token: "--res-cpu", points: resource.map((r) => [at(r.ts), r.cpu_percent]) },
    { name: "RAM GB", token: "--res-ram", axis: 1, points: resource.map((r) => [at(r.ts), r.ram_used_mb / 1024]) },
    ...(gpu
      ? [
          { name: "VRAM GB", token: "--res-vram", axis: 1 as const, points: resource.map((r): [number, number | null] => [at(r.ts), r.vram_used_mb === null ? null : r.vram_used_mb / 1024]) },
          { name: "torch alloc GB", token: "--res-vram", axis: 1 as const, dashed: true, points: process.map((p): [number, number | null] => [at(p.ts), p.torch_allocated_mb === null ? null : p.torch_allocated_mb / 1024]) },
        ]
      : []),
  ];
  const work = [
    stage.total !== null ? `${count(stage.step)} / ${count(stage.total)}` : null,
    stage.throughput !== null ? `${stage.throughput.toFixed(1)}/s` : null,
    stage.cache_hit ? "reused an existing version (cached)" : null,
  ].filter(Boolean);
  return (
    <Drawer
      open
      className="stage-drawer"
      title={`Stage ${stage.name}`}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      heading={
        <span style={{ display: "inline-flex", gap: "var(--sp-2)", alignItems: "center" }}>
          <h2 style={{ margin: 0, font: "var(--fw-semibold) var(--fs-16) var(--font-mono)" }}>{stage.name}</h2>
          <StatePill state={STATUS_PILL[stage.status] ?? "queued"} label={stage.status.charAt(0).toUpperCase() + stage.status.slice(1)} />
        </span>
      }
    >
      <div className="kv">
        <span>Ran</span>
        <span className="mono">
          {stage.started_at ? `${clock(stage.started_at)} – ${stage.finished_at ? clock(stage.finished_at) : "now"} · ${duration(seconds)}` : "not started"} · {stage.queue} worker
        </span>
        <span>Versions</span>
        <span className="mono">
          {stage.input_version_id ? <Link to={`/versions/${stage.input_version_id}`}>{stage.input_version_id}</Link> : "source folder"} →{" "}
          {stage.output_version_id ? <Link to={`/versions/${stage.output_version_id}`}>{stage.output_version_id}</Link> : "–"}
        </span>
        <span>Work</span>
        <span className="mono">{work.length ? work.join(" · ") : "–"}</span>
        {metricLine(stage.metrics) ? (
          <>
            <span>Metrics</span>
            <span className="mono">{metricLine(stage.metrics)}</span>
          </>
        ) : null}
      </div>
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Peaks</th>
              <th className="r">RAM</th>
              <th className="r">VRAM</th>
              <th className="r">GPU temp</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{stage.name}</td>
              <td className="r">{peaks.ram_used_mb !== undefined ? `${gb(peaks.ram_used_mb)} GB` : "—"}</td>
              <td className="r">{gpu && peaks.vram_used_mb !== undefined ? `${gb(peaks.vram_used_mb)} GB` : "—"}</td>
              <td className="r">{peaks.gpu_temp_c !== undefined ? `${String(Math.round(peaks.gpu_temp_c))} °C` : "—"}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {resource.length > 1 ? (
        <LiveChart series={series} units={["%", "GB"]} label={`Resources during ${stage.name}: ${series.map((s) => s.name).join(", ")}`} />
      ) : (
        <p className="note">{telemetry.isPending ? "Loading samples…" : "No resource samples were taken during this stage (it was too short or reused)."}</p>
      )}
      {!gpu ? <p className="note">GPU lines are hidden: this stage ran on the cpu queue.</p> : null}
      <div className="log narrow" aria-label={`Events of ${stage.name}`}>
        {[...(events.data?.items ?? [])].reverse().map((e) => (
          <div key={e.id} className={e.level === "WARN" ? "warn" : e.level === "ERROR" ? "error" : undefined}>
            <span className="ts">{clock(e.ts)}</span>
            <span className={`lv-${e.level}`}>{e.level}</span>
            <span>{e.message}</span>
            {e.reason || e.suggested_action ? (
              <span className="why">
                {e.reason ? <><b>Why</b> {e.reason} </> : null}
                {e.suggested_action ? <><b>Do</b> {e.suggested_action}</> : null}
              </span>
            ) : null}
          </div>
        ))}
      </div>
    </Drawer>
  );
}
