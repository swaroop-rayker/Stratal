// SCR-13 History (approved hi-fi SCR-13history_v1, 2026-11-24; MON-4.5): the last N runs of
// one stage in one group of like runs, one point per run on an evenly spaced axis, a lane per
// figure with the group's median and the thresholds dashed, tags on unusual runs, and the
// runs as a table. *All groups* puts every run against its own group's median.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState, type CSSProperties } from "react";
import { Link } from "react-router";

import { api, call, type StreamEvent } from "../../api/client";
import { useAlerts, useHistory, type HistoryRun, type SystemHistory } from "../../api/monitor";
import { useFeedMessage } from "../../api/stream";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Skeleton } from "../../components/ui";

const MB = 1024;
const NS = [10, 20, 50, 100];
const SHAPES = ["", "o", "sq", "dia", "tri", "sq o", "dia o"];
/** A group's marker in *All groups*: a shape and a categorical colour, so no two groups differ by colour alone. */
function mark(index: number): { shape: string; color: string } {
  return { shape: SHAPES[index % SHAPES.length] ?? "", color: `var(--cat-${String((index % 8) + 1)})` };
}
const PAD_L = 3; // % of the plot kept free on the left
const PAD_R = 15; // and on the right, where the median and threshold labels sit
const TOP = 6;
const SPAN = 52; // px of the 64 px lane the values use

export const NOTE_WORDS: Record<string, string> = {
  paused: "paused",
  resumed: "worker lost, resumed",
  fine_tune: "fine-tune",
  pruned: "pruned",
  reused_work: "reused",
  peaks_missing: "peaks unavailable",
};

interface LaneDef {
  key: string;
  token: string;
  label: string;
  sub: string;
  values: (number | null)[];
  fmt: (v: number) => string;
  thresholds: { value: number; label: string }[];
  tags?: (string | null)[];
  /** A run with no value is drawn hollow at the foot (a rate: no work done); otherwise it leaves a gap. */
  hollowMissing?: boolean;
}

