// SCR-11 v2's comparison (EVAL-1.3; mockup SCR-11v2_v1, frames A, B and F): two to six versions
// scored on one test split, side by side. The URL holds the split and the versions
// (/models/compare?split=ds_…&v=mv_…&v=mv_…), so a comparison can be bookmarked. Versions without
// test metrics can be scored once from the picker (K8's exception).
import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError, type Schemas } from "../../api/client";
import { useDatasets, useVersions } from "../../api/queries";
import { useCompareCandidates, useComparison, useRegistryLive, useScoreTest } from "../../api/registry";
import { Drawer } from "../../components/Drawer";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";
import { trainable } from "../build/TrainDrawer";

type Comparison = Schemas["Comparison"];
type Candidate = Schemas["CompareCandidate"];

const LABELS: Record<string, string> = { accuracy: "accuracy", macro_f1: "macro F1", mse: "MSE", rmse: "RMSE", mae: "MAE", r2: "R²" };
const MIN = 2;
const MAX = 6;

function fixed(n: number): string {
  return Math.abs(n) >= 1000 ? count(Math.round(n)) : n.toFixed(3);
}

/** Classes with the largest spread between versions first; at most 20 (decision 5). */
export function bySpread(versions: Comparison["versions"]): string[] {
  const classes = [...new Set(versions.flatMap((v) => Object.keys(v.per_class_f1)))];
  const spread = (c: string) => {
    const values = versions.map((v) => v.per_class_f1[c] ?? 0);
    return Math.max(...values) - Math.min(...values);
  };
  return classes.sort((a, b) => spread(b) - spread(a) || a.localeCompare(b)).slice(0, 20);
}

function SplitPicker({ onPick }: { onPick: (split: string) => void }) {
  const datasets = useDatasets();
  const [slug, setSlug] = useState<string | undefined>(undefined);
  const versions = useVersions(slug);
  const splits = (versions.data?.items ?? []).filter((v) => trainable(v));
  return (
    <EmptyState icon="grid" title="Choose the test split to compare on"
      action={
        <span style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap", justifyContent: "center" }}>
          <label className="sr-only" htmlFor="cmp-dataset">Dataset</label>
          <select id="cmp-dataset" className="select mono" value={slug ?? ""} onChange={(e) => { setSlug(e.target.value || undefined); }}>
            <option value="">Dataset…</option>
            {(datasets.data?.items ?? []).map((d) => <option key={d.slug} value={d.slug}>{d.slug}</option>)}
          </select>
          <label className="sr-only" htmlFor="cmp-split">Split version</label>
          <select id="cmp-split" className="select mono" disabled={!slug} value="" onChange={(e) => { if (e.target.value) onPick(e.target.value); }}>
            <option value="">Split version…</option>
            {splits.map((v) => <option key={v.version_id} value={v.version_id}>{v.version_id} · {v.stage}</option>)}
          </select>
        </span>
      }>
      Versions are compared only on the split version they were tested on.
    </EmptyState>
  );
}

