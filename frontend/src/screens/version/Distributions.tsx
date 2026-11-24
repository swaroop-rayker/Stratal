// SCR-03 v2 Distributions (approved hi-fi SCR-03v2_v1, frame A; UI-2.2): the funnel from
// raw to this version, then each column raw (every row) against here (accepted rows),
// on shared bins, with five numbers and what the removed rows have in common.
import { useState } from "react";

import type { Schemas } from "../../api/client";
import { useDistributions } from "../../api/queries";
import { ErrorState } from "../../components/states";
import { Skeleton } from "../../components/ui";
import { count } from "../../lib/format";

type Dist = Schemas["Distributions"];
type Column = Schemas["ColumnProfile"];
type Five = Schemas["FiveNumbers"];

const INTEGER_UNITS = new Set(["px", "words", "tokens"]);
const CHIP: Record<string, string> = { rejected: "rej", duplicate: "dup", filtered: "fil" };
const ADDED: Record<string, string> = {
  label: "captions and CLIP scores added",
  preprocess: "copies made",
  tokenize: "tokens added",
};

function fmt(value: number, unit: string): string {
  if (INTEGER_UNITS.has(unit) || Number.isInteger(value)) return Math.round(value).toLocaleString("en-US");
  if (Math.abs(value) < 10) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return value.toFixed(1);
}

function binLabel(low: number, high: number, unit: string, last: boolean): string {
  if (INTEGER_UNITS.has(unit)) return `${fmt(low, unit)}–${fmt(last ? high : high - 1, unit)}`;
  return `${fmt(low, unit)}–${fmt(high, unit)}`;
}

