// SCR-04 resource tiles (MON-4.1, MON-2.1, MON-2.4): CPU, RAM, GPU, VRAM (NVML used beside
// PyTorch allocated, dashed), temperature and power, each with its chart. Values that cannot
// be read show "unavailable", never zero (principle 6).
import type { CSSProperties } from "react";

import { Icon } from "../../components/ui";
import { gb } from "../../lib/format";

/** One point of the job's telemetry: the resource view merged with the job's own. */
export interface Point {
  ts: string;
  cpu_percent?: number | null;
  ram_used_mb?: number | null;
  ram_total_mb?: number | null;
  gpu_util_percent?: number | null;
  gpu_mem_util_percent?: number | null;
  vram_used_mb?: number | null;
  vram_total_mb?: number | null;
  gpu_temp_c?: number | null;
  gpu_power_w?: number | null;
  rss_mb?: number | null;
  torch_allocated_mb?: number | null;
  torch_reserved_mb?: number | null;
}

type Key = keyof Omit<Point, "ts">;

function values(points: Point[], key: Key): number[] {
  return points.map((p) => p[key]).filter((v): v is number => typeof v === "number");
}

function last(points: Point[], key: Key): number | null | undefined {
  for (let i = points.length - 1; i >= 0; i--) {
    const v = points[i]?.[key];
    if (v !== undefined) return v;
  }
  return undefined;
}

function Chart({ points, keys, max, color }: { points: Point[]; keys: { key: Key; dashed?: boolean }[]; max: number; color: string }) {
  const lines = keys.map(({ key, dashed }) => {
    const xs = points.map((p) => p[key]);
    const n = Math.max(1, xs.length - 1);
    const pts = xs
      .map((v, i) => (typeof v === "number" ? `${((i / n) * 100).toFixed(1)},${(45 - (Math.min(v, max) / max) * 43).toFixed(1)}` : null))
      .filter(Boolean)
      .join(" ");
    return (
      <polyline
        key={key}
        fill="none"
        stroke={color}
        strokeWidth="1.4"
        strokeDasharray={dashed ? "4 3" : undefined}
        vectorEffect="non-scaling-stroke"
        points={pts}
      />
    );
  });
  return (
    <svg className="ch" viewBox="0 0 100 46" preserveAspectRatio="none" aria-hidden="true">
      <line className="grid" x1="0" y1="23" x2="100" y2="23" />
      {points.length > 1 ? lines : null}
    </svg>
  );
}

function Tile({ res, label, value, unit, detail, children, unavailable, info }: {
  res: string;
  label: string;
  value: string;
  unit?: string;
  detail: string;
  children: React.ReactNode;
  unavailable?: string | null;
  info?: string;
}) {
  if (unavailable) {
    return (
      <div className="tile unavailable" style={{ "--res": `var(${res})` } as CSSProperties} title={unavailable}>
        <div className="k">
          <i />
          {label}
        </div>
        <div className="v">— unavailable</div>
        <div className="d">{unavailable}</div>
        {children}
      </div>
    );
  }
  return (
    <div className="tile" style={{ "--res": `var(${res})` } as CSSProperties}>
      <div className="k">
        <i />
        {label}
        {info ? (
          <span title={info} aria-label={info} tabIndex={0} style={{ display: "inline-flex" }}>
            <Icon name="info" style={{ width: 12, height: 12 }} />
          </span>
        ) : null}
      </div>
      <div className="v">
        {value}
        {unit ? <small>{unit}</small> : null}
      </div>
      <div className="d">{detail}</div>
      {children}
    </div>
  );
}

const fmt = (v: number | null | undefined, digits = 0) => (typeof v === "number" ? v.toFixed(digits) : "—");

function max0(points: Point[], k: Key): number | null {
  const xs = values(points, k);
  return xs.length ? Math.max(...xs) : null;
}

/** `training` (SCR-04 v2, region 6): the CPU tile names the loader's workers and the
 * temperature tile the pause limit (RUN-1.6). */
export function ResourceTiles({ points, cores, gpuError, training }: {
  points: Point[];
  cores: number | null;
  gpuError: string | null;
  training?: { loaderWorkers: number | null; pauseAt: number | null };
}) {
  const workers = training?.loaderWorkers ?? null;
  const pauseAt = training?.pauseAt ?? null;
  const cpuDetail = workers !== null
    ? `loader: ${String(workers)} ${workers === 1 ? "worker" : "workers"}`
    : `${cores ? `${String(cores)} cores · ` : ""}max ${fmt(max0(points, "cpu_percent"))}%`;
  const has = points.length > 0;
  const gpuNull = has && last(points, "gpu_util_percent") === null;
  const unavailable = gpuNull ? `NVML not reachable${gpuError ? ` · ${gpuError}` : ""}` : null;
  const ramTotal = last(points, "ram_total_mb") ?? null;
  const vramTotal = last(points, "vram_total_mb") ?? null;
  const max = (k: Key) => (values(points, k).length ? Math.max(...values(points, k)) : null);
  const alloc = last(points, "torch_allocated_mb");
  const reserved = last(points, "torch_reserved_mb");
  const power = last(points, "gpu_power_w");
  return (
    <div className="res">
      <Tile res="--res-cpu" label="CPU" value={fmt(last(points, "cpu_percent"))} unit="%" detail={cpuDetail}>
        <Chart points={points} keys={[{ key: "cpu_percent" }]} max={100} color="var(--res-cpu)" />
      </Tile>
      <Tile res="--res-ram" label="RAM" value={gb(last(points, "ram_used_mb"))} unit={` / ${gb(ramTotal)} GB`} detail={`job RSS ${gb(last(points, "rss_mb"))} GB`}>
        <Chart points={points} keys={[{ key: "ram_used_mb" }]} max={ramTotal ?? 1} color="var(--res-ram)" />
      </Tile>
      <Tile
        res="--res-gpu"
        label="GPU"
        value={fmt(last(points, "gpu_util_percent"))}
        unit="%"
        detail={`memory controller ${fmt(last(points, "gpu_mem_util_percent"))}%`}
        unavailable={unavailable}
        info="GPU utilisation is the share of time at least one kernel was running. 100% does not mean the GPU is used efficiently."
      >
        <Chart points={points} keys={[{ key: "gpu_util_percent" }]} max={100} color="var(--res-gpu)" />
      </Tile>
      <Tile
        res="--res-vram"
        label="VRAM"
        value={gb(last(points, "vram_used_mb"))}
        unit={` / ${gb(vramTotal)} GB`}
        detail={typeof alloc === "number" ? `torch alloc ${gb(alloc)} · rsv ${gb(reserved)}` : "torch: no CUDA in this stage"}
        unavailable={unavailable}
      >
        <Chart points={points} keys={[{ key: "vram_used_mb" }, { key: "torch_allocated_mb", dashed: true }]} max={vramTotal ?? 1} color="var(--res-vram)" />
      </Tile>
      <Tile
        res="--res-temp"
        label="GPU temp · power"
        value={fmt(last(points, "gpu_temp_c"))}
        unit=" °C"
        detail={`${typeof power === "number" ? `${power.toFixed(0)} W · ` : ""}${pauseAt !== null ? `pauses at ${String(pauseAt)} °C` : `max ${fmt(max("gpu_temp_c"))} °C`}`}
        unavailable={unavailable}
      >
        <Chart points={points} keys={[{ key: "gpu_temp_c" }]} max={100} color="var(--res-temp)" />
      </Tile>
    </div>
  );
}
