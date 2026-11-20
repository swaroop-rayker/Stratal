// SCR-10 region 2 (HPO-1.5): each trial's value against its number, the best so far as a
// step line, pruned trials hollow at their last value, failed trials as a cross on the
// axis. Table shows the same numbers. Drawn by hand in SVG, as SCR-04's training charts.
import type { Study, Trial } from "../../api/studies";
import { bestSoFar, score } from "../../api/studies";

const W = 640;
const H = 190;
const PAD = { l: 44, r: 12, t: 10, b: 24 };

function extent(values: number[]): [number, number] {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = hi === lo ? Math.max(Math.abs(hi) * 0.05, 1e-3) : (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}

export function HistoryChart({ study, trials }: { study: Study; trials: Trial[] }) {
  const shown = trials.filter((t) => t.state !== "running");
  const values = shown.flatMap((t) => (t.value === null ? [] : [t.value]));
  const n = Math.max(study.limits.n_trials ?? 1, ...trials.map((t) => t.number + 1), 1);
  const [y0, y1] = values.length ? extent(values) : [0, 1];
  const x = (i: number) => PAD.l + ((i + 0.5) / n) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const best = bestSoFar(trials, study.direction);
  const steps: string[] = [];
  best.forEach(([i, v], k) => {
    const next = best[k + 1];
    steps.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    steps.push(`${x(next ? next[0] : n - 0.5).toFixed(1)},${y(v).toFixed(1)}`);
  });
  const ticks = [y0 + (y1 - y0) * 0.1, (y0 + y1) / 2, y1 - (y1 - y0) * 0.1];
  const xTicks = [0, Math.floor((n - 1) / 2), n - 1];
  return (
    <svg className="hist" viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label={`Optimisation history: ${String(shown.length)} finished trials; best ${study.objective} so far ${score(best[best.length - 1]?.[1])}. The Table view lists the same numbers.`}>
      {ticks.map((v) => (
        <g key={v}>
          <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
          <text className="axis" x={PAD.l - 6} y={y(v) + 4} textAnchor="end">{score(v)}</text>
        </g>
      ))}
      {xTicks.map((i) => (
        <text key={i} className="axis" x={x(i)} y={H - 6} textAnchor="middle">{String(i)}</text>
      ))}
      {steps.length ? <polyline className="best" points={steps.join(" ")} /> : null}
      {shown.map((t) => {
        if (t.state === "failed" || t.value === null) {
          const cx = x(t.number);
          const cy = H - PAD.b - 4;
          return <path key={t.number} className="fail" d={`M${String(cx - 4)} ${String(cy - 4)}l8 8m0 -8l-8 8`} />;
        }
        return <circle key={t.number} className={t.state === "pruned" ? "hollow" : "dot"} cx={x(t.number)} cy={y(t.value)} r="4.5" />;
      })}
    </svg>
  );
}

export function HistoryTable({ study, trials }: { study: Study; trials: Trial[] }) {
  const best = new Map(bestSoFar(trials, study.direction));
  const rows = [...trials].sort((a, b) => a.number - b.number);
  const sofar = rows.reduce<(number | null)[]>((acc, t) => [...acc, best.get(t.number) ?? acc[acc.length - 1] ?? null], []);
  return (
    <div className="table-wrap hist-table">
      <table className="t dense">
        <caption className="sr-only">Optimisation history</caption>
        <thead><tr><th className="r">#</th><th>State</th><th className="r">{study.objective}</th><th className="r">Best so far</th></tr></thead>
        <tbody>
          {rows.map((t, i) => (
            <tr key={t.number}>
              <td className="p">{String(t.number)}</td>
              <td>{t.state}</td>
              <td className="p">{score(t.value)}</td>
              <td className="p">{score(sofar[i])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The median of earlier trials' values at each step: what the median pruner compared. */
export function medianAtSteps(trials: Trial[], before: number): Map<number, number> {
  const byStep = new Map<number, number[]>();
  for (const t of trials) {
    if (t.number >= before || t.state === "failed" || t.state === "running") continue;
    for (const [step, value] of t.intermediate) {
      if (step === undefined || value === undefined) continue;
      byStep.set(step, [...(byStep.get(step) ?? []), value]);
    }
  }
  const out = new Map<number, number>();
  for (const [step, values] of byStep) {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    out.set(step, sorted.length % 2 ? (sorted[mid] ?? 0) : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2);
  }
  return out;
}

/** Region C's chart: a trial's value at each step against the median of earlier trials. */
export function TrialChart({ trial, median }: { trial: Trial; median: Map<number, number> }) {
  const own = trial.intermediate.filter((p): p is [number, number] => p.length === 2);
  const med = [...median.entries()].sort((a, b) => a[0] - b[0]);
  const all = [...own.map((p) => p[1]), ...med.map((p) => p[1])];
  if (!all.length) return null;
  const last = Math.max(...own.map((p) => p[0]), ...med.map((p) => p[0]), 1);
  const [y0, y1] = extent(all);
  const w = 460;
  const h = 150;
  const x = (s: number) => 36 + ((s - 1) / Math.max(last - 1, 1)) * (w - 48);
  const y = (v: number) => 8 + (1 - (v - y0) / (y1 - y0)) * (h - 30);
  const line = (ps: [number, number][]) => ps.map(([s, v]) => `${x(s).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg className="hist" viewBox={`0 0 ${String(w)} ${String(h)}`} role="img" aria-label={`Trial ${String(trial.number)} at each step against the median of earlier trials`}>
      {[y0 + (y1 - y0) * 0.1, y1 - (y1 - y0) * 0.1].map((v) => (
        <g key={v}><line className="grid" x1={36} x2={w - 12} y1={y(v)} y2={y(v)} /><text className="axis" x={30} y={y(v) + 4} textAnchor="end">{score(v)}</text></g>
      ))}
      {[1, last].map((s) => <text key={s} className="axis" x={x(s)} y={h - 6} textAnchor="middle">{String(s)}</text>)}
      {med.length ? <polyline className="med" points={line(med)} /> : null}
      <polyline className="trial" points={line(own)} />
      {own.map(([s, v]) => <circle key={s} className="dot" cx={x(s)} cy={y(v)} r="3.5" />)}
    </svg>
  );
}
