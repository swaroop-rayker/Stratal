// SCR-13 Overview (approved hi-fi, frames A and B): resource history with the jobs strip,
// alerts with their actions (MON-2.6, MON-2.7), diagnostics (MON-3.2), loads and unloads
// (MON-3.3). Sources: ADR-0020 rule 13.
import { useState, type CSSProperties } from "react";
import { Link } from "react-router";

import {
  ALERT_NAMES,
  actionOf,
  alertValue,
  useAlerts,
  useDiagnostics,
  useModelEvents,
  useTelemetryRange,
  type DiagnosticLive,
  type MonitorEvent,
  type Range,
  type RangeJob,
  type SystemAlert,
} from "../../api/monitor";
import { Drawer } from "../../components/Drawer";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Icon, Skeleton, StatePill } from "../../components/ui";
import { clock, duration, stamp } from "../../lib/format";
import { LanesChart, type Lane } from "./Lanes";

const MB = 1024;

function num(m: Record<string, unknown> | null | undefined, key: string): number | null {
  const v = m?.[key];
  return typeof v === "number" ? v : null;
}

function hhmm(ms: number, span: number): string {
  const d = new Date(ms);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (span <= 36 * 3600_000) return time;
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

function pct(fromMs: number, toMs: number, ms: number): number {
  return Math.min(100, Math.max(0, ((ms - fromMs) / (toMs - fromMs)) * 100));
}

function JobsStrip({ jobs, fromMs, toMs, loads, alerts, onZoom }: {
  jobs: RangeJob[];
  fromMs: number;
  toMs: number;
  loads: MonitorEvent[];
  alerts: SystemAlert[];
  onZoom: (job: RangeJob) => void;
}) {
  return (
    <div className="sys-strip">
      {jobs.flatMap((job) => {
        const stages = job.stages.filter((s) => s.started_at);
        const longest = stages.reduce<string | null>((best, s) => {
          const len = (Date.parse(s.finished_at ?? new Date(toMs).toISOString()) - Date.parse(s.started_at ?? "")) || 0;
          const bestStage = stages.find((x) => x.name === best);
          const bestLen = bestStage ? Date.parse(bestStage.finished_at ?? new Date(toMs).toISOString()) - Date.parse(bestStage.started_at ?? "") : -1;
          return len > bestLen ? s.name : best;
        }, null);
        return stages.map((s) => {
          const start = Date.parse(s.started_at ?? "");
          const end = s.finished_at ? Date.parse(s.finished_at) : toMs;
          const left = pct(fromMs, toMs, start);
          const width = Math.max(0.3, pct(fromMs, toMs, end) - left);
          const state = s.status === "failed" ? "failed" : s.status === "running" ? "running" : s.status === "paused" ? "paused" : "completed";
          return (
            <button
              type="button"
              key={`${job.job_id}-${s.name}`}
              className="sys-band"
              style={{ left: `${left}%`, width: `${width}%`, "--c": `var(--state-${state})` } as CSSProperties}
              title={`${job.job_id} · ${s.name} ${clock(s.started_at)}–${s.finished_at ? clock(s.finished_at) : "now"}. Click to zoom to the job.`}
              onClick={() => { onZoom(job); }}
            >
              {s.name === longest ? (
                <>
                  <span className="mono">{job.job_id}</span> · {s.name}
                </>
              ) : null}
            </button>
          );
        });
      })}
      {loads.map((e) => (
        <span
          key={e.event_uid}
          className="sys-mk"
          tabIndex={0}
          style={{ left: `${pct(fromMs, toMs, Date.parse(e.ts))}%` }}
          title={`${clock(e.ts)} ${e.message}`}
          aria-label={`${clock(e.ts)} ${e.message}`}
        />
      ))}
      {alerts.map((a) => (
        <span
          key={a.fired.event_uid}
          className="sys-amk"
          tabIndex={0}
          style={{ left: `${pct(fromMs, toMs, Date.parse(a.fired.ts))}%` }}
          title={`${clock(a.fired.ts)} ${a.fired.message}`}
          aria-label={`${clock(a.fired.ts)} ${a.fired.message}`}
        >
          <Icon name="alert" />
        </span>
      ))}
    </div>
  );
}

function History({ range, onZoom }: { range: Range; onZoom: (job: RangeJob) => void }) {
  const telemetry = useTelemetryRange(range);
  const alerts = useAlerts(range);
  const models = useModelEvents(range);
  const [table, setTable] = useState(false);
  if (telemetry.isError) return <ErrorState error={telemetry.error} title="The resource history could not be loaded" onRetry={() => void telemetry.refetch()} />;
  if (!telemetry.data) {
    return (
      <section className="card" aria-busy="true">
        <Skeleton width={180} height={16} />
        <Skeleton width={900} height={300} />
      </section>
    );
  }
  const data = telemetry.data;
  const fromMs = Date.parse(data.from);
  const toMs = Date.parse(data.to);
  const points = data.points;
  const t = (p: (typeof points)[number]) => Date.parse(p.ts);
  const series = (f: (p: (typeof points)[number]) => number | null | undefined): [number, number | null][] =>
    points.map((p) => [t(p), f(p) ?? null]);
  const max = (f: (p: (typeof points)[number]) => number | null | undefined) =>
    points.reduce<number | null>((m, p) => {
      const v = f(p);
      return v === null || v === undefined ? m : m === null ? v : Math.max(m, v);
    }, null);
  const ramTotal = (max((p) => p.ram_total_mb) ?? 10 * MB) / MB;
  const vramTotal = (max((p) => p.vram_total_mb) ?? 6 * MB) / MB;
  const th = alerts.data?.thresholds;
  const averaged = points.some((p) => p.minute_average);
  const loads = models.data?.items ?? [];
  const fired = alerts.data?.items ?? [];
  const v = (n: number | null, d = 0) => (n === null ? "—" : n.toFixed(d));
  const lanes: Lane[] = [
    { key: "cpu", token: "--res-cpu", label: "CPU", sub: `machine · max ${v(max((p) => p.cpu_percent))} %`, min: 0, max: 100, unit: " %", digits: 0, series: [{ name: "", points: series((p) => p.cpu_percent) }] },
    {
      key: "ram", token: "--res-ram", label: "RAM", sub: `max ${v((max((p) => p.ram_used_mb) ?? 0) / MB, 1)} / ${ramTotal.toFixed(1)} GB`, min: 0, max: ramTotal, unit: " GB", digits: 1,
      thresholds: th ? [{ value: (ramTotal * th.ram_percent) / 100, label: `${th.ram_percent.toFixed(0)} %` }] : [],
      series: [{ name: "", points: series((p) => (p.ram_used_mb === null ? null : p.ram_used_mb / MB)) }],
    },
    { key: "gpu", token: "--res-gpu", label: "GPU", info: "NVML's GPU utilisation is the share of time a kernel was running, not how much of the GPU it used.", sub: `NVML · max ${v(max((p) => p.gpu_util_percent))} %`, min: 0, max: 100, unit: " %", digits: 0, series: [{ name: "", points: series((p) => p.gpu_util_percent) }] },
    {
      key: "vram", token: "--res-vram", label: "VRAM", sub: `card · max ${v((max((p) => p.vram_used_mb) ?? 0) / MB, 1)} / ${vramTotal.toFixed(1)} GB`, min: 0, max: vramTotal, unit: " GB", digits: 1,
      thresholds: th ? [{ value: (vramTotal * th.vram_percent) / 100, label: `${th.vram_percent.toFixed(0)} %` }] : [],
      series: [{ name: "", points: series((p) => (p.vram_used_mb === null ? null : p.vram_used_mb / MB)) }],
    },
    {
      key: "temp", token: "--res-temp", label: "GPU temp", sub: `°C · max ${v(max((p) => p.gpu_temp_c))}`, min: 30, max: 90, unit: " °C", digits: 0,
      thresholds: th ? [{ value: th.pause_c, label: `pause ${th.pause_c.toFixed(0)}` }, { value: th.resume_c, label: `resume ${th.resume_c.toFixed(0)}` }] : [],
      series: [{ name: "", points: series((p) => p.gpu_temp_c) }],
    },
  ];
  const span = toMs - fromMs;
  return (
    <section className="card">
      <h2>
        Resource history
        <span className="sub2">
          {points.length ? `drawn as ${duration(data.bucket_s)} means` : "no samples in this range"}
          {averaged ? " · one-minute averages beyond 7 days" : ""}
        </span>
        <span className="spacer" />
        <span className="group-btns" role="group" aria-label="View">
          <button type="button" className="btn sm" aria-pressed={!table} onClick={() => { setTable(false); }}>Chart</button>
          <button type="button" className="btn sm" aria-pressed={table} onClick={() => { setTable(true); }}>Table</button>
        </span>
      </h2>
      {table ? (
        <div className="table-wrap sys-scroll">
          <table className="t dense">
            <caption className="sr-only">Resource means per time bucket</caption>
            <thead><tr><th>Time</th><th className="r">CPU %</th><th className="r">RAM GB</th><th className="r">GPU %</th><th className="r">VRAM GB</th><th className="r">Temp °C</th></tr></thead>
            <tbody>
              {points.slice(-200).reverse().map((p) => (
                <tr key={p.ts}>
                  <td className="when">{stamp(p.ts)}{p.minute_average ? " · 1-min" : ""}</td>
                  <td className="r">{v(p.cpu_percent)}</td>
                  <td className="r">{p.ram_used_mb === null ? "—" : (p.ram_used_mb / MB).toFixed(1)}</td>
                  <td className="r">{v(p.gpu_util_percent)}</td>
                  <td className="r">{p.vram_used_mb === null ? "—" : (p.vram_used_mb / MB).toFixed(1)}</td>
                  <td className="r">{v(p.gpu_temp_c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="sys-lanes">
          <div className="lab">Jobs<small>{data.jobs.length} in range</small></div>
          <JobsStrip jobs={data.jobs} fromMs={fromMs} toMs={toMs} loads={loads} alerts={fired} onZoom={onZoom} />
          <div className="labs">
            {lanes.map((lane) => (
              <div key={lane.key} className="lab" style={{ "--res": `var(${lane.token})` } as CSSProperties}>
                <i />
                {lane.label}
                {lane.info ? <span title={lane.info} aria-label={lane.info} tabIndex={0}><Icon name="info" className="sys-info" /></span> : null}
                <small>{lane.sub}</small>
              </div>
            ))}
          </div>
          <div role="img" aria-label={`Resource lanes from ${stamp(data.from)} to ${stamp(data.to)}: ${lanes.map((l) => `${l.label} ${l.sub}`).join("; ")}`}>
            <LanesChart lanes={lanes} x="time" min={fromMs} max={toMs} xLabel={(ms) => hhmm(ms, span)} tipTitle={(ms) => stamp(new Date(ms).toISOString())} />
          </div>
        </div>
      )}
      <div className="sys-thnote">
        <span><span className="dash" />thresholds from Settings: RAM {th?.ram_percent ?? 85} %, VRAM {th?.vram_percent ?? 90} %, pause {th?.pause_c ?? 83} °C and resume {th?.resume_c ?? 75} °C</span>
        <span>◆ model loaded or unloaded</span>
        <span>Click a job's band to zoom to it</span>
      </div>
    </section>
  );
}

function AlertsCard({ range, selected, onOpen }: { range: Range; selected: string | null; onOpen: (a: SystemAlert) => void }) {
  const alerts = useAlerts(range);
  if (alerts.isError) return <section className="card"><ErrorState error={alerts.error} title="Alerts could not be loaded" onRetry={() => void alerts.refetch()} /></section>;
  const items = alerts.data?.items ?? [];
  const th = alerts.data?.thresholds;
  return (
    <section className="card" aria-busy={alerts.isPending}>
      <h2>Alerts<span className="sub2">{items.length ? `${items.length} in range` : ""}</span></h2>
      {alerts.isPending ? (
        <Skeleton width={400} height={80} />
      ) : items.length === 0 ? (
        <EmptyState icon="check" title="No alerts in this range">
          The thresholds are VRAM {th?.vram_percent ?? 90} %, RAM {th?.ram_percent ?? 85} % and {th?.pause_c ?? 83} °C (Settings).
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="t dense wrap">
            <caption className="sr-only">Alerts in the range</caption>
            <thead><tr><th>When</th><th>Alert</th><th className="r">Value</th><th>Job · stage</th><th>Action</th><th className="r">Lasted</th></tr></thead>
            <tbody aria-live="polite">
              {items.map((a) => {
                const act = actionOf(a);
                return (
                  <tr
                    key={a.fired.event_uid}
                    aria-selected={selected === a.fired.event_uid ? true : undefined}
                    tabIndex={0}
                    onClick={() => { onOpen(a); }}
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen(a); }}
                  >
                    <td className="when">{clock(a.fired.ts)}</td>
                    <td><span className="sys-akind"><Icon name="alert" />{ALERT_NAMES[a.code] ?? a.code}</span></td>
                    <td className="r">{alertValue(a)}</td>
                    <td className={a.fired.job_id ? "id" : "muted2"}>{a.fired.job_id ? `${a.fired.job_id}${a.fired.stage ? ` · ${a.fired.stage}` : ""}` : "no job"}</td>
                    <td>
                      {act.state ? <StatePill state={act.state} /> : <span className="muted2">{a.firing ? "warned · firing" : "warned only"}</span>}
                      {act.resumedAt ? <span className="muted2 sys-small"> resumed {clock(act.resumedAt)}</span> : null}
                    </td>
                    <td className="r">{a.firing ? "firing" : duration(a.lasted_s)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="note2">An alert fires after 5 s over its threshold, once per crossing. VRAM and RAM re-arm 5 points below; temperature re-arms at the resume limit.</p>
    </section>
  );
}

function Bar({ label, value, share, mark, token, text }: { label: string; value: number | null; share: number; mark: number; token?: string; text: string }) {
  return (
    <div className="sys-obs" style={token ? ({ "--res": `var(${token})` } as CSSProperties) : undefined}>
      <span>{label}</span>
      <span className="sys-obar" role="img" aria-label={`${label}: ${text}`}>
        <i style={{ width: `${Math.min(100, Math.max(0, share * 100))}%` }} />
        <s style={{ left: `${Math.min(100, mark * 100)}%` }} />
      </span>
      <span className="v">{value === null ? "—" : text}</span>
    </div>
  );
}

function LiveDiagnosis({ d }: { d: DiagnosticLive }) {
  const o = d.observed;
  const gpu = o.gpu_percent;
  const capacity = o.capacity_cores;
  return (
    <div className="sys-dg">
      <div className="who">
        <StatePill state="running" />
        <Link className="mono" to={`/jobs/${d.job_id}`}>{d.job_id} · {d.stage}</Link>
        <span className="spacer" />
        <Badge>expected {d.expected.join(", ").toUpperCase()}</Badge>
      </div>
      <Bar label="GPU" token="--res-gpu" value={gpu} share={(gpu ?? 0) / 100} mark={d.thresholds.gpu_low_percent / 100} text={`${(gpu ?? 0).toFixed(0)} %${gpu !== null && gpu < d.thresholds.gpu_low_percent ? ` · under ${d.thresholds.gpu_low_percent.toFixed(0)}` : ""}`} />
      <Bar label="Job CPU" token="--res-cpu" value={o.cpu_cores} share={(o.cpu_cores ?? 0) / capacity} mark={d.thresholds.cpu_saturated_share} text={`${(o.cpu_cores ?? 0).toFixed(2)} of ${capacity.toFixed(0)} core${capacity === 1 ? "" : "s"}`} />
      <Bar label="Disk read" value={o.disk_read_mb_s} share={o.disk_read_mb_s / (d.thresholds.disk_mb_s * 2)} mark={0.5} text={`${o.disk_read_mb_s.toFixed(0)} MB/s · of ${d.thresholds.disk_mb_s.toFixed(0)}`} />
      {d.diagnosis ? (
        <div className="callout warn"><Icon name="alert" /><span><b>{d.diagnosis.message}.</b> {d.diagnosis.suggested_action}</span></div>
      ) : d.queue === "gpu" ? (
        o.samples < 20 || d.running_for_s < 60 ? (
          <div className="sys-okline muted2"><Icon name="clock" />Warming up: diagnoses start after a minute of running.</div>
        ) : (
          <div className="sys-okline"><Icon name="check" />As expected: the GPU is busy.</div>
        )
      ) : (
        <div className="sys-okline muted2"><Icon name="info" />A CPU stage: shown, not diagnosed (ADR-0020 rule 7).</div>
      )}
    </div>
  );
}

function DiagnosticsCard({ range }: { range: Range }) {
  const diags = useDiagnostics(range);
  if (diags.isError) return <section className="card"><ErrorState error={diags.error} title="Diagnostics could not be loaded" onRetry={() => void diags.refetch()} /></section>;
  const live = diags.data?.live ?? [];
  const items = diags.data?.items ?? [];
  return (
    <section className="card" aria-busy={diags.isPending}>
      <h2>Diagnostics</h2>
      <div className="sys-sect">Now · last 30 s</div>
      {diags.isPending ? <Skeleton width={360} height={90} /> : live.length ? live.map((d) => <LiveDiagnosis key={`${d.job_id}-${d.stage}`} d={d} />) : <p className="note2">No stage is running.</p>}
      <div className="sys-sect">Earlier</div>
      {items.length ? (
        <div className="table-wrap">
          <table className="t dense wrap">
            <caption className="sr-only">Earlier diagnoses</caption>
            <thead><tr><th>When</th><th>Job · stage</th><th>Diagnosis</th><th className="r">Lasted</th></tr></thead>
            <tbody aria-live="polite">
              {items.map((i) => (
                <tr key={i.fired.event_uid}>
                  <td className="when">{clock(i.fired.ts)}</td>
                  <td className="id">{i.fired.job_id} · {i.fired.stage}</td>
                  <td>{i.fired.message}</td>
                  <td className="r">{i.cleared ? duration(i.lasted_s) : "ongoing"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="note2">No diagnoses in this range.</p>
      )}
    </section>
  );
}

function LoadsCard({ range }: { range: Range }) {
  const models = useModelEvents(range);
  if (models.isError) return <section className="card"><ErrorState error={models.error} title="Loads and unloads could not be loaded" onRetry={() => void models.refetch()} /></section>;
  const items = models.data?.items ?? [];
  const fig = (m: Record<string, unknown> | null, a: string, b: string, d: number) => {
    const x = num(m, a);
    const y = num(m, b);
    return x === null || y === null ? "—" : (
      <span className="sys-fig">{(x / MB).toFixed(d)} <span className="arrow">→</span> {(y / MB).toFixed(d)} GB</span>
    );
  };
  return (
    <section className="card" aria-busy={models.isPending}>
      <h2>Loads and unloads<span className="sub2">PyTorch first, the card beside it</span></h2>
      {models.isPending ? (
        <Skeleton width={600} height={60} />
      ) : items.length === 0 ? (
        <p className="note2">No model was loaded or unloaded in this range.</p>
      ) : (
        <div className="table-wrap">
          <table className="t dense">
            <caption className="sr-only">Model loads and unloads in the range</caption>
            <thead><tr><th>When</th><th>Event</th><th>Model</th><th>Job · stage</th><th className="r">PyTorch allocated</th><th className="r">Card (driver)</th><th className="r">Took</th></tr></thead>
            <tbody aria-live="polite">
              {items.map((e) => {
                const m = e.metrics;
                const loaded = e.type === "model_loaded";
                const model = /^(?:Loaded|Unloaded) (.+?)(?: to GPU|:|,)/.exec(e.message)?.[1] ?? "—";
                const reserved = `reserved ${((num(m, "reserved_mb_before") ?? 0) / MB).toFixed(1)} → ${((num(m, "reserved_mb_after") ?? 0) / MB).toFixed(1)} GB`;
                return (
                  <tr key={e.event_uid} title={reserved}>
                    <td className="when">{clock(e.ts)}</td>
                    <td><span className={loaded ? "sys-ev up" : "sys-ev"}><Icon name="down" />{loaded ? "loaded" : "unloaded"}</span></td>
                    <td>{model}</td>
                    <td className={e.job_id ? "id" : "muted2"}>{e.job_id ? `${e.job_id} · ${e.stage ?? ""}` : (e.stage ?? "service")}</td>
                    <td className="r">{fig(m, "allocated_mb_before", "allocated_mb_after", 2)}</td>
                    <td className="r">{fig(m, "card_used_mb_before", "card_used_mb_after", 1)}</td>
                    <td className="r">{num(m, "load_s") === null ? "" : `${(num(m, "load_s") ?? 0).toFixed(1)} s`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="note2">A row's tooltip gives PyTorch's reserved memory: its gap from allocated is the caching allocator (MON-2.4). The card's figure includes other processes.</p>
    </section>
  );
}

const CAUSED_ICON: Record<string, "pause" | "play" | "check" | "x" | "alert"> = {
  thermal_pause: "pause",
  thermal_resume: "play",
  stalled: "alert",
};

function AlertDrawer({ alert, onClose }: { alert: SystemAlert; onClose: () => void }) {
  const m = (alert.fired.metrics ?? {}) as Record<string, unknown>;
  const timeline = [...alert.actions, ...(alert.cleared ? [alert.cleared] : [])].sort((a, b) => a.ts.localeCompare(b.ts) || a.id - b.id);
  return (
    <Drawer
      open
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={`Alert: ${ALERT_NAMES[alert.code] ?? alert.code}`}
      width={470}
      className="sys-drawer"
      heading={
        <h2 className="sys-dhead">
          <Badge tone="warn"><Icon name="alert" />{ALERT_NAMES[alert.code] ?? alert.code}</Badge>
          {alert.firing ? <span className="pill warn"><Icon name="alert" />firing</span> : <span className="pill completed"><Icon name="check" />cleared</span>}
        </h2>
      }
    >
      <div className="kv">
        <span>Fired</span><span className="mono">{stamp(alert.fired.ts)}</span>
        <span>Value</span><span className="mono">{alertValue(alert)}{alert.code !== "no_progress" ? ` for 5 s · limit ${typeof m["threshold"] === "number" ? String(m["threshold"]) : "—"}` : ""}</span>
        <span>Job · stage</span><span className="mono">{alert.fired.job_id ? `${alert.fired.job_id} · ${alert.fired.stage ?? ""}` : "no job"}</span>
        <span>Reason</span><span>{alert.fired.reason}</span>
      </div>
      <div className="sys-sect">What it caused</div>
      {timeline.length ? (
        <div className="sys-tl">
          {timeline.map((e) => {
            const raw = e.metrics?.["code"];
            const code = typeof raw === "string" ? raw : "";
            const cleared = e === alert.cleared;
            const icon = cleared ? "check" : e.type === "stage_failed" ? "x" : (CAUSED_ICON[code] ?? "alert");
            return (
              <div key={e.event_uid}>
                <Icon name={icon} />
                <span className="when">{clock(e.ts)} · {cleared ? "cleared" : e.type === "stage_failed" ? "failed" : "action"}</span>
                {e.message}
                {e.type === "stage_failed" && e.suggested_action ? <span className="muted2"> ({e.suggested_action})</span> : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="note2">Nothing yet: VRAM and RAM alerts warn only. A failure out of memory soon after would show here.</p>
      )}
      <div className="callout info"><Icon name="info" /><span>{alert.fired.suggested_action}</span></div>
      {alert.fired.job_id ? (
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          <Link className="btn sm" to={`/jobs/${alert.fired.job_id}`}><Icon name="list" />Open {alert.fired.job_id}'s log</Link>
        </div>
      ) : null}
    </Drawer>
  );
}

export function Overview({ range, onZoom }: { range: Range; onZoom: (job: RangeJob) => void }) {
  const [open, setOpen] = useState<SystemAlert | null>(null);
  return (
    <>
      <History range={range} onZoom={onZoom} />
      <div className="sys-cols2">
        <AlertsCard range={range} selected={open?.fired.event_uid ?? null} onOpen={setOpen} />
        <DiagnosticsCard range={range} />
      </div>
      <LoadsCard range={range} />
      {open ? <AlertDrawer alert={open} onClose={() => { setOpen(null); }} /> : null}
    </>
  );
}