function Picker({ open, onClose, split, chosen, onCompare }: {
  open: boolean;
  onClose: () => void;
  split: string;
  chosen: string[];
  onCompare: (ids: string[]) => void;
}) {
  const candidates = useCompareCandidates(open ? split : null);
  const score = useScoreTest();
  const [picked, setPicked] = useState<string[] | null>(null);
  const selected = picked ?? chosen;
  const items = candidates.data?.items ?? [];
  const groups = [...new Set(items.map((c) => c.lineage_id))];
  // Two lineages can share a name (M8's reference small_cnn and a graph): add the ID then.
  const lineagesByName = new Map<string, Set<string>>();
  for (const c of items) lineagesByName.set(c.lineage_name, (lineagesByName.get(c.lineage_name) ?? new Set()).add(c.lineage_id));
  const facts = candidates.data?.split;
  function toggle(c: Candidate, on: boolean) {
    setPicked(on ? [...selected, c.id] : selected.filter((id) => id !== c.id));
  }
  const n = selected.length;
  return (
    <Drawer open={open} onOpenChange={(o) => { if (!o) { setPicked(null); onClose(); } }} title="Add or remove versions" width={560}
      footer={
        <div className="cmp-foot">
          <span className="cmp-sub">{String(n)} chosen · {n < MIN ? `choose at least ${String(MIN)}` : `${String(MIN)} to ${String(MAX)}`}</span>
          <span className="spacer" style={{ flex: 1 }} />
          <Button size="sm" variant="ghost" onClick={() => { setPicked(null); onClose(); }}>Cancel</Button>
          <Button size="sm" variant="primary" disabled={n < MIN || n > MAX} onClick={() => { onCompare(selected); setPicked(null); }}>Compare{n >= MIN ? ` ${String(n)}` : ""}</Button>
        </div>
      }>
      <div className="scr11 in-dialog" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        {facts ? <div className="cmp-facts"><span><b>{facts.dataset}</b> · {facts.version_id}</span><span>test <b>{count(facts.test_rows)}</b> rows</span></div> : null}
        {candidates.isPending ? <Skeleton width={480} height={160} /> : candidates.isError ? <ErrorState error={candidates.error} title="The versions could not be listed" onRetry={() => void candidates.refetch()} /> : items.length === 0 ? (
          <EmptyState icon="grid" title="No trained version on this split yet">Train a model on it first.</EmptyState>
        ) : (
          <div className="table-wrap cmp-pick">
            <table className="t">
              <caption className="sr-only">Versions trained on {split}</caption>
              <thead><tr><th style={{ width: 32 }}><span className="sr-only">Chosen</span></th><th>Version</th><th className="r">Test</th><th>Weights</th><th>Test metrics</th></tr></thead>
              <tbody>
                {groups.map((g) => {
                  const rows = items.filter((c) => c.lineage_id === g);
                  const first = rows[0];
                  if (!first) return null;
                  const repeated = (lineagesByName.get(first.lineage_name)?.size ?? 0) > 1;
                  return [
                    <tr key={`${g}-h`} className="sec"><td colSpan={5}>{first.lineage_name}{repeated ? <> · <span className="mono" style={{ textTransform: "none", letterSpacing: 0 }}>{g}</span></> : null}</td></tr>,
                    ...rows.map((c) => {
                      const tested = c.test_value !== null;
                      const on = selected.includes(c.id);
                      return (
                        <tr key={c.id} className={tested ? undefined : "cmp-na"} aria-selected={on}>
                          <td><input type="checkbox" checked={on} disabled={!tested} aria-label={tested ? c.id : `${c.id}, ${c.why_not ?? "not scored"}`} onChange={(e) => { toggle(c, e.target.checked); }} /></td>
                          <td className="id">{c.id}</td>
                          <td className="r">{tested ? `${c.test_metric === "accuracy" ? "acc" : c.test_metric ?? ""} ${fixed(c.test_value ?? 0)}` : "–"}</td>
                          <td className={c.weights_status === "present" ? undefined : "weights-gone"}>{c.weights_status === "present" ? "present" : "deleted"}</td>
                          <td>
                            {tested ? (c.study_id ? <span className="badge once">computed once</span> : <span className="cmp-why">from its run</span>) : c.scoring_job_id ? (
                              <span className="pill running"><span className="pulse" />Scoring · {c.scoring_job_id}</span>
                            ) : c.can_score ? (
                              <Button size="sm" disabled={score.isPending} onClick={() => { score.mutate(c.id); }}><Icon name="play" />Score on test</Button>
                            ) : <span className="cmp-why">{c.why_not ?? "not scored"}</span>}
                          </td>
                        </tr>
                      );
                    }),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
        {score.isError ? <ErrorState error={score.error} title="Score on test was refused" /> : null}
        <p className="note2">No test metrics: trained before M10. <i>Score on test</i> runs the test split through the version once (<span className="mono">test_eval</span>); a version with test metrics is never scored again (HPO-1.4). Versions whose weights were deleted keep their metrics, so they can still be compared (REG-2.7).</p>
      </div>
    </Drawer>
  );
}

function IntervalChart({ data }: { data: Comparison }) {
  const [table, setTable] = useState(false);
  const rows = data.versions.filter((v) => v.accuracy_low !== null && v.accuracy_high !== null);
  const low = Math.min(0.5, ...rows.map((r) => Math.floor((r.accuracy_low ?? 0) * 4) / 4));
  const at = (x: number) => `${String((100 * (x - low)) / (1 - low))}%`;
  const ticks = [0, 1, 2, 3, 4].map((i) => low + ((1 - low) * i) / 4);
  return (
    <section className="card">
      <h2>Accuracy with its 95 % interval<span className="spacer" />
        <span className="group-btns" role="group" aria-label="View"><Button size="sm" aria-pressed={!table} onClick={() => { setTable(false); }}>Chart</Button><Button size="sm" aria-pressed={table} onClick={() => { setTable(true); }}>Table</Button></span></h2>
      {table ? (
        <div className="table-wrap"><table className="t dense"><caption className="sr-only">Accuracy and its Wilson 95 % interval</caption>
          <thead><tr><th>Version</th><th className="r">Accuracy</th><th className="r">Low</th><th className="r">High</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id}><td className="id">{r.id}</td><td className="r">{fixed(r.metrics.accuracy ?? 0)}</td><td className="r">{fixed(r.accuracy_low ?? 0)}</td><td className="r">{fixed(r.accuracy_high ?? 0)}</td></tr>)}</tbody>
        </table></div>
      ) : (
        <div className="cmp-ci" role="img" aria-label={`Accuracy intervals: ${rows.map((r) => `${r.id} ${fixed(r.metrics.accuracy ?? 0)} (${fixed(r.accuracy_low ?? 0)} to ${fixed(r.accuracy_high ?? 0)})`).join("; ")}`}>
          {rows.map((r) => (
            <span key={r.id} style={{ display: "contents" }}>
              <span>{r.id}</span>
              <div className="cmp-row"><span className="cmp-w" style={{ left: at(r.accuracy_low ?? 0), width: `calc(${at(r.accuracy_high ?? 0)} - ${at(r.accuracy_low ?? 0)})` }} /><span className="cmp-d" style={{ left: at(r.metrics.accuracy ?? 0) }} /></div>
              <span className="cmp-val">{fixed(r.metrics.accuracy ?? 0)}</span>
            </span>
          ))}
          <span /><div className="cmp-ax">{ticks.map((t) => <span key={t}>{t.toFixed(t * 100 % 5 === 0 ? 2 : 3)}</span>)}</div><span />
        </div>
      )}
      {data.overlap ? (
        <div className="callout info"><Icon name="info" /><span><b>The intervals overlap.</b> With {count(data.split.test_rows)} test rows one mistake moves accuracy by {data.split.test_rows ? (1 / data.split.test_rows).toFixed(3) : "?"}, so this split cannot tell these {["", "", "two", "three", "four", "five", "six"][rows.length] ?? String(rows.length)} apart. A larger test split would.</span></div>
      ) : null}
    </section>
  );
}

function F1ByClass({ data }: { data: Comparison }) {
  const classes = bySpread(data.versions);
  const total = new Set(data.versions.flatMap((v) => Object.keys(v.per_class_f1))).size;
  if (classes.length === 0) return null;
  return (
    <section className="card">
      <h2>F1 by class <span className="note2" style={{ fontWeight: 400 }}>largest spread first{total > 20 ? `, the 20 of ${String(total)} classes that differ most` : ""}</span></h2>
      <div className="table-wrap cmp-table"><table className="t dense"><caption className="sr-only">F1 by class and version</caption>
        <thead><tr><th scope="col">Class</th>{data.versions.map((v) => <th key={v.id} scope="col" className="r">{v.id}</th>)}</tr></thead>
        <tbody>{classes.map((c) => {
          const values = data.versions.map((v) => v.per_class_f1[c] ?? 0);
          const top = Math.max(...values);
          const tie = values.every((x) => x === top);
          return (
            <tr key={c}><td>{c}</td>{data.versions.map((v) => {
              const f = v.per_class_f1[c] ?? 0;
              const best = !tie && f === top;
              return <td key={v.id} className={best ? "r cmp-lead" : "r"}>{f.toFixed(3)}{best ? <span className="sr-only"> best</span> : null}<span className="cmp-bar" aria-hidden="true"><i style={{ ["--p" as string]: `${String(100 * f)}%` }} /></span></td>;
            })}</tr>
          );
        })}</tbody>
      </table></div>
      <p className="note2">Best in semibold; a tie marks none. Each version's confusion matrix is in its drawer (SCR-11's task metrics).</p>
    </section>
  );
}

function MetricsTable({ data }: { data: Comparison }) {
  const metrics = [...new Set(data.versions.flatMap((v) => Object.keys(v.metrics)))];
  return (
    <section className="card">
      <h2>Metrics <span className="note2" style={{ fontWeight: 400 }}>test split</span></h2>
      <div className="table-wrap cmp-table"><table className="t"><caption className="sr-only">Test metrics by version</caption>
        <thead><tr><th scope="col">Metric</th>{data.versions.map((v) => <th key={v.id} scope="col"><span className="vid">{v.id}</span><span className="lin">{v.lineage_name}</span></th>)}</tr></thead>
        <tbody>
          {metrics.map((m) => (
            <Fragment key={m}>
              <tr><td><b>{LABELS[m] ?? m}</b></td>{data.versions.map((v) => {
                const best = (data.best[m] ?? []).includes(v.id);
                const value = v.metrics[m];
                return (
                  <td key={v.id} className={best ? "r cmp-lead" : "r"}>
                    {value === undefined ? "–" : fixed(value)}
                    {m === "accuracy" && v.correct !== null && data.split.test_rows ? <span className="cmp-of">{count(v.correct)} of {count(data.split.test_rows)}</span> : null}
                    {best ? <span className="badge best">best</span> : null}
                  </td>
                );
              })}</tr>
              {m === "accuracy" ? (
                <tr key="ci"><td className="cmp-lab">95 % interval (Wilson)</td>{data.versions.map((v) => <td key={v.id} className="r cmp-dim">{v.accuracy_low !== null && v.accuracy_high !== null ? `${fixed(v.accuracy_low)} – ${fixed(v.accuracy_high)}` : "–"}</td>)}</tr>
              ) : null}
            </Fragment>
          ))}
          <tr className="sec"><td colSpan={data.versions.length + 1}>About each version</td></tr>
          <tr><td className="cmp-lab">Kind</td>{data.versions.map((v) => <td key={v.id}>{v.kind === "classical" ? `classical · ${v.model}` : "network (graph)"}</td>)}</tr>
          <tr><td className="cmp-lab">Parameters</td>{data.versions.map((v) => <td key={v.id} className="r">{count(v.parameters)}</td>)}</tr>
          <tr><td className="cmp-lab">Trained</td>{data.versions.map((v) => <td key={v.id} className="m2">{v.trained_at ? v.trained_at.slice(0, 16).replace("T", " ") : "–"}{v.job_id ? <> · <Link to={`/jobs/${v.job_id}`}>{v.job_id}</Link></> : null}</td>)}</tr>
          <tr><td className="cmp-lab">Weights</td>{data.versions.map((v) => <td key={v.id} className={v.weights_status === "present" ? undefined : "weights-gone"}>{v.weights_status === "present" ? "present" : "deleted"}</td>)}</tr>
          <tr><td className="cmp-lab">Parent</td>{data.versions.map((v) => <td key={v.id} className="cmp-dim">{v.parent_is_base || !v.parent_version_id ? "its lineage's base" : <span className="mono">{v.parent_version_id}</span>}</td>)}</tr>
        </tbody>
      </table></div>
    </section>
  );
}

export function CompareScreen() {
  useRegistryLive();
  const [params, setParams] = useSearchParams();
  const split = params.get("split");
  const ids = params.getAll("v");
  const [picking, setPicking] = useState(false);
  const comparison = useComparison(split, ids);
  useCrumbs([{ label: "Models", to: "/models" }, { label: "Compare" }]);

  function set(next: { split?: string; ids?: string[] }) {
    const p = new URLSearchParams();
    const s = next.split ?? split;
    if (s) p.set("split", s);
    for (const id of next.ids ?? ids) p.append("v", id);
    setParams(p, { replace: true });
  }

  const problem = comparison.error instanceof ApiError ? comparison.error.problem : null;
  const bad = problem?.errors[0];
  const badIndex = bad && bad.loc[0] === "v" && typeof bad.loc[1] === "number" ? bad.loc[1] : null;
  const data = comparison.data;
  return (
    <div className="scr11">
      <div className="phead">
        <h1>Compare on one test split</h1>
        <span className="sub">{String(ids.length)} versions · <span className="mono">EVAL-1.3</span></span>
        <span className="spacer" style={{ flex: 1 }} />
        {split ? <Button size="sm" onClick={() => { setPicking(true); }}><Icon name="plus" />Add or remove versions…</Button> : null}
        {split ? <Button size="sm" variant="ghost" onClick={() => { void navigator.clipboard.writeText(window.location.href); }}>Copy link</Button> : null}
      </div>
      {!split ? <SplitPicker onPick={(s) => { set({ split: s, ids: [] }); setPicking(true); }} /> : ids.length < MIN ? (
        <EmptyState icon="grid" title="Pick at least two versions" action={<Button size="sm" variant="primary" onClick={() => { setPicking(true); }}>Choose versions</Button>}>
          They must be tested on one split: <span className="mono">{split}</span>.
        </EmptyState>
      ) : comparison.isPending ? (
        <section className="card" aria-busy="true"><Skeleton width={260} height={18} /><Skeleton width={900} height={36} /><Skeleton width={900} height={36} /><span className="note2">Loading {String(ids.length)} versions…</span></section>
      ) : problem && bad ? (
        <div className="state err" role="alert">
          <Icon name="alert" />
          <b>{bad.loc[0] === "v" && bad.loc.length === 2 ? "These versions were not tested on one split" : "These versions cannot be compared"}</b>
          <p><span className="mono">{problem.code}</span> · {bad.msg}</p>
          {badIndex !== null ? <Button size="sm" onClick={() => { set({ ids: ids.filter((_, i) => i !== badIndex) }); }}>Remove it</Button> : <Button size="sm" onClick={() => { setPicking(true); }}>Choose versions</Button>}
        </div>
      ) : comparison.isError || !data ? (
        <ErrorState error={comparison.error} title="The comparison could not be loaded" onRetry={() => void comparison.refetch()} />
      ) : (
        <>
          <section className="card">
            <h2>Test split</h2>
            <div className="cmp-facts">
              <span><b>{data.split.dataset}</b> · {data.split.version_id}</span>
              <span>test <b>{count(data.split.test_rows)}</b> rows</span>
              <span>{data.split.dataset_type ?? "?"} · {data.split.task ?? "?"}</span>
              {data.split.classes.length ? <span>{String(data.split.classes.length)} classes{data.split.classes.length <= 6 ? `: ${data.split.classes.join(", ")}` : ""}</span> : null}
            </div>
            <p className="note2">Every column below was scored on these {count(data.split.test_rows)} rows.</p>
          </section>
          <MetricsTable data={data} />
          <div className="cmp-two">
            {data.versions.some((v) => v.accuracy_low !== null) ? <IntervalChart data={data} /> : null}
            <F1ByClass data={data} />
          </div>
        </>
      )}
      {split ? <Picker open={picking} onClose={() => { setPicking(false); }} split={split} chosen={ids} onCompare={(next) => { set({ ids: next }); setPicking(false); }} /> : null}
    </div>
  );
}
