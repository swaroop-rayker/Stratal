// SCR-03 v4 Columns tab (approved hi-fi SCR-03columns_v1; DATA-7.5, DATA-7.3): any tabular
// version as a table of its columns over its accepted rows: kind, role, encoding, missing,
// distinct and distribution, a row expanding in place to 20 bins with a table of them.
import { Fragment, useMemo, useState } from "react";

import type { Schemas } from "../../api/client";
import { useColumns } from "../../api/queries";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon } from "../../components/ui";
import { count } from "../../lib/format";

type Summary = Schemas["VersionColumns"];
type Col = Schemas["ColumnSummary"];
type Lineage = Schemas["Lineage"];

interface Numeric {
  type: "numeric";
  min: number | null;
  median: number | null;
  max: number | null;
  sparkline?: number[];
  histogram?: { edges: number[]; counts: number[] };
}
interface Categorical {
  type: "categorical";
  top: { value: string; count: number }[];
  more: number;
}

type SortKey = "schema" | "name" | "kind" | "role" | "encoding" | "missing" | "distinct";

/** A number as the summary prints it: whole numbers with separators, else up to 3 decimals. */
export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 1 : abs >= 1 ? 3 : 4;
  return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

/** One operation in words, as the operations callout lists them (frame B). */
export function describeOp(op: Record<string, unknown>): string {
  const s = (k: string) => {
    const v = op[k];
    return typeof v === "string" ? v : Array.isArray(v) ? v.join(", ") : "";
  };
  switch (op["op"]) {
    case "drop": return `drop ${s("columns")}`;
    case "rename": return `rename ${s("from")} → ${s("to")}`;
    case "reorder": return "reorder the columns";
    case "set_kind": return `set_kind ${s("column")} ${s("kind")}`;
    case "set_target": return `set_target ${s("column")}`;
    case "set_encoding": return `set_encoding ${s("column")} ${s("encoding")}`;
    case "derive": return `derive ${s("name")} = ${s("expression")}`;
    case "filter": return `filter ${s("expression")}`;
    default: return String(op["op"]);
  }
}

/** The transform this version descends from, and the raw version it started from. */
export function transformOf(versionId: string, stage: string, config: Record<string, unknown>, lineage: Lineage | undefined) {
  const own = stage === "transform" ? { version_id: versionId, config } : null;
  const found = own ?? lineage?.ancestors.find((a) => a.stage === "transform") ?? null;
  if (!found) return null;
  const raw = lineage?.ancestors.find((a) => a.stage === "ingest");
  const ops = (found.config["operations"] ?? []) as Record<string, unknown>[];
  return { versionId: found.version_id, rawId: raw?.version_id ?? null, ops };
}

function Spark({ values, label }: { values: number[]; label: string }) {
  const top = Math.max(1, ...values);
  return (
    <span className="col-spk" role="img" aria-label={label}>
      {values.map((v, i) => (
        <i key={i} style={{ height: `${String(Math.round((v / top) * 100))}%` }} />
      ))}
    </span>
  );
}

function Cats({ items, more }: { items: { value: string; count: number }[]; more: number }) {
  const top = Math.max(1, ...items.map((i) => i.count));
  return (
    <span className="col-cats">
      {items.map((i) => (
        <span key={i.value}>
          {i.value}
          <i style={{ width: `${String(Math.round((i.count / top) * 28))}px` }} />
          <small>{count(i.count)}</small>
        </span>
      ))}
      {more ? <span className="more">+{count(more)} more</span> : null}
    </span>
  );
}

function encodingCell(col: Col) {
  if (col.role === "target") return <td className="enc none">—</td>;
  if (col.encoding === null) {
    return col.kind === "numeric" ? (
      <td className="enc none">—</td>
    ) : (
      <td className="enc">one_hot<small>default</small></td>
    );
  }
  return <td className="enc">{col.encoding}</td>;
}