function median(values: (number | null)[]): number | null {
  const kept = values.filter((v): v is number => v !== null).sort((a, b) => a - b);
  if (kept.length === 0) return null;
  const mid = Math.floor(kept.length / 2);
  return kept.length % 2 ? (kept[mid] ?? null) : ((kept[mid - 1] ?? 0) + (kept[mid] ?? 0)) / 2;
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

function rateText(v: number, unit: string | null): string {
  if (unit === "samples/s") return Math.round(v).toLocaleString("en-GB");
  return `${v.toFixed(1)}/s`;
}

function timeText(v: number, unit: string): string {
  if (unit === "s/epoch" || v < 100) return `${v.toFixed(1)} s`;
  return `${Math.round(v).toLocaleString("en-GB")} s`;
}

const gb = (mb: number) => `${(mb / MB).toFixed(2)} GB`;

function xOf(i: number, n: number): number {
  return n === 1 ? 50 : PAD_L + (i * (100 - PAD_L - PAD_R)) / (n - 1);
}

function scale(values: (number | null)[], thresholds: { value: number }[], relative: boolean): [number, number] {
  if (relative) return [70, 130];
  const all = [...values.filter((v): v is number => v !== null), ...thresholds.map((t) => t.value)];
  const top = Math.max(1e-9, ...all) * 1.25;
  return [0, top];
}

function Lane({ lane, runs, relative, marks, hover, onHover }: {
  lane: LaneDef;
  runs: HistoryRun[];
  relative: boolean;
  marks: ({ shape: string; color: string } | null)[];
  hover: string | null;
  onHover: (id: string | null) => void;
}) {
  const [min, max] = scale(lane.values, lane.thresholds, relative);
  const y = (v: number) => TOP + (1 - (Math.min(Math.max(v, min), max) - min) / (max - min)) * SPAN;
  const mid = relative ? 100 : median(lane.values);
  const n = runs.length;
  const line = lane.values
    .map((v, i) => (v === null ? null : `${xOf(i, n).toFixed(2)},${y(v).toFixed(2)}`))
    .filter((p): p is string => p !== null)
    .join(" ");
  const style = { "--res": `var(${lane.token})` } as CSSProperties;
  const last = lane.values[lane.values.length - 1] ?? null;
  const caption = [mid === null ? null : `median ${lane.fmt(mid)}`, last === null ? null : `latest ${lane.fmt(last)}`].filter(Boolean).join(" · ");
  return (
    <>
      <div className="lab" style={style}>
        <i /><span>{lane.label}</span><small>{lane.sub}</small>
        <span className="sys-fig2">{relative ? "100 % = the group's median" : caption}</span>
      </div>
      <div className="sys-hplot" style={style} role="img" aria-label={`${lane.label}: ${String(n)} runs${mid === null ? "" : `, median ${lane.fmt(mid)}`}`}>
        <svg viewBox="0 0 100 64" preserveAspectRatio="none" aria-hidden="true">
          {lane.thresholds.map((t) => <line key={t.label} className="th" x1="0" x2="100" y1={y(t.value)} y2={y(t.value)} />)}
          {mid !== null ? <line className="med" x1="0" x2="100" y1={y(mid)} y2={y(mid)} /> : null}
          {!relative && line ? <polyline points={line} /> : null}
        </svg>
        {lane.thresholds.map((t) => {
          const lowest = lane.thresholds.length > 1 && t.value === Math.min(...lane.thresholds.map((x) => x.value));
          return <span key={t.label} className={lowest ? "sys-thl below" : "sys-thl"} style={{ top: y(t.value) }}>{t.label}</span>;
        })}
        {mid !== null && !lane.thresholds.some((t) => Math.abs(y(t.value) - y(mid)) < 16) ? <span className="sys-medl" style={{ top: y(mid) }}>{relative ? "100 %" : `median ${lane.fmt(mid)}`}</span> : null}
        {runs.map((run, i) => {
          const v = lane.values[i] ?? null;
          if (v === null && !lane.hollowMissing) return null;
          const left = `${xOf(i, n).toFixed(2)}%`;
          const top = v === null ? TOP + SPAN : y(v);
          const m = marks[i] ?? null;
          const cls = ["sys-pt", m?.shape ?? "", v === null ? "o" : "", i === n - 1 ? "last" : "", hover === run.job_id ? "on" : ""].filter(Boolean).join(" ");
          const tag = relative ? null : (lane.tags?.[i] ?? null);
          return (
            <span key={run.job_id}>
              <Link
                to={`/jobs/${run.job_id}`}
                className={cls}
                tabIndex={-1}
                style={{ left, top, ...(m ? ({ "--res": m.color } as CSSProperties) : {}) }}
                aria-label={`${run.job_id}, ${when(run.started_at)}: ${v === null ? "no value" : lane.fmt(v)}`}
                onMouseEnter={() => { onHover(run.job_id); }}
                onMouseLeave={() => { onHover(null); }}
                onFocus={() => { onHover(run.job_id); }}
                onBlur={() => { onHover(null); }}
              />
              {tag ? <span className={["sys-tagl", top > 20 ? "up" : xOf(i, n) > 55 ? "left" : ""].filter(Boolean).join(" ")} style={{ left, top }}>{tag}</span> : null}
            </span>
          );
        })}
      </div>
    </>
  );
}

function lanesOf(data: SystemHistory, runs: HistoryRun[], relative: boolean, limits: { ramMb: number | null; pause: number; resume: number }): LaneDef[] {
  const training = data.time_unit === "s/epoch";
  const groupOf = (r: HistoryRun) => r.group;
  const rel = (values: (number | null)[]): (number | null)[] => {
    if (!relative) return values;
    const byGroup = new Map<string, (number | null)[]>();
    runs.forEach((r, i) => { byGroup.set(groupOf(r), [...(byGroup.get(groupOf(r)) ?? []), values[i] ?? null]); });
    return values.map((v, i) => {
      const run = runs[i];
      const m = run ? median(byGroup.get(groupOf(run)) ?? []) : null;
      return v === null || !m ? null : (v / m) * 100;
    });
  };
  const pct = (v: number) => `${v.toFixed(0)} %`;
  const lanes: LaneDef[] = [];
  const tagOf = (r: HistoryRun) => {
    const words = r.notes.filter((n) => n !== "reused_work" && n !== "peaks_missing").map((n) => NOTE_WORDS[n] ?? n);
    return words.length ? words.join(", ") : null;
  };
  if (data.rate_unit) {
    lanes.push({
      key: "rate", token: "--cat-1", label: relative ? "Rate, against its group" : "Rate",
      sub: relative ? "% of the group's median" : data.rate_unit === "samples/s" ? "samples/s while training" : data.rate_unit,
      values: rel(runs.map((r) => r.rate)),
      fmt: relative ? pct : (v) => rateText(v, data.rate_unit), thresholds: [],
      tags: runs.map((r) => (r.rate === null && !r.reused ? "reused" : null)),
      hollowMissing: true,
    });
  }
  lanes.push({
    key: "time", token: "--cat-1", label: training ? "Time per epoch" : "Running time",
    sub: relative ? "% of the group's median" : training ? "s, running time ÷ epochs" : "s, running time across attempts",
    values: rel(runs.map((r) => r.time)),
    fmt: relative ? pct : (v) => timeText(v, data.time_unit), thresholds: [],
    tags: runs.map((r) => {
      const t = tagOf(r);
      return t && r.time !== null && !relative ? `${timeText(r.time, data.time_unit)} · ${t}` : t;
    }),
  });
  const peak = (key: "vram_used_mb" | "torch_allocated_mb" | "ram_used_mb" | "gpu_temp_c") => runs.map((r) => r.peaks[key]);
  const vram = peak("vram_used_mb").map((v) => (v === null ? null : v / MB));
  if (vram.some((v) => v !== null)) {
    lanes.push({ key: "vram", token: "--res-vram", label: "VRAM peak", sub: "card · GB of 6.0", values: rel(vram), fmt: relative ? pct : (v) => `${v.toFixed(2)} GB`, thresholds: relative ? [] : [{ value: 5.5, label: "NFR-1 5.5 GB" }] });
  }
  const torch = peak("torch_allocated_mb");
  if (torch.some((v) => v !== null)) {
    lanes.push({ key: "torch", token: "--res-vram", label: "PyTorch allocated", sub: "peak · MB", values: rel(torch), fmt: relative ? pct : (v) => `${v.toFixed(0)} MB`, thresholds: [] });
  }
  const ram = peak("ram_used_mb").map((v) => (v === null ? null : v / MB));
  if (ram.some((v) => v !== null)) {
    const total = limits.ramMb ? limits.ramMb / MB : null;
    lanes.push({ key: "ram", token: "--res-ram", label: "RAM peak", sub: `the VM, everything running${total ? ` · GB of ${total.toFixed(1)}` : ""}`, values: rel(ram), fmt: relative ? pct : (v) => `${v.toFixed(2)} GB`, thresholds: !relative && total ? [{ value: total * 0.85, label: "85 %" }] : [] });
  }
  const temp = peak("gpu_temp_c");
  if (temp.some((v) => v !== null)) {
    lanes.push({ key: "temp", token: "--res-temp", label: "GPU temp", sub: "max · °C", values: rel(temp), fmt: relative ? pct : (v) => `${v.toFixed(0)} °C`, thresholds: relative ? [] : [{ value: limits.pause, label: `pause ${limits.pause.toFixed(0)}` }, { value: limits.resume, label: `resume ${limits.resume.toFixed(0)}` }] });
  }
  return lanes;
}

function Axis({ runs }: { runs: HistoryRun[] }) {
  const n = runs.length;
  const dayOf = (r: HistoryRun) => (r.started_at ? new Date(r.started_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "");
  // A day is labelled where it starts, unless that is too close to the previous label.
  const shown = runs.map(dayOf).reduce<{ day: string; x: number }[]>((acc, day, i) => {
    const prev = acc[acc.length - 1];
    if (day && day !== prev?.day && (!prev || xOf(i, n) - prev.x >= 8)) acc.push({ day, x: xOf(i, n) });
    return acc;
  }, []);
  return (
    <>
      <div className="sys-xaxis">{runs.map((r, i) => <span key={r.job_id} style={{ left: `${xOf(i, n).toFixed(2)}%` }}>{r.job_id.replace(/^job_/, "")}</span>)}</div>
      <div className="sys-xaxis">
        {shown.map((d) => <span key={d.day} style={{ left: `${d.x.toFixed(2)}%` }}>{d.day}</span>)}
      </div>
    </>
  );
}

export function History({ stage, queue, group, n, reused, onChange, onCompare }: {
  stage: string | null;
  queue: string | null;
  group: string | null;
  n: number;
  reused: boolean;
  onChange: (next: Record<string, string | null>) => void;
  onCompare: (a: string, b: string) => void;
}) {
  const history = useHistory({ stage, queue, group, n, reused });
  const alerts = useAlerts({ kind: "preset", minutes: 60 });
  const latest = useQuery({ queryKey: ["monitor", "latest"], queryFn: () => call(() => api.GET("/api/v1/telemetry/latest")) });
  const client = useQueryClient();
  const [hover, setHover] = useState<string | null>(null);
  const [ticked, setTicked] = useState<string[]>([]);
  const [chart, setChart] = useState(true);
  const onEvent = useCallback((event: StreamEvent) => {
    if (event.type === "stage_completed") void client.invalidateQueries({ queryKey: ["monitor", "history"] });
  }, [client]);
  useFeedMessage("event", onEvent);

  if (history.isError) return <section className="card"><ErrorState error={history.error} title="The history could not be loaded" onRetry={() => void history.refetch()} /></section>;
  const data = history.data;
  if (!data) return <section className="card" aria-busy="true"><Skeleton width={260} height={16} /><Skeleton width={900} height={64} /><Skeleton width={900} height={64} /></section>;
  if (data.stages.length === 0) return <section className="card"><EmptyState icon="chart" title="No job has completed yet">Runs appear here as jobs finish, one lane per figure.</EmptyState></section>;

  const all = data.group === null;
  const runs = [...data.runs].reverse(); // oldest left
  const groupKeys = data.groups.map((g) => g.key);
  const marks = runs.map((r) => (all ? mark(groupKeys.indexOf(r.group)) : null));
  const th = alerts.data?.thresholds;
  const limits = { ramMb: latest.data?.ram_total_mb ?? null, pause: th?.pause_c ?? 83, resume: th?.resume_c ?? 75 };
  const lanes = lanesOf(data, runs, all, limits);
  const current = data.stages.find((s) => s.stage === data.stage && s.queue === data.queue);
  const groupLabel = data.groups.find((g) => g.key === data.group)?.label;
  const training = data.time_unit === "s/epoch";
  const isLabel = data.stage === "label";
  const toggle = (id: string) => { setTicked((t) => (t.includes(id) ? t.filter((x) => x !== id) : [...t, id].slice(-2))); };
  const pair = ticked.length === 2 ? [...ticked].sort((a, b) => runs.findIndex((r) => r.job_id === a) - runs.findIndex((r) => r.job_id === b)) : null;

  return (
    <>
      <div className="sys-hctl">
        <label>Stage{" "}
          <select className="input mono" aria-label="Stage" value={`${data.stage ?? ""}|${data.queue ?? ""}`} onChange={(e) => { const [s, q] = e.target.value.split("|"); setTicked([]); onChange({ stage: s ?? null, queue: q ?? null, group: null }); }}>
            {data.stages.map((s) => <option key={`${s.stage}|${s.queue}`} value={`${s.stage}|${s.queue}`}>{s.stage} · {s.queue.toUpperCase()} · {s.runs} runs</option>)}
          </select>
        </label>
        <label>Group{" "}
          <select className="input mono" aria-label="Group" value={data.group ?? "all"} onChange={(e) => { setTicked([]); onChange({ group: e.target.value }); }}>
            {data.groups.map((g) => <option key={g.key} value={g.key}>{g.label} · {g.runs} runs</option>)}
            <option value="all">All groups · {data.groups.reduce((t, g) => t + g.runs, 0)} runs</option>
          </select>
        </label>
        <span>Last</span>
        <span className="group-btns" role="group" aria-label="Last runs">
          {NS.map((k) => <button key={k} type="button" className="btn sm" aria-pressed={n === k} onClick={() => { onChange({ n: k === 20 ? null : String(k) }); }}>{k}</button>)}
        </span>
        <span>runs</span>
        <span className="spacer" />
        <label className="sys-cb" title={training ? "Training never reuses a run" : undefined}>
          <input type="checkbox" checked={reused} disabled={training} onChange={(e) => { onChange({ reused: e.target.checked ? "1" : null }); }} />
          Include stages reused from the cache{current?.reused ? ` (${String(current.reused)})` : ""}
        </label>
      </div>

      {runs.length === 0 ? (
        <section className="card"><EmptyState icon="chart" title="No runs in this group">{reused ? "Nothing matches." : "Every run was reused from the cache; tick the box to show them."}</EmptyState></section>
      ) : (
        <>
          <section className="card">
            <h2>
              Trends
              <span className="sub2">
                {all ? `the last ${String(runs.length)} runs, each against its group's median` : `${runs.length < n ? `all ${String(runs.length)} runs of this group` : `the last ${String(runs.length)} runs`} · oldest left · dashed: the group's median`}
              </span>
              <span className="spacer" />
              <span className="group-btns" role="group" aria-label="View">
                <button type="button" className="btn sm" aria-pressed={chart} onClick={() => { setChart(true); }}>Chart</button>
                <button type="button" className="btn sm" aria-pressed={!chart} onClick={() => { setChart(false); }}>Table only</button>
              </span>
            </h2>
            {runs.length === 1 ? <p className="note2">One run in this group so far: a trend needs two. Its figures are in the table.</p> : null}
            {all ? (
              <div className="sys-gkey">
                {data.groups.map((g, i) => (
                  <span key={g.key}><span className={`sys-pt ${mark(i).shape}`} style={{ "--res": mark(i).color } as CSSProperties} aria-hidden="true" /><span className="mono">{g.label}</span> {g.runs}</span>
                ))}
              </div>
            ) : null}
            {chart && runs.length > 1 ? (
              <div className="sys-hlanes">
                {lanes.map((lane) => <Lane key={lane.key} lane={lane} runs={runs} relative={all} marks={marks} hover={hover} onHover={setHover} />)}
                <div />
                <div><Axis runs={runs} /></div>
              </div>
            ) : null}
          </section>
          <div className="table-wrap">
            <table className="t dense wrap sys-htable">
              <caption className="sr-only">Runs of {data.stage}{groupLabel ? ` in ${groupLabel}` : ""}, newest first</caption>
              <thead>
                <tr>
                  <th aria-label="Select" />
                  <th>Run</th><th>Started</th>
                  {training ? <><th className="r">Epochs</th><th className="r">Time per epoch</th></> : isLabel ? <><th className="r">Images</th><th className="r">Captions computed</th><th className="r">Running time</th></> : <th className="r">Running time</th>}
                  {data.rate_unit ? <th className="r">Rate, {data.rate_unit}</th> : null}
                  <th className="r">VRAM, card</th><th className="r">PyTorch</th><th className="r">RAM, VM</th><th className="r">Temp</th><th className="r">Job RSS</th><th>Note</th>
                </tr>
              </thead>
              <tbody>
                {data.runs.map((r) => {
                  const note = [...(r.attempts > 1 ? [`${String(r.attempts)} attempts`] : []), ...r.notes.map((x) => NOTE_WORDS[x] ?? x)].join(" · ");
                  const p = r.peaks;
                  return (
                    <tr key={r.job_id} className={hover === r.job_id ? "is-hover" : undefined} onMouseEnter={() => { setHover(r.job_id); }} onMouseLeave={() => { setHover(null); }}>
                      <td className="cbx"><input type="checkbox" aria-label={`Select ${r.job_id}`} checked={ticked.includes(r.job_id)} onChange={() => { toggle(r.job_id); }} /></td>
                      <td className="id"><Link to={`/jobs/${r.job_id}`}>{r.job_id}</Link></td>
                      <td className="when">{when(r.started_at)}</td>
                      {training ? <><td className="r">{r.epochs ?? "—"}</td><td className="r">{r.time === null ? "—" : timeText(r.time, data.time_unit)}</td></> : isLabel ? <><td className="r">{r.items?.toLocaleString("en-GB") ?? "—"}</td><td className="r">{r.computed?.toLocaleString("en-GB") ?? "—"}</td><td className="r">{r.time === null ? "—" : timeText(r.time, data.time_unit)}</td></> : <td className="r">{r.time === null ? "—" : timeText(r.time, data.time_unit)}</td>}
                      {data.rate_unit ? <td className={r.rate === null ? "r muted" : "r"}>{r.rate === null ? "—" : rateText(r.rate, data.rate_unit)}</td> : null}
                      <td className="r">{p.vram_used_mb === null ? "—" : gb(p.vram_used_mb)}</td>
                      <td className="r">{p.torch_allocated_mb === null ? "—" : `${p.torch_allocated_mb.toFixed(0)} MB`}</td>
                      <td className="r">{p.ram_used_mb === null ? "—" : gb(p.ram_used_mb)}</td>
                      <td className="r">{p.gpu_temp_c === null ? "—" : `${p.gpu_temp_c.toFixed(0)} °C`}</td>
                      <td className="r muted">{p.rss_mb === null ? "—" : `${(p.rss_mb / MB).toFixed(1)} GB`}</td>
                      <td><span className="sys-tagt">{note}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="sys-hfoot">
              <Button size="sm" disabled={!pair} title={pair ? undefined : "Tick two runs"} onClick={() => { if (pair?.[0] && pair[1]) onCompare(pair[0], pair[1]); }}>
                <Icon name="chart" />Compare these two
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
