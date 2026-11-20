// SCR-11's task metrics (M10, EVAL-1.2; mockup SCR-11eval_v1): a version's scores for its task
// by split, per-class F1 and the confusion matrix for classification, and why there are
// none when there are none. Everything comes from the version's stored `metrics`.
import { type CSSProperties, type KeyboardEvent, useState } from "react";
import { Link } from "react-router";

import type { VersionDetail } from "../../api/registry";
import { Badge, Button, Icon } from "../../components/ui";
import { count } from "../../lib/format";

type Split = "val" | "test";
type Detail = {
  rows?: number;
  labels?: string[];
  per_class_f1?: Record<string, number>;
  confusion_matrix?: number[][];
  explained_variance_ratio?: number[];
  clusters?: number;
  noise_points?: number;
};
type Metrics = Record<string, unknown> & { task?: string; detail?: Record<string, Detail | string | null> };

/** At most this many classes get a matrix; above it, the largest confusions as a list. */
export const MATRIX_MAX = 20;

/** Each task's rows: the stored key's suffix and its label (EVAL-1.2's sets, task_metrics). */
const ROWS: Record<string, [string, string][]> = {
  classification: [["accuracy", "accuracy"], ["macro_f1", "macro F1"]],
  regression: [["mse", "MSE"], ["rmse", "RMSE"], ["mae", "MAE"], ["r2", "R²"]],
  clustering: [["silhouette", "silhouette"], ["davies_bouldin", "Davies–Bouldin"]],
  language_model: [["perplexity", "perplexity"]],
  translation: [["bleu", "BLEU"], ["chrf", "chrF"]],
  captioning: [["clip_score", "CLIP score"], ["cider_d", "CIDEr-D"]],
};

/** A score as the table prints it: three decimals below 10, else grouped to one decimal. */
export function figure(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString("en-GB");
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(3);
}

/** The largest off-diagonal cells: [true, predicted, count, share of the true class's rows]. */
export function topConfusions(labels: string[], matrix: number[][], n = 10): [string, string, number, number][] {
  const out: [string, string, number, number][] = [];
  matrix.forEach((row, i) => {
    const total = row.reduce((a, b) => a + b, 0);
    row.forEach((c, j) => {
      if (i !== j && c > 0) out.push([labels[i] ?? String(i), labels[j] ?? String(j), c, total ? c / total : 0]);
    });
  });
  return out.sort((a, b) => b[2] - a[2]).slice(0, n);
}

/** Why a version has no task metrics, or null when it has them. */
export function noMetricsReason(v: Pick<VersionDetail, "state" | "metrics">): string | null {
  const m = (v.metrics ?? {}) as Metrics;
  if (v.state === "base") return "A base version holds the initial weights; it is never trained or scored.";
  if (v.state === "partial") return "Scored when the run completes.";
  if (!m.task) return "Not scored: this run finished before task metrics existed (M10). Re-train runs it again with the same config and data, and scores it.";
  return null;
}

