// SCR-13 Compare two jobs (approved hi-fi, frame C; MON-4.4, K7): two pickers, aligned by
// time spent running or by epoch, small multiples A solid and B dashed in each lane's
// resource colour, and a table of means and peaks with no "better" mark.
import type { CSSProperties } from "react";

import { useJobOverlay, useRecentJobs, type OverlaySide } from "../../api/monitor";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Skeleton } from "../../components/ui";
import { LanesChart, type Lane } from "./Lanes";

const MB = 1024;
const TRAINING = new Set(["train", "hpo_trial"]);

function Picker({ label, value, onChange, dashed }: { label: string; value: string | null; onChange: (id: string) => void; dashed?: boolean }) {
  const jobs = useRecentJobs();
  const items = [...(jobs.data?.items ?? [])].sort((a, b) => Number(TRAINING.has(b.kind)) - Number(TRAINING.has(a.kind)));
  return (
    <label className="sys-pick">
      <b>{label}</b>
      <span className={dashed ? "sys-sw b" : "sys-sw"} aria-hidden="true" />
      <select className="input mono" value={value ?? ""} onChange={(e) => { onChange(e.target.value); }} aria-label={`Job ${label}`}>
        <option value="" disabled>Pick a job…</option>
        {items.map((j) => (
          <option key={j.id} value={j.id}>{j.id} · {j.kind} · {j.state}</option>
        ))}
      </select>
    </label>
  );
}

function lanesOf(a: OverlaySide, b: OverlaySide): Lane[] {
  const pts = (side: OverlaySide, f: (p: OverlaySide["machine"][number]) => number | null | undefined): [number, number | null][] =>
    side.machine.map((p) => [p.x, f(p) ?? null]);
  const proc = (side: OverlaySide, f: (p: OverlaySide["process"][number]) => number | null | undefined): [number, number | null][] =>
    side.process.map((p) => [p.x, f(p) ?? null]);
  const top = (values: (number | null)[], floor: number) => Math.max(floor, ...values.filter((v): v is number => v !== null)) * 1.1;
  const vram = [...pts(a, (p) => p.vram_used_mb), ...pts(b, (p) => p.vram_used_mb)].map(([, v]) => (v === null ? null : v / MB));
  const rss = [...proc(a, (p) => p.rss_mb), ...proc(b, (p) => p.rss_mb)].map(([, v]) => (v === null ? null : v / MB));
  const vMax = top(vram, 1);
  const rMax = top(rss, 1);
  return [
    { key: "gpu", token: "--res-gpu", label: "GPU", sub: "NVML · 0–100 %", info: "NVML's GPU utilisation is the share of time a kernel was running.", min: 0, max: 100, unit: " %", digits: 0, series: [{ name: "A", points: pts(a, (p) => p.gpu_util_percent) }, { name: "B", points: pts(b, (p) => p.gpu_util_percent), dashed: true }] },
    { key: "vram", token: "--res-vram", label: "VRAM", sub: `card · 0–${vMax.toFixed(1)} GB`, min: 0, max: vMax, unit: " GB", digits: 2, series: [{ name: "A", points: pts(a, (p) => (p.vram_used_mb === null ? null : p.vram_used_mb / MB)) }, { name: "B", points: pts(b, (p) => (p.vram_used_mb === null ? null : p.vram_used_mb / MB)), dashed: true }] },
    { key: "rss", token: "--res-ram", label: "Job RSS", sub: `the job's own · 0–${rMax.toFixed(0)} GB`, min: 0, max: rMax, unit: " GB", digits: 1, series: [{ name: "A", points: proc(a, (p) => (p.rss_mb === null ? null : p.rss_mb / MB)) }, { name: "B", points: proc(b, (p) => (p.rss_mb === null ? null : p.rss_mb / MB)), dashed: true }] },
    { key: "temp", token: "--res-temp", label: "GPU temp", sub: "°C · 30–90", min: 30, max: 90, unit: " °C", digits: 0, series: [{ name: "A", points: pts(a, (p) => p.gpu_temp_c) }, { name: "B", points: pts(b, (p) => p.gpu_temp_c), dashed: true }] },
  ];
}

const ROWS: { label: string; get: (s: OverlaySide["summary"]) => number | null; fmt: (v: number) => string }[] = [
  { label: "Seconds per epoch", get: (s) => s.seconds_per_epoch, fmt: (v) => `${v.toFixed(1)} s` },
  { label: "GPU, mean", get: (s) => s.gpu_mean_percent, fmt: (v) => `${v.toFixed(0)} %` },
  { label: "Card VRAM, peak", get: (s) => s.vram_peak_mb, fmt: (v) => `${(v / MB).toFixed(2)} GB` },
  { label: "PyTorch allocated, peak", get: (s) => s.torch_allocated_peak_mb, fmt: (v) => `${v.toFixed(0)} MB` },
  { label: "Job RSS, mean", get: (s) => s.rss_mean_mb, fmt: (v) => `${(v / MB).toFixed(1)} GB` },
  { label: "Job CPU, mean", get: (s) => s.cpu_cores_mean, fmt: (v) => `${v.toFixed(2)} cores` },
  { label: "GPU temperature, max", get: (s) => s.gpu_temp_max_c, fmt: (v) => `${v.toFixed(0)} °C` },
];