function Funnel({ dist, onReason }: { dist: Dist; onReason: (code: string) => void }) {
  const last = dist.funnel.length - 1;
  return (
    <section className="card v2card">
      <h2>From raw to this version <span className="sub2">rows each stage took in, removed and passed on</span></h2>
      <div className="table-wrap">
        <table className="t dense">
          <caption className="sr-only">Rows by stage from raw to this version</caption>
          <thead>
            <tr><th>Stage</th><th>Version</th><th className="r">In</th><th>Removed, by reason</th><th className="r">Out</th></tr>
          </thead>
          <tbody>
            {dist.funnel.map((step, i) => (
              <tr key={step.version_id} className={i === last ? "here" : undefined}>
                <td>{i === last ? <b>{step.stage}</b> : step.stage}</td>
                <td className="id">{step.version_id}</td>
                <td className="r">{count(step.rows_in)}</td>
                <td>
                  {step.removed.length ? (
                    <span className="chips">
                      {step.removed.map((r) => (
                        <button
                          type="button"
                          key={r.reason_code}
                          className={`chip ${CHIP[r.status] ?? "dup"}`}
                          onClick={() => { onReason(r.reason_code); }}
                          aria-label={`${r.reason_code}, ${count(r.rows)} rows: show them`}
                        >
                          <i />
                          {r.reason_code} <b>{count(r.rows)}</b>
                        </button>
                      ))}
                    </span>
                  ) : step.parts ? (
                    <span className="parts">train {count(step.parts["train"])} · val {count(step.parts["val"])} · test {count(step.parts["test"])}</span>
                  ) : (
                    <span className="lab">none{ADDED[step.stage] ? ` · ${ADDED[step.stage] ?? ""}` : ""}</span>
                  )}
                </td>
                <td className="r">{count(step.rows_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note2">
        {count(dist.removed)} of {count(dist.row_count)} rows ({dist.row_count ? ((dist.removed / dist.row_count) * 100).toFixed(1) : "0"} %) were removed. A reason opens Rows filtered to it.
      </p>
    </section>
  );
}

function FiveTable({ col }: { col: Column }) {
  const rows: [string, Five | null | undefined, boolean][] = [
    [`${col.raw_label} · ${count(col.raw?.n ?? 0)}`, col.raw, false],
    [`here · ${count(col.here?.n ?? 0)}`, col.here, true],
  ];
  return (
    <div className="table-wrap">
      <table className="t dense">
        <caption className="sr-only">{col.label}, five numbers</caption>
        <thead><tr><th /><th className="r">min</th><th className="r">p10</th><th className="r">median</th><th className="r">p90</th><th className="r">max</th></tr></thead>
        <tbody>
          {rows.map(([label, five, here]) =>
            five ? (
              <tr key={label} className={here ? "here" : undefined}>
                <td className={here ? undefined : "lab"}>{label}</td>
                {(["min", "p10", "median", "p90", "max"] as const).map((k) => (
                  <td key={k} className="r">{fmt(five[k], col.unit)}</td>
                ))}
              </tr>
            ) : null,
          )}
        </tbody>
      </table>
    </div>
  );
}

function Histogram({ col, marker }: { col: Column; marker: number | null }) {
  const bins = col.bins ?? [];
  const top = Math.max(1, ...bins.map((b) => b.raw));
  const text = bins.map((b, i) => `${binLabel(b.low, b.high, col.unit, i === bins.length - 1)} ${String(b.raw)} and ${String(b.here)}`).join("; ");
  return (
    <>
      <div className="key">
        <span><i className="k-raw" />{col.raw_label} · {count(col.raw?.n ?? 0)}</span>
        <span><i className="k-here" />here · {count(col.here?.n ?? 0)}</span>
      </div>
      <div className="hist" role="img" aria-label={`${col.label} histogram, ${col.raw_label} against here: ${text}`}>
        {bins.map((b, i) => (
          <div className="hrow" key={b.low}>
            <span>{binLabel(b.low, b.high, col.unit, i === bins.length - 1)}{marker !== null && b.low < marker && marker <= b.high ? " ◂" : ""}</span>
            <div className="track">
              <span className="raw" style={{ width: `${((b.raw / top) * 100).toFixed(1)}%` }} />
              {b.here ? <span className="here" style={{ width: `${((b.here / top) * 100).toFixed(1)}%` }} /> : null}
            </div>
            <span className="n">{count(b.raw)} · {count(b.here)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function ColumnCard({ col, marker, cut }: { col: Column; marker: number | null; cut: number | null }) {
  const [table, setTable] = useState(false);
  const removed = col.removed;
  return (
    <section className="card v2card">
      <h2>
        {col.label} {col.unit ? <span className="sub2">{col.unit}</span> : null}
        <span className="spacer" />
        {col.bins ? (
          <span className="group-btns" role="group" aria-label={`${col.label} view`}>
            <button type="button" className="btn sm" aria-pressed={!table} onClick={() => { setTable(false); }}>Chart</button>
            <button type="button" className="btn sm" aria-pressed={table} onClick={() => { setTable(true); }}>Table</button>
          </span>
        ) : null}
      </h2>
      {col.bins && !table ? <Histogram col={col} marker={marker} /> : null}
      {col.bins && table ? (
        <div className="table-wrap">
          <table className="t dense">
            <caption className="sr-only">{col.label} bins</caption>
            <thead><tr><th>Bin</th><th className="r">{col.raw_label}</th><th className="r">here</th></tr></thead>
            <tbody>
              {col.bins.map((b, i) => (
                <tr key={b.low}><td>{binLabel(b.low, b.high, col.unit, i === (col.bins?.length ?? 0) - 1)}</td><td className="r">{count(b.raw)}</td><td className="r">{count(b.here)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {!col.bins && col.here ? <p className="note2">Every row has {fmt(col.here.min, col.unit)} {col.unit}: no chart.</p> : null}
      <FiveTable col={col} />
      {marker !== null ? (
        <p className="mark-line">
          The filter's threshold, <b className="mono">{marker}</b> (OC-5), cuts the left tail{cut !== null ? `: ${count(cut)} rows` : ""}. ◂ marks its bin.
        </p>
      ) : removed && removed.n ? (
        <p className="note2">
          Removed · {count(removed.n)}: median {fmt(removed.median, col.unit)}{col.unit ? ` ${col.unit}` : ""}, from {fmt(removed.min, col.unit)} to {fmt(removed.max, col.unit)}.
        </p>
      ) : null}
    </section>
  );
}

function Classes({ dist }: { dist: Dist }) {
  const classes = dist.classes ?? [];
  const top = Math.max(1, ...classes.map((c) => c.rows));
  return (
    <section className="card v2card">
      <h2>Classes <span className="sub2">accepted rows per split</span></h2>
      <div className="table-wrap">
        <table className="t dense">
          <caption className="sr-only">Class counts per split</caption>
          <thead><tr><th>Class</th><th className="r">train</th><th className="r">val</th><th className="r">test</th><th className="r">rows</th><th style={{ width: 160 }}><span className="sr-only">Bar</span></th></tr></thead>
          <tbody>
            {classes.slice(0, 40).map((c) => (
              <tr key={c.label}>
                <td>{c.label}</td><td className="r">{count(c.train)}</td><td className="r">{count(c.val)}</td><td className="r">{count(c.test)}</td><td className="r">{count(c.rows)}</td>
                <td><span className="cbar" style={{ width: `${((c.rows / top) * 100).toFixed(1)}%` }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {classes.length > 40 ? <p className="note2">{count(classes.length - 40)} more classes.</p> : null}
    </section>
  );
}

export function DistributionsTab({ versionId, active, onReason }: { versionId: string; active: boolean; onReason: (code: string) => void }) {
  const dist = useDistributions(versionId, active);
  if (dist.isError) return <ErrorState error={dist.error} title="The distributions could not be computed" onRetry={() => void dist.refetch()} />;
  if (!dist.data) {
    return (
      <div className="v2stack" aria-busy="true">
        <section className="card v2card"><Skeleton width={260} height={16} /><span className="skeleton" style={{ height: 160, display: "block" }} /></section>
        <section className="card v2card"><Skeleton width={200} height={16} /><span className="skeleton" style={{ height: 120, display: "block" }} /></section>
      </div>
    );
  }
  const d = dist.data;
  const clipMarker = typeof d.markers["clip_score"] === "number" ? d.markers["clip_score"] : null;
  const filtered = d.funnel.find((s) => s.stage === "filter")?.removed.reduce((n, r) => n + r.rows, 0) ?? null;
  const formats = Object.entries(d.formats);
  return (
    <div className="v2stack">
      <Funnel dist={d} onReason={onReason} />
      <div className="two">
        {d.columns.map((col) => (
          <ColumnCard key={col.key} col={col} marker={col.key === "clip_score" ? clipMarker : null} cut={col.key === "clip_score" ? filtered : null} />
        ))}
      </div>
      {d.classes?.length ? <Classes dist={d} /> : null}
      {d.token_cut ? (
        <p className="note2">
          Tokens are cut at {d.token_cut.length}: {count(d.token_cut.cut)} of {count(d.token_cut.rows)} texts ({((d.token_cut.cut / Math.max(1, d.token_cut.rows)) * 100).toFixed(1)} %) were longer.
        </p>
      ) : null}
      {formats.length === 1 ? (
        <p className="note2">All {count(d.row_count)} are {formats[0]?.[0]}, so format has no chart.</p>
      ) : formats.length > 1 ? (
        <p className="note2">Formats: {formats.map(([f, n]) => `${f} ${count(n)}`).join(" · ")}.</p>
      ) : null}
    </div>
  );
}