function Matrix({ labels, matrix, split }: { labels: string[]; matrix: number[][]; split: string }) {
  const [table, setTable] = useState(false);
  const [tip, setTip] = useState<string | null>(null);
  const [at, setAt] = useState<[number, number]>([0, 0]);
  /** Arrow keys move between cells (one cell is in the tab order), showing each one's sentence. */
  function move(event: KeyboardEvent<HTMLDivElement>) {
    const step: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const d = step[event.key];
    if (!d) return;
    event.preventDefault();
    const n = labels.length;
    const next: [number, number] = [Math.min(n - 1, Math.max(0, at[0] + d[0])), Math.min(n - 1, Math.max(0, at[1] + d[1]))];
    setAt(next);
    event.currentTarget.querySelector<HTMLElement>(`[data-cell="${String(next[0])}-${String(next[1])}"]`)?.focus();
  }
  const sentence = (i: number, j: number, c: number, total: number) =>
    `${labels[i] ?? ""} predicted as ${labels[j] ?? ""}: ${String(c)} of ${count(total)} (${total ? ((100 * c) / total).toFixed(1) : "0"} %)`;
  return (
    <>
      <div className="h"><h3>Confusion matrix</h3><span className="sub">rows true, columns predicted · {split} · counts</span><span className="spacer" />
        <Button size="sm" variant="ghost" aria-pressed={table} onClick={() => { setTable(!table); }}>Table</Button></div>
      {table ? (
        <div className="table-wrap"><table className="t dense"><caption className="sr-only">Confusion matrix, {split}</caption>
          <thead><tr><th scope="col">true \ predicted</th>{labels.map((l) => <th key={l} scope="col" className="r">{l}</th>)}</tr></thead>
          <tbody>{matrix.map((row, i) => <tr key={labels[i]}><th scope="row">{labels[i]}</th>{row.map((c, j) => <td key={labels[j]} className="r">{String(c)}</td>)}</tr>)}</tbody></table></div>
      ) : (
        <div className="cm" role="grid" aria-label={`Confusion matrix, ${split}`} onKeyDown={move} style={{ gridTemplateColumns: `74px repeat(${String(labels.length)}, minmax(0, 1fr))` }}>
          <div role="row" className="cm-row">
            <span role="columnheader" className="ch" />
            {labels.map((l) => <span key={l} role="columnheader" className="ch" title={l}>{l.slice(0, 3)}</span>)}
          </div>
          {matrix.map((row, i) => {
            const total = row.reduce((a, b) => a + b, 0);
            return (
              <div role="row" className="cm-row" key={labels[i]}>
                <span role="rowheader" className="rh">{labels[i]}</span>
                {row.map((c, j) => {
                  const share = total ? c / total : 0;
                  const words = sentence(i, j, c, total);
                  return (
                    <span key={labels[j]} role="gridcell" data-cell={`${String(i)}-${String(j)}`} tabIndex={at[0] === i && at[1] === j ? 0 : -1} aria-label={words}
                      className={`v${share >= 0.5 ? " hi" : ""}`}
                      style={{ "--a": `${String(c ? Math.max(4, Math.round(45 * share)) : 0)}%` } as CSSProperties}
                      onMouseEnter={() => { setTip(words); }} onFocus={() => { setTip(words); }} onMouseLeave={() => { setTip(null); }}>
                      {String(c)}
                    </span>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
      {tip && !table ? <span className="tip" role="status">{tip}</span> : null}
    </>
  );
}

export function TaskMetrics({ version, onRetrain }: { version: VersionDetail; onRetrain: (() => void) | null }) {
  const m = (version.metrics ?? {}) as Metrics;
  const task = m.task ?? null;
  const detail = m.detail ?? {};
  const [split, setSplit] = useState<Split>("val");
  const why = noMetricsReason(version);
  if (why) {
    return (
      <section className="tm" aria-label="Task metrics">
        <div className="h"><h3>Task metrics</h3></div>
        <p className="note2">{why}</p>
        {version.state === "trained" && onRetrain ? <Button size="sm" onClick={onRetrain}><Icon name="refresh" />Re-train</Button> : null}
      </section>
    );
  }
  const trainOnly = task === "clustering" || task === "projection";
  const splits: [string, string][] = trainOnly ? [["train", "Train"]] : [["val", "Validation"], ["test", "Test"]];
  const rows = ROWS[task ?? ""] ?? [];
  const value = (s: string, key: string) => (s === "train" ? m[key] : m[`${s}_${key}`]);
  const pick = detail[split];
  const chosen: Detail = typeof pick === "object" && pick !== null ? pick : {};
  const labels = chosen.labels ?? [];
  const f1 = Object.entries(chosen.per_class_f1 ?? {});
  const matrix = chosen.confusion_matrix ?? [];
  const sorted = labels.length > MATRIX_MAX ? [...f1].sort((a, b) => a[1] - b[1]) : labels.map((l) => [l, chosen.per_class_f1?.[l] ?? 0] as [string, number]);
  const splitWord = split === "val" ? "validation" : "test";
  const trainDetail = detail.train;
  const pca = typeof trainDetail === "object" && trainDetail !== null ? trainDetail.explained_variance_ratio : undefined;
  const toggle = (
    <span className="group-btns" role="group" aria-label="Split">
      <Button size="sm" aria-pressed={split === "val"} onClick={() => { setSplit("val"); }}>Validation</Button>
      <Button size="sm" aria-pressed={split === "test"} onClick={() => { setSplit("test"); }}>Test</Button>
    </span>
  );
  return (
    <section className="tm" aria-label="Task metrics">
      <div className="h"><h3>Task metrics</h3><Badge>{task === "projection" ? "PCA" : task ?? "–"}</Badge><span className="spacer" /><span className="sub mono">EVAL-1.2</span></div>
      {pca ? (
        <div className="f1">
          {pca.map((share, i) => [
            <span key={`n${String(i)}`}>component {String(i + 1)}</span>,
            <span key={`b${String(i)}`} className="bar" aria-hidden="true"><i style={{ "--p": `${String(Math.round(100 * share))}%` } as CSSProperties} /></span>,
            <span key={`v${String(i)}`}>{share.toFixed(2)}</span>,
          ])}
          <span>total</span><span /><span>{pca.reduce((a, b) => a + b, 0).toFixed(2)}</span>
        </div>
      ) : (
        <table className="t dense">
          <caption className="sr-only">Task metrics by split</caption>
          <thead><tr><th scope="col">Metric</th>{splits.map(([s, word]) => <th key={s} scope="col" className="r">{word}{s === "test" && version.study_id ? <> <span className="badge once">computed once</span></> : null}</th>)}</tr></thead>
          <tbody>
            {rows.map(([key, label]) => <tr key={key}><td>{label}</td>{splits.map(([s]) => <td key={s} className="r">{figure(value(s, key))}</td>)}</tr>)}
            {task === "clustering" ? (
              <>
                <tr><td className="dimrow">clusters</td><td className="r dimrow">{figure(m.clusters)}</td></tr>
                <tr><td className="dimrow">noise points</td><td className="r dimrow">{typeof m.noise_points === "number" ? count(m.noise_points) : "–"}</td></tr>
              </>
            ) : null}
            {typeof m.val_loss === "number" ? <tr><td className="dimrow">loss</td><td className="r dimrow">{figure(m.val_loss)}</td>{splits.length > 1 ? <td className="r dimrow">–</td> : null}</tr> : null}
            {!trainOnly ? (
              <tr><td className="dimrow">rows</td>{splits.map(([s]) => {
                const d = detail[s];
                return <td key={s} className="r dimrow">{typeof d === "object" && d !== null && typeof d.rows === "number" ? count(d.rows) : "–"}</td>;
              })}</tr>
            ) : null}
          </tbody>
        </table>
      )}
      {task === "regression" ? <p className="note2">On the target's own scale. An R² of 0 is as good as always predicting the mean.</p> : null}
      {trainOnly && !pca ? <p className="note2">Clustering has no held-out split: the scores are on the train split it was fitted on. Silhouette is better higher (−1 to 1), Davies–Bouldin lower.</p> : null}
      {pca ? <p className="note2">Explained variance by component, on the train split.</p> : null}
      {version.study_id ? (
        <p className="note2">Test computed once by study <Link className="mono" to={`/studies/${version.study_id}`}>{version.study_id}</Link> on its best trial (HPO-1.4).</p>
      ) : null}
      {task === "classification" && f1.length ? (
        <>
          {labels.length > MATRIX_MAX ? (
            <>
              <div className="h"><h3>Most confused</h3><span className="sub">{splitWord} · {String(labels.length)} classes, too many for a matrix</span><span className="spacer" />{toggle}</div>
              <div className="conf" role="table" aria-label={`Most confused, ${splitWord}`}>
                <span role="columnheader">True</span><span role="columnheader">Predicted</span><span role="columnheader" className="r">Count</span><span role="columnheader" className="r">Share</span>
                {topConfusions(labels, matrix).map(([t, p, c, share]) => [
                  <b key={`${t}${p}t`} role="cell">{t}</b>, <span key={`${t}${p}p`} role="cell">{p}</span>,
                  <span key={`${t}${p}c`} role="cell" className="r">{String(c)}</span>, <span key={`${t}${p}s`} role="cell" className="r">{(100 * share).toFixed(1)} %</span>,
                ])}
              </div>
              <div className="h"><h3>Per-class F1</h3><span className="sub">lowest first</span></div>
              <div className="f1 scroll">
                {sorted.map(([label, v]) => [
                  <span key={`${label}n`}>{label}</span>,
                  <span key={`${label}b`} className="bar" aria-hidden="true"><i style={{ "--p": `${String(Math.round(100 * v))}%` } as CSSProperties} /></span>,
                  <span key={`${label}v`}>{v.toFixed(2)}</span>,
                ])}
              </div>
            </>
          ) : (
            <>
              <div className="h"><h3>Per-class F1</h3><span className="spacer" />{toggle}</div>
              <div className="f1">
                {sorted.map(([label, v]) => [
                  <span key={`${label}n`}>{label}</span>,
                  <span key={`${label}b`} className="bar" aria-hidden="true"><i style={{ "--p": `${String(Math.round(100 * v))}%` } as CSSProperties} /></span>,
                  <span key={`${label}v`}>{v.toFixed(2)}</span>,
                ])}
              </div>
              {matrix.length ? <Matrix labels={labels} matrix={matrix} split={splitWord} /> : null}
            </>
          )}
        </>
      ) : null}
    </section>
  );
}
