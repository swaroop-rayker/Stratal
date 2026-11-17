// SCR-01 regions 6 and 7: the resource strip and the connection state.
// GET /telemetry/latest and GET /system on load, then `sample` messages (MON-2.1).
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import { api, call, type StreamSample, type System } from "../api/client";
import { type Connection, useFeedMessage } from "../api/stream";
import { Icon, Skeleton, Sparkline } from "../components/ui";
import { clock, gb } from "../lib/format";

const HISTORY = 60;

interface Series {
  cpu: number[];
  ram: number[];
  gpu: number[];
  vram: number[];
}

function push(values: number[], value: number | null | undefined): number[] {
  if (value === null || value === undefined) return values;
  const next = [...values, value];
  return next.length > HISTORY ? next.slice(next.length - HISTORY) : next;
}

/** Re-render every second so "stale" is judged against the current time. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);
  return now;
}

function Unavailable({ label, res, reason }: { label: string; res: string; reason: string }) {
  return (
    <span className="m unavail" style={{ "--res": `var(${res})` } as React.CSSProperties} title={reason}>
      <i className="sq" />
      {label} <b>— unavailable</b>
    </span>
  );
}

export function ResourceStrip({ connection }: { connection: Connection }) {
  const latest = useQuery({
    queryKey: ["telemetry", "latest"],
    queryFn: () => call(() => api.GET("/api/v1/telemetry/latest")),
  });
  const system = useQuery({
    queryKey: ["system"],
    queryFn: () => call(() => api.GET("/api/v1/system")),
    staleTime: 5 * 60_000,
  });
  const [sample, setSample] = useState<StreamSample | null>(null);
  const [series, setSeries] = useState<Series>({ cpu: [], ram: [], gpu: [], vram: [] });

  const take = useCallback((next: StreamSample) => {
    setSample(next);
    setSeries((s) => ({
      cpu: push(s.cpu, next.cpu_percent),
      ram: push(s.ram, next.ram_used_mb),
      gpu: push(s.gpu, next.gpu_util_percent),
      vram: push(s.vram, next.vram_used_mb),
    }));
  }, []);
  useFeedMessage("sample", take);

  const now = useNow();
  // Until the first live sample, show the latest stored one.
  const shown: StreamSample | null = sample ?? latest.data ?? null;

  if (shown === null) {
    if (latest.isPending || latest.isSuccess) {
      return (
        <footer className="status" aria-label="Machine resources" aria-busy="true">
          {["CPU", "RAM", "GPU", "VRAM"].map((label, i) => (
            <span key={label} className="m">
              {label} <Skeleton width={i % 2 ? 72 : 28} height={8} />
            </span>
          ))}
          <span className="m conn">
            {connection === "reconnecting" ? (
              <span className="lost">
                <Icon name="refresh" style={{ width: 12, height: 12 }} />
                Reconnecting…
              </span>
            ) : (
              "connecting…"
            )}
          </span>
        </footer>
      );
    }
  }

  const info: System | undefined = system.data;
  const s: Partial<StreamSample> = shown ?? {};
  const interval = s.interval_s ?? 1;
  const age = s.ts ? (now - new Date(s.ts).getTime()) / 1000 : Infinity;
  const stale = connection !== "live" || age > 3 * interval;
  const ramTotal = s.ram_total_mb ?? info?.ram_total_mb ?? null;
  const vramTotal = s.vram_total_mb ?? info?.vram_total_mb ?? null;
  const gpuReason = info?.gpu_error
    ? `NVML not reachable (${info.gpu_error}). Restart WSL, then retry.`
    : "The GPU's metrics could not be read in this sample.";
  const idle = interval >= 5;
  const label = `CPU ${String(Math.round(s.cpu_percent ?? 0))}%, RAM ${gb(s.ram_used_mb)} of ${gb(
    ramTotal,
  )} GB, GPU ${s.gpu_util_percent === null || s.gpu_util_percent === undefined ? "unavailable" : `${String(Math.round(s.gpu_util_percent))}%`}, VRAM ${gb(s.vram_used_mb)} of ${gb(vramTotal)} GB`;

  return (
    <footer className="status" aria-label={`Machine resources: ${label}`} aria-live="off">
      <span className={`m${stale ? " stale" : ""}`} style={{ "--res": "var(--res-cpu)" } as React.CSSProperties}>
        <i className="sq" />
        CPU <b>{s.cpu_percent === undefined ? "—" : `${String(Math.round(s.cpu_percent ?? 0))}%`}</b>
        <Sparkline values={series.cpu} max={100} color="var(--res-cpu)" />
      </span>
      <span className={`m${stale ? " stale" : ""}`} style={{ "--res": "var(--res-ram)" } as React.CSSProperties}>
        <i className="sq" />
        RAM <b>{gb(s.ram_used_mb)}</b>
        <small>/ {gb(ramTotal)}<span className="unit"> GB</span></small>
        <Sparkline values={series.ram} max={ramTotal ?? 1} color="var(--res-ram)" />
      </span>
      {s.gpu_util_percent === null ? (
        <Unavailable label="GPU" res="--res-gpu" reason={gpuReason} />
      ) : (
        <span className={`m${stale ? " stale" : ""}`} style={{ "--res": "var(--res-gpu)" } as React.CSSProperties}>
          <i className="sq" />
          GPU <b>{s.gpu_util_percent === undefined ? "—" : `${String(Math.round(s.gpu_util_percent))}%`}</b>
          <Sparkline values={series.gpu} max={100} color="var(--res-gpu)" />
        </span>
      )}
      {s.vram_used_mb === null ? (
        <Unavailable label="VRAM" res="--res-vram" reason={gpuReason} />
      ) : (
        <span className={`m${stale ? " stale" : ""}`} style={{ "--res": "var(--res-vram)" } as React.CSSProperties}>
          <i className="sq" />
          VRAM <b>{gb(s.vram_used_mb)}</b>
          <small>/ {gb(vramTotal)}<span className="unit"> GB</span></small>
          <Sparkline values={series.vram} max={vramTotal ?? 1} color="var(--res-vram)" />
        </span>
      )}
      {s.gpu_temp_c === null ? (
        <Unavailable label="GPU" res="--res-temp" reason={gpuReason} />
      ) : (
        <span className={`m${stale ? " stale" : ""}`} style={{ "--res": "var(--res-temp)" } as React.CSSProperties}>
          <i className="sq" />
          GPU <b>{s.gpu_temp_c === undefined ? "—" : `${String(Math.round(s.gpu_temp_c))} °C`}</b>
          {s.gpu_power_w === null || s.gpu_power_w === undefined ? null : (
            <small>· {Math.round(s.gpu_power_w)} W</small>
          )}
        </span>
      )}
      <span className="m conn">
        {connection === "live" ? (
          <span className="live">
            <Icon name="check" style={{ width: 12, height: 12 }} />
            live
          </span>
        ) : (
          <span className="lost">
            <Icon name="refresh" style={{ width: 12, height: 12 }} />
            {connection === "connecting" ? "connecting…" : "Reconnecting…"}
          </span>
        )}
        {connection === "live"
          ? `· ${String(Math.round(interval))} s${idle ? " idle" : ""} · last ${clock(s.ts)}`
          : s.ts
            ? `· values from ${clock(s.ts)}`
            : null}
      </span>
    </footer>
  );
}