export function CompareJobs({ a, b, align, onChange }: {
  a: string | null;
  b: string | null;
  align: "time" | "epoch";
  onChange: (next: { a?: string | null; b?: string | null; align?: "time" | "epoch" }) => void;
}) {
  const overlay = useJobOverlay(a, b, align);
  // Ask by time first: its response says whether epoch alignment is possible.
  const probe = useJobOverlay(a, b, "time");
  const epochReason = probe.data?.epoch_unavailable ?? null;
  const data = overlay.data;
  const xMax = data ? Math.max(data.common_epochs ?? 0, ...[data.a, data.b].flatMap((s) => s.machine.map((p) => p.x))) : 1;
  return (
    <>
      <div className="sys-pickrow">
        <Picker label="A" value={a} onChange={(id) => { onChange({ a: id }); }} />
        <Picker label="B" value={b} dashed onChange={(id) => { onChange({ b: id }); }} />
        <Button variant="ghost" size="sm" onClick={() => { onChange({ a: b, b: a }); }} disabled={!a || !b}>Swap</Button>
        <span className="spacer" />
        <span className="group-btns" role="group" aria-label="Align">
          <button type="button" className="btn sm" aria-pressed={align === "time"} onClick={() => { onChange({ align: "time" }); }}>By time running</button>
          <button
            type="button"
            className={epochReason ? "btn sm is-disabled" : "btn sm"}
            aria-pressed={align === "epoch"}
            aria-disabled={epochReason ? true : undefined}
            title={epochReason ?? undefined}
            onClick={() => { if (!epochReason) onChange({ align: "epoch" }); }}
          >
            By epoch
          </button>
        </span>
      </div>
      {!a || !b ? (
        <section className="card"><EmptyState icon="list" title="Pick two jobs to compare">Training runs are listed first.</EmptyState></section>
      ) : overlay.isError ? (
        <section className="card"><ErrorState error={overlay.error} title="The two jobs could not be compared" onRetry={() => void overlay.refetch()} /></section>
      ) : !data ? (
        <section className="card" aria-busy="true"><Skeleton width={200} height={16} /><Skeleton width={900} height={260} /></section>
      ) : (
        <>
          <section className="card">
            <h2>
              {data.align === "epoch" ? `Epochs 1–${String(data.common_epochs ?? 0)}, the epochs both ran` : "Time spent running, pauses removed"}
              <span className="sub2">machine lanes include anything else that was running</span>
              <span className="spacer" />
              <span className="sys-legend-ab">
                <span><span className="sys-sw" /> A · {data.a.job_id} · {data.a.label}</span>
                <span><span className="sys-sw b" /> B · {data.b.job_id} · {data.b.label}</span>
              </span>
            </h2>
            {(() => {
              const lanes = lanesOf(data.a, data.b);
              return (
                <div className="sys-lanes">
                  <div className="labs">
                    {lanes.map((lane) => (
                      <div key={lane.key} className="lab" style={{ "--res": `var(${lane.token})` } as CSSProperties}>
                        <i />{lane.label}
                        {lane.info ? <span title={lane.info} aria-label={lane.info} tabIndex={0}><Icon name="info" className="sys-info" /></span> : null}
                        <small>{lane.sub}</small>
                      </div>
                    ))}
                  </div>
                  <div role="img" aria-label={`${data.a.job_id} solid and ${data.b.job_id} dashed, ${data.align === "epoch" ? "by epoch" : "by seconds running"}`}>
                    <LanesChart
                      lanes={lanes}
                      x="value"
                      min={0}
                      max={data.align === "epoch" ? (data.common_epochs ?? xMax) : xMax}
                      xLabel={(v) => (data.align === "epoch" ? (v === 0 ? "epoch 0" : v.toFixed(0)) : `${v.toFixed(0)} s`)}
                      tipTitle={(v) => (data.align === "epoch" ? `epoch ${v.toFixed(2)}` : `${v.toFixed(0)} s running`)}
                    />
                  </div>
                </div>
              );
            })()}
          </section>
          <div className="table-wrap sys-summary">
            <table className="t dense">
              <caption className="sr-only">Means and peaks over the aligned part</caption>
              <thead><tr><th>{data.align === "epoch" ? `Epochs 1–${String(data.common_epochs ?? 0)}` : "Running"}</th><th className="r">A · {data.a.job_id}</th><th className="r">B · {data.b.job_id}</th></tr></thead>
              <tbody>
                {ROWS.filter((row) => data.align === "epoch" || row.label !== "Seconds per epoch").map((row) => {
                  const va = row.get(data.a.summary);
                  const vb = row.get(data.b.summary);
                  return (
                    <tr key={row.label}>
                      <td>{row.label}</td>
                      <td className="r">{va === null ? "—" : row.fmt(va)}</td>
                      <td className="r">{vb === null ? "—" : row.fmt(vb)}</td>
                    </tr>
                  );
                })}
                <tr><td>Time running</td><td className="r">{data.a.running_s.toFixed(0)} s</td><td className="r">{data.b.running_s.toFixed(0)} s</td></tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