function Detail({ col }: { col: Col }) {
  const d = col.distribution as unknown as Numeric | Categorical;
  if (d.type === "categorical") {
    const top = Math.max(1, ...d.top.map((t) => t.count));
    return (
      <div className="col-detail">
        <div className="col-bars" role="img" aria-label={`${col.name}: ${d.top.map((t) => `${t.value} ${String(t.count)}`).join(", ")}`}>
          {d.top.map((t) => (
            <Fragment key={t.value}>
              <span className="mono">{t.value}</span>
              <span className="bar"><i style={{ width: `${String((t.count / top) * 100)}%` }} /></span>
              <span className="mono r">{count(t.count)}</span>
            </Fragment>
          ))}
        </div>
        <p className="col-note">
          {count(col.distinct)}{col.distinct_capped ? "+" : ""} categories{d.more ? `; the ${String(d.top.length)} largest shown, ${count(d.more)} more` : ""}
          {col.order ? <> · order: <span className="mono">{col.order.join(" < ")}</span></> : null}
        </p>
      </div>
    );
  }
  const h = d.histogram;
  if (!h || d.min === null || d.max === null) return <p className="col-note">No values among the accepted rows.</p>;
  const top = Math.max(1, ...h.counts);
  const span = d.max - d.min;
  const medianAt = span > 0 && d.median !== null ? ((d.median - d.min) / span) * 100 : null;
  const first = h.counts.slice(0, 10);
  const second = h.counts.slice(10);
  const bin = (k: number) => `${num(h.edges[k])}–${num(h.edges[k + 1])}`;
  return (
    <div className="col-detail">
      <div>
        <div className="col-hist" role="img" aria-label={`${col.name}, ${String(h.counts.length)} bins from ${num(d.min)} to ${num(d.max)}; the table beside lists every bin`}>
          {h.counts.map((c, i) => <i key={i} style={{ height: `${String(Math.round((c / top) * 100))}%` }} />)}
          {medianAt !== null ? (
            <span className="med" style={{ left: `${medianAt.toFixed(1)}%` }}><span>median {num(d.median)}</span></span>
          ) : null}
        </div>
        <div className="col-ax">
          {[0, 5, 10, 15, 20].map((k) => <span key={k}>{num(h.edges[k])}</span>)}
        </div>
        <p className="col-note">
          {String(h.counts.length)} bins of {num(span / h.counts.length)} · {count(h.counts.reduce((a, b) => a + b, 0))} values · {count(col.distinct)}{col.distinct_capped ? "+" : ""} distinct
        </p>
      </div>
      <div className="col-bins">
        {[first, second].map((part, j) => (
          <table key={j}>
            <thead><tr><th>Bin</th><th className="r">Rows</th></tr></thead>
            <tbody>
              {part.map((c, i) => (
                <tr key={i}><td>{bin(i + j * 10)}</td><td className="r">{count(c)}</td></tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
    </div>
  );
}

function Row({ col, open, onToggle }: { col: Col; open: boolean; onToggle: () => void }) {
  const d = col.distribution as unknown as Numeric | Categorical;
  const ignored = col.encoding === "ignore";
  return (
    <>
      <tr
        className={`${open ? "col-open" : ""}${ignored ? " ign" : ""}`}
        aria-expanded={open}
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className="name"><Icon name={open ? "down" : "chevron"} />{col.name}</td>
        <td className="kind">{col.kind}</td>
        <td className="role">{col.role === "target" ? <span className="badge">target</span> : "feature"}</td>
        {encodingCell(col)}
        <td className={`r${col.missing ? "" : " zero"}`}>{count(col.missing)}</td>
        <td className="r">{col.distinct_capped ? `> ${count(col.distinct)}` : count(col.distinct)}</td>
        {d.type === "numeric" ? (
          <>
            <td>{d.sparkline ? <Spark values={d.sparkline} label={`${col.name}: min ${num(d.min)}, median ${num(d.median)}, max ${num(d.max)}; 12 bins ${d.sparkline.join(", ")}`} /> : null}</td>
            <td><span className="col-five">{num(d.min)} · <b>{num(d.median)}</b> · {num(d.max)}</span></td>
          </>
        ) : (
          <td colSpan={2}><Cats items={d.top.slice(0, 5)} more={d.more + Math.max(0, d.top.length - 5)} /></td>
        )}
      </tr>
      {open ? (
        <tr className="col-exp">
          <td colSpan={8}><Detail col={col} /></td>
        </tr>
      ) : null}
    </>
  );
}

function SummaryLine({ data, onShowRejected }: { data: Summary; onShowRejected: (reason: string) => void }) {
  const features = data.columns.filter((c) => c.role === "feature");
  const numeric = features.filter((c) => c.kind === "numeric").length;
  const categorical = features.length - numeric;
  const target = data.columns.find((c) => c.role === "target");
  const kinds = categorical === 0 ? "all numeric" : numeric === 0 ? "all categorical" : `${String(numeric)} numeric, ${String(categorical)} categorical`;
  const left = Object.entries(data.not_counted);
  const leftRows = left.reduce((a, [, n]) => a + n, 0);
  return (
    <div className="col-sum">
      <span><b>{count(data.columns.length)}</b> columns</span>
      <span><b>{count(features.length)}</b> features, {kinds}</span>
      <span>
        target <span className="mono">{data.target}</span> · {data.task}
        {data.task === "classification" && target ? `, ${count(target.distinct)} classes` : ""}
      </span>
      <span>over <b>{count(data.counted)}</b> accepted rows</span>
      {leftRows ? (
        <span className="left">
          <b>{count(leftRows)}</b> rows not counted ({left.map(([code]) => code).join(", ")}) ·{" "}
          <button type="button" className="col-link" onClick={() => { onShowRejected(left[0]?.[0] ?? ""); }}>Show them</button>
        </span>
      ) : null}
    </div>
  );
}

export function ColumnsTab({ versionId, stage, config, lineage, active, onShowRows, onEditTransform }: {
  versionId: string;
  stage: string;
  config: Record<string, unknown>;
  lineage: Lineage | undefined;
  active: boolean;
  onShowRows: (reason: string) => void;
  onEditTransform: (rawId: string, fromId: string) => void;
}) {
  const columns = useColumns(versionId, active);
  const [open, setOpen] = useState<string | null>(null);
  const [kind, setKind] = useState("all");
  const [role, setRole] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "schema", desc: false });
  const transform = transformOf(versionId, stage, config, lineage);

  const shown = useMemo(() => {
    const all = columns.data?.columns ?? [];
    const kept = all.filter((c) => (kind === "all" || c.kind === kind) && (role === "all" || c.role === role));
    if (sort.key === "schema") return kept;
    const key = sort.key;
    const value = (c: Col): string | number =>
      key === "missing" ? c.missing : key === "distinct" ? c.distinct : key === "encoding" ? (c.encoding ?? "") : key === "kind" ? c.kind : key === "role" ? c.role : c.name;
    const sorted = [...kept].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      return typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    });
    return sort.desc ? sorted.reverse() : sorted;
  }, [columns.data, kind, role, sort]);

  if (columns.isPending) {
    return (
      <div className="col-tab" aria-busy="true">
        <p className="note2">Computing the column figures · once, then kept for this version</p>
        {[0, 1, 2, 3].map((i) => <span key={i} className="skeleton" style={{ height: 28, display: "block" }} />)}
      </div>
    );
  }
  if (columns.isError) {
    return <ErrorState error={columns.error} title="The column figures could not be computed" onRetry={() => void columns.refetch()} />;
  }
  const data = columns.data;
  const header = (key: SortKey, label: string, right = false) => (
    <th className={right ? "r" : undefined} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
      <button type="button" onClick={() => { setSort((s) => ({ key, desc: s.key === key ? !s.desc : false })); }}>
        {label}
        {sort.key === key ? <Icon name="sort" /> : null}
      </button>
    </th>
  );
  return (
    <div className="col-tab">
      {transform ? (
        <div className="col-ops">
          <Icon name="info" />
          <span>Made by a transform of <span className="mono">{transform.rawId ?? "its raw version"}</span>:</span>
          {transform.ops.map((op, i) => (
            <Fragment key={i}>
              {i ? <span className="sep">·</span> : null}
              <span className="op">{describeOp(op)}</span>
            </Fragment>
          ))}
          <span className="spacer" />
          {transform.rawId ? (
            <Button size="sm" onClick={() => { onEditTransform(transform.rawId ?? "", transform.versionId); }}>Edit the transform…</Button>
          ) : null}
        </div>
      ) : null}
      <div className="col-bar">
        <SummaryLine data={data} onShowRejected={onShowRows} />
        <div className="col-filt">
          <label>
            <span className="sr-only">Kind</span>
            <select className="select" value={kind} onChange={(e) => { setKind(e.target.value); }}>
              <option value="all">Kind: all</option>
              <option value="numeric">Kind: numeric</option>
              <option value="categorical">Kind: categorical</option>
            </select>
          </label>
          <label>
            <span className="sr-only">Role</span>
            <select className="select" value={role} onChange={(e) => { setRole(e.target.value); }}>
              <option value="all">Role: all</option>
              <option value="feature">Role: feature</option>
              <option value="target">Role: target</option>
            </select>
          </label>
          <span>{count(shown.length)} of {count(data.columns.length)}</span>
        </div>
      </div>
      {data.counted === 0 ? (
        <EmptyState icon="ban" title="Every row of this version was rejected." action={<Button size="sm" onClick={() => { onShowRows(Object.keys(data.not_counted)[0] ?? ""); }}>Show the rejected rows</Button>}>
          The columns are listed with their kind and role; their figures read &quot;—&quot;.
        </EmptyState>
      ) : null}
      <div className="table-wrap col-wrap">
        <table className="t dense col-t">
          <caption className="sr-only">Columns of {versionId} over {count(data.counted)} accepted rows</caption>
          <thead>
            <tr>
              {header("name", "Column")}
              {header("kind", "Kind")}
              {header("role", "Role")}
              {header("encoding", "Encoding")}
              {header("missing", "Missing", true)}
              {header("distinct", "Distinct", true)}
              <th>Distribution</th>
              <th>min · median · max</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((col) => (
              <Row key={col.name} col={col} open={open === col.name} onToggle={() => { setOpen((o) => (o === col.name ? null : col.name)); }} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
