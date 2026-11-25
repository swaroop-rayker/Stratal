// SCR-15 transform editor (approved hi-fi SCR-15_v1; SCREENS.md v4; DATA-7.1 to DATA-7.5,
// UI-2.3): the operations for a table's raw version, generated from the transform stage's
// schema, a preview of the first rows computed by the stage's own code, and Run.
import * as Menu from "@radix-ui/react-dropdown-menu";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Problem, type Schemas } from "../../api/client";
import { useLineage, useStageSchema, useVersion } from "../../api/queries";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";

type Preview = Schemas["TransformPreview"];
type Op = Record<string, unknown> & { op: string };
interface Col { name: string; kind: string; role: string; encoding?: string | null; order?: string[] | null }
interface JsonSchema {
  description?: string;
  enum?: string[];
  anyOf?: JsonSchema[];
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
}

export const DEFAULTS: Record<string, Op> = {
  drop: { op: "drop", columns: [] },
  rename: { op: "rename", from: "", to: "" },
  reorder: { op: "reorder", columns: [] },
  set_kind: { op: "set_kind", column: "", kind: "categorical" },
  set_target: { op: "set_target", column: "" },
  set_encoding: { op: "set_encoding", column: "", encoding: "one_hot" },
  derive: { op: "derive", name: "", expression: "" },
  filter: { op: "filter", expression: "" },
};
const ORDER = ["drop", "rename", "reorder", "set_kind", "set_target", "set_encoding", "derive", "filter"];
const LINES: Record<string, string> = {
  drop: "remove columns (not the target)",
  rename: "give a column a new name",
  reorder: "set the order of every column",
  set_kind: "numeric ↔ categorical",
  set_target: "choose the target; the old one becomes a feature",
  set_encoding: "one_hot, embedding, ordinal or ignore",
  derive: "a new column from a formula",
  filter: "keep rows where a condition holds",
};
const DRAFT = (raw: string) => `stratal.transform.${raw}`;
const PREVIEW_ROWS = 50;

/** A problem's messages by operation and field, from errors[].loc (rule 26 and its As built:
 * ["stages","transform","operations", i, (tag,) field] or ending at the index). */
export function problemsByOp(problem: Problem | null): Map<number, Map<string, string>> {
  const out = new Map<number, Map<string, string>>();
  for (const e of problem?.errors ?? []) {
    const loc = e.loc.filter((p) => p !== "body");
    const at = loc.indexOf("operations");
    const index = at >= 0 ? loc[at + 1] : undefined;
    if (typeof index !== "number") continue;
    const rest = loc.slice(at + 2).filter((p): p is string => typeof p === "string");
    const field = rest.length ? (rest[rest.length - 1] ?? "op") : "op";
    const words = e.code === "string_too_short" || e.code === "missing" ? "required" : e.code === "too_short" ? "choose at least one" : e.msg;
    const forOp = out.get(index) ?? new Map<string, string>();
    if (!forOp.has(field)) forOp.set(field, words);
    out.set(index, forOp);
  }
  return out;
}

function useOpSchemas() {
  const schema = useStageSchema("transform", true);
  return useMemo(() => {
    const defs = ((schema.data ?? {}) as { $defs?: Record<string, JsonSchema> }).$defs ?? {};
    const byOp: Record<string, JsonSchema> = {};
    for (const def of Object.values(defs)) {
      const tag = def.properties?.["op"] as { const?: string } | undefined;
      if (tag?.const) byOp[tag.const] = def;
    }
    return { byOp, ready: schema.isSuccess };
  }, [schema.data, schema.isSuccess]);
}

function enumOf(s: JsonSchema | undefined): string[] | null {
  if (!s) return null;
  if (s.enum) return s.enum;
  const inner = s.anyOf?.find((a) => a.enum);
  return inner?.enum ?? null;
}

function Field({ name, label, error, children }: { name: string; label?: string; error?: string; children: React.ReactNode }) {
  return (
    <>
      <span className="lab">{label ?? name}</span>
      {children}
      {error ? <span className="sr-only" id={`err-${name}`}>{error}</span> : null}
    </>
  );
}

function ColumnSelect({ value, columns, onChange, bad, label }: { value: string; columns: Col[]; onChange: (v: string) => void; bad: boolean; label: string }) {
  return (
    <select className={`select${bad ? " bad" : ""}`} value={value} aria-label={label} aria-invalid={bad || undefined} onChange={(e) => { onChange(e.target.value); }}>
      <option value="" disabled>choose a column</option>
      {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
      {value && !columns.some((c) => c.name === value) ? <option value={value}>{value}</option> : null}
    </select>
  );
}

function OrderEditor({ order, onChange, label }: { order: string[]; onChange: (o: string[]) => void; label: string }) {
  const [focus, setFocus] = useState(0);
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j] ?? "", next[i] ?? ""];
    onChange(next);
    setFocus(j);
  };
  const numeric = order.length > 0 && order.every((v) => v.trim() !== "" && !Number.isNaN(Number(v)));
  return (
    <div className="tx-order">
      <span className="hint">
        <span>order · {String(order.length)} categories, first to last</span>
        <span className="spacer" />
        {numeric ? <Button size="sm" onClick={() => { onChange([...order].sort((a, b) => Number(a) - Number(b))); }}>Sort as numbers</Button> : null}
      </span>
      <div className="line" role="listbox" aria-label={label}>
        {order.map((v, i) => (
          <span
            key={v}
            className="o"
            role="option"
            aria-selected={i === focus}
            tabIndex={i === focus ? 0 : -1}
            onFocus={() => { setFocus(i); }}
            onKeyDown={(e) => {
              if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); move(i, -1); }
              else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); move(i, 1); }
              else if (e.key === "ArrowLeft") { e.preventDefault(); setFocus(Math.max(0, i - 1)); }
              else if (e.key === "ArrowRight") { e.preventDefault(); setFocus(Math.min(order.length - 1, i + 1)); }
            }}
          >
            {v}
          </span>
        ))}
      </div>
      <span className="hint">←/→ to move between · Alt+←/Alt+→ to move one</span>
    </div>
  );
}

function OpCard({ op, index, total, schema, columns, sample, errors, summary, onChange, onMove, onRemove, onDuplicate }: {
  op: Op;
  index: number;
  total: number;
  schema: JsonSchema | undefined;
  columns: Col[];
  sample: string[];
  errors: Map<string, string> | undefined;
  summary: string | null;
  onChange: (op: Op) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
  onDuplicate: () => void;
}) {
  const bad = (f: string) => Boolean(errors?.get(f));
  const set = (key: string, value: unknown) => { onChange({ ...op, [key]: value }); };
  const features = columns.filter((c) => c.role === "feature");
  const props = Object.entries(schema?.properties ?? {}).filter(([k]) => k !== "op");
  const onKey = (e: KeyboardEvent) => {
    if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); onMove(-1); }
    if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); onMove(1); }
  };
  const field = (name: string, prop: JsonSchema) => {
    const label = name === "from" ? "from" : name;
    const value = op[name];
    if (name === "column" || name === "from") {
      const pool = op.op === "set_encoding" ? features : op.op === "set_target" ? columns.filter((c) => c.role !== "target") : columns;
      return <Field key={name} name={name} label={label} error={errors?.get(name)}><ColumnSelect label={`${op.op} ${name}`} value={typeof value === "string" ? value : ""} columns={pool} bad={bad(name)} onChange={(v) => { set(name, v); }} /></Field>;
    }
    if (name === "columns" && op.op === "drop") {
      const chosen = Array.isArray(value) ? (value as string[]) : [];
      return (
        <Field key={name} name={name} error={errors?.get(name)}>
          {chosen.map((c) => (
            <span key={c} className={`tag${bad(name) ? " bad" : ""}`}>
              {c}
              <button type="button" aria-label={`Keep ${c}`} onClick={() => { set(name, chosen.filter((x) => x !== c)); }}><Icon name="x" /></button>
            </span>
          ))}
          <select className="select" value="" aria-label="add a column to drop" onChange={(e) => { if (e.target.value) set(name, [...chosen, e.target.value]); }}>
            <option value="">add a column</option>
            {columns.filter((c) => c.role !== "target" && !chosen.includes(c.name)).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </Field>
      );
    }
    if (name === "columns" && op.op === "reorder") {
      const order = Array.isArray(value) && (value as string[]).length ? (value as string[]) : columns.map((c) => c.name);
      return <OrderEditor key={name} order={order} label="column order" onChange={(o) => { set(name, o); }} />;
    }
    if (name === "order") {
      if (op["encoding"] !== "ordinal") return null;
      const order = Array.isArray(value) && (value as string[]).length ? (value as string[]) : sample;
      return <OrderEditor key={name} order={order} label="category order" onChange={(o) => { set(name, o); }} />;
    }
    const choices = enumOf(prop);
    if (choices) {
      const optional = prop.anyOf?.some((a) => a.type === "null");
      return (
        <Field key={name} name={name} error={errors?.get(name)}>
          <select className={`select${bad(name) ? " bad" : ""}`} aria-label={`${op.op} ${name}`} value={typeof value === "string" ? value : ""} onChange={(e) => { set(name, e.target.value || null); }}>
            {optional ? <option value="">inferred</option> : null}
            {choices.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
      );
    }
    const wide = name === "expression";
    return (
      <Field key={name} name={name} error={errors?.get(name)}>
        <input
          className={`tin${bad(name) ? " bad" : ""}${wide ? " wide" : ""}`}
          aria-label={`${op.op} ${name}`}
          aria-invalid={bad(name) || undefined}
          value={typeof value === "string" ? value : ""}
          spellCheck={false}
          onChange={(e) => { set(name, e.target.value); }}
        />
      </Field>
    );
  };
  const messages = [...(errors?.entries() ?? [])];
  return (
    <div className={`tx-op${messages.length ? " bad" : ""}`} onKeyDown={onKey} role="group" aria-label={`Operation ${String(index + 1)}: ${op.op}`}>
      <div className="hd">
        <span className="no">{index + 1}</span>
        <select className="select op" aria-label={`Operation ${String(index + 1)} type`} value={op.op} onChange={(e) => { onChange({ ...(DEFAULTS[e.target.value] ?? { op: e.target.value }) }); }}>
          {ORDER.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <span className="spacer" />
        <Menu.Root>
          <Menu.Trigger asChild>
            <button type="button" className="icon-btn" aria-label={`Operation ${String(index + 1)} actions`}><Icon name="more" /></button>
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content className="menu-pop" align="end" sideOffset={4}>
              <Menu.Item disabled={index === 0} onSelect={() => { onMove(-1); }}>Move up<kbd>Alt ↑</kbd></Menu.Item>
              <Menu.Item disabled={index === total - 1} onSelect={() => { onMove(1); }}>Move down<kbd>Alt ↓</kbd></Menu.Item>
              <Menu.Item onSelect={onDuplicate}>Duplicate</Menu.Item>
              <Menu.Item onSelect={onRemove}>Remove</Menu.Item>
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <div className="tx-fields">{props.map(([name, prop]) => (name === "order" || (name === "columns" && op.op === "reorder") ? null : field(name, prop)))}</div>
      {props.filter(([name]) => name === "order" || (name === "columns" && op.op === "reorder")).map(([name, prop]) => field(name, prop))}
      {messages.length ? (
        messages.map(([f, m]) => (
          <div key={f} className="tx-err"><Icon name="x" /><span>{f === "op" ? m : `${f}: ${m}`}</span></div>
        ))
      ) : summary ? (
        <div className={summary.includes("cast_failed") && /[1-9][\d,]* cast_failed/.test(summary) ? "tx-warn" : "tx-eff"}>
          <Icon name={/[1-9][\d,]* cast_failed/.test(summary) ? "alert" : "info"} />
          <span>{summary}</span>
        </div>
      ) : null}
    </div>
  );
}

/** The resulting columns in order, each dropped one struck through where it was. */
export function chipOrder(base: Col[], result: Col[], dropped: Col[], ops: Op[]): { col: Col; gone: boolean }[] {
  const renamedFrom = new Map(ops.filter((o) => o.op === "rename").map((o) => [String(o["to"]), String(o["from"])]));
  const origin = (c: Col) => {
    const i = base.findIndex((b) => b.name === (renamedFrom.get(c.name) ?? c.name));
    return i < 0 ? Number.POSITIVE_INFINITY : i;
  };
  const gone = [...dropped].sort((a, b) => base.indexOf(a) - base.indexOf(b));
  const out: { col: Col; gone: boolean }[] = [];
  for (const c of result) {
    while (gone.length && base.indexOf(gone[0] as Col) < origin(c)) out.push({ col: gone.shift() as Col, gone: true });
    out.push({ col: c, gone: false });
  }
  return [...out, ...gone.map((col) => ({ col, gone: true }))];
}

function castColumn(detail: string | null | undefined): string | null {
  const m = /^'([^']+)' is /.exec(detail ?? "");
  return m?.[1] ?? null;
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("en-US", { useGrouping: false }) : String(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function PreviewTable({ preview, changed, stale }: { preview: Preview; changed: Set<string>; stale: boolean }) {
  const cols = preview.columns as unknown as Col[];
  const STATUS: Record<string, string> = { accepted: "acc", rejected: "rej", filtered: "fil", duplicate: "dup" };
  const anyReason = preview.rows.some((r) => r.reason_code);
  return (
    <div className={`tx-scroll${stale ? " stale" : ""}`}>
      <table className="tx-t">
        <caption className="sr-only">The first {preview.rows.length} rows after the operations</caption>
        <thead>
          <tr>
            <th className="pin">row</th>
            <th>status</th>
            {anyReason ? <th>reason</th> : null}
            {cols.map((c) => (
              <th key={c.name} className={`${c.kind === "numeric" ? "r " : ""}${changed.has(c.name) ? "chg" : ""}`}>
                {c.name}
                {c.role === "target" ? <small>target{changed.has(c.name) ? ` · ${c.kind}` : ""}</small> : changed.has(c.name) ? <small>{c.kind}{c.encoding ? ` · ${c.encoding}` : ""}</small> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((r) => {
            const failed = r.reason_code === "cast_failed" ? castColumn(r.reason_detail) : null;
            return (
              <tr key={r.row_key}>
                <td className="pin">{r.row_key}</td>
                <td className="ui"><span className={`rs ${STATUS[r.status] ?? "dup"}`}><i />{r.status}</span></td>
                {anyReason ? <td>{r.reason_code ? <span className="reason" title={r.reason_detail ?? undefined}>{r.reason_code}</span> : null}</td> : null}
                {cols.map((c) => {
                  const v = r.values[c.name];
                  if (failed === c.name) {
                    return <td key={c.name} className={`bad${c.kind === "numeric" ? " r" : ""}`}><Icon name="x" /> {cell(v)}</td>;
                  }
                  const numeric = c.kind === "numeric";
                  return <td key={c.name} className={`${numeric ? "r" : ""}${changed.has(c.name) ? " chg" : ""}`}>{cell(v)}</td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function since(t: number, now: number): string {
  const s = Math.max(0, (now - t) / 1000);
  return s < 60 ? `${s.toFixed(1)} s ago` : `${Math.round(s / 60)} min ago`;
}

function readDraft(versionId: string): Op[] | null {
  try {
    const raw = sessionStorage.getItem(DRAFT(versionId));
    return raw ? (JSON.parse(raw) as Op[]) : null;
  } catch {
    return null;
  }
}

export function TransformScreen() {
  const { versionId = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const from = params.get("from");
  const version = useVersion(versionId);
  const lineage = useLineage(versionId);
  const loaded = useVersion(from ?? "", Boolean(from));
  const v = version.data;
  useCrumbs([
    { label: "Datasets", to: "/datasets" },
    ...(v ? [{ label: v.dataset, to: `/datasets/${v.dataset}` }] : []),
    { label: versionId, id: true, to: `/versions/${versionId}` },
    { label: "Transform" },
  ]);

  if (version.isPending || (from && loaded.isPending)) {
    return (
      <div className="scr15" aria-busy="true">
        <Skeleton width={320} height={22} />
        <div className="tx-body">
          <div className="tx-col">{[0, 1, 2].map((i) => <span key={i} className="skeleton" style={{ height: 56, display: "block" }} />)}</div>
          <div className="tx-col">{[0, 1, 2, 3, 4].map((i) => <span key={i} className="skeleton" style={{ height: 24, display: "block" }} />)}</div>
        </div>
      </div>
    );
  }
  if (version.isError || !v) {
    return <div className="scr15"><ErrorState error={version.error} title={`Version ${versionId} could not be loaded`} onRetry={() => void version.refetch()} /></div>;
  }
  if (v.dtype !== "tabular" || v.stage !== "ingest") {
    const raw = v.dtype === "tabular" ? lineage.data?.ancestors.find((a) => a.stage === "ingest") : undefined;
    return (
      <div className="scr15">
        <EmptyState icon="ban" title="Transforms apply to a table's raw version." action={raw ? <Button size="sm" onClick={() => void navigate(`/versions/${raw.version_id}/transform`)}>Transform {raw.version_id}</Button> : <Button size="sm" onClick={() => void navigate(`/versions/${versionId}`)}>Open the version</Button>}>
          <span className="mono">{versionId}</span> is {v.dtype === "tabular" ? `a ${v.stage} version of ${v.dataset}` : "not a table"}.
          {raw ? <> Its raw version is <span className="mono">{raw.version_id}</span>.</> : null}
        </EmptyState>
      </div>
    );
  }
  const loadedOps = (loaded.data?.config["operations"] ?? []) as Op[];
  return <Editor key={`${versionId}:${from ?? ""}`} version={v} from={from} loadedOps={loadedOps} />;
}

function Editor({ version: v, from, loadedOps }: { version: Schemas["Version"]; from: string | null; loadedOps: Op[] }) {
  const versionId = v.version_id;
  const navigate = useNavigate();
  const { byOp } = useOpSchemas();
  // The list starts from the session's draft, else the loaded transform, else empty.
  const [initial] = useState(() => {
    const draft = readDraft(versionId);
    return { ops: draft ?? loadedOps, note: Boolean(draft && from && JSON.stringify(draft) !== JSON.stringify(loadedOps)) };
  });
  const [ops, setOps] = useState<Op[]>(initial.ops);
  const [draftNote, setDraftNote] = useState(initial.note);
  const [mode, setMode] = useState<"split" | "transform">("split");
  const [later, setLater] = useState(false);
  const [split, setSplit] = useState({ train: 0.8, val: 0.1, test: 0.1, seed: 0 });
  const [adding, setAdding] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    try {
      sessionStorage.setItem(DRAFT(versionId), JSON.stringify(ops));
    } catch {
      /* private mode: the draft is a convenience */
    }
  }, [ops, versionId]);

  // The preview, 300 ms after the last edit; the last result stays, dimmed, meanwhile.
  const [debounced, setDebounced] = useState<Op[]>(initial.ops);
  useEffect(() => {
    const t = setTimeout(() => { setDebounced(ops); }, 300);
    return () => { clearTimeout(t); };
  }, [ops]);
  const preview = useQuery({
    queryKey: ["versions", versionId, "transform-preview", JSON.stringify(debounced)],
    queryFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/transform/preview", {
          params: { path: { version_id: versionId } },
          body: { operations: debounced, limit: PREVIEW_ROWS },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const base = useQuery({
    queryKey: ["versions", versionId, "transform-preview", "[]"],
    queryFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/transform/preview", {
          params: { path: { version_id: versionId } },
          body: { operations: [], limit: PREVIEW_ROWS },
        }),
      ),
    staleTime: Infinity,
  });
  useEffect(() => {
    const t = setInterval(() => { setNow(Date.now()); }, 1000);
    return () => { clearInterval(t); };
  }, []);

  const problem = preview.error instanceof ApiError && preview.error.problem.status === 422 ? preview.error.problem : null;
  const errors = problemsByOp(problem);
  const result = preview.data;
  const baseCols = useMemo(() => (base.data?.columns ?? []) as unknown as Col[], [base.data]);
  const stepCols = (i: number): Col[] => {
    if (i === 0) return baseCols;
    const step = result?.steps[i - 1];
    return step ? (step.columns as unknown as Col[]) : baseCols;
  };
  const resultCols = useMemo(() => (result?.columns ?? baseCols) as unknown as Col[], [result, baseCols]);
  const changed = useMemo(() => {
    const before = new Map(baseCols.map((c) => [c.name, c]));
    const out = new Set<string>();
    for (const c of resultCols) {
      const b = before.get(c.name);
      if (!b || b.kind !== c.kind || b.role !== c.role || (b.encoding ?? null) !== (c.encoding ?? null)) out.add(c.name);
    }
    return out;
  }, [baseCols, resultCols]);
  const renamed = new Set(ops.filter((o) => o.op === "rename" && typeof o["to"] === "string").map((o) => String(o["to"])));
  // Dropped: in the raw schema, not in the result, and not renamed away.
  const renamedFrom = new Set(ops.filter((o) => o.op === "rename").map((o) => String(o["from"])));
  const dropped = baseCols.filter((c) => !resultCols.some((r) => r.name === c.name) && !renamedFrom.has(c.name));

  const run = useMutation({
    mutationFn: () => {
      const stages: Record<string, unknown> = { transform: { operations: ops } };
      const defaults: Record<string, number> = { train: 0.8, val: 0.1, test: 0.1, seed: 0 };
      const changedSplit = Object.fromEntries(Object.entries(split).filter(([k, val]) => val !== defaults[k]));
      if (mode === "split" && Object.keys(changedSplit).length) stages["split"] = changedSplit;
      return call(() =>
        api.POST("/api/v1/jobs", {
          body: { kind: "pipeline", from_version: versionId, stages, until_stage: mode === "transform" ? "transform" : null } as never,
        }),
      );
    },
    onSuccess: (job) => {
      try {
        sessionStorage.removeItem(DRAFT(versionId));
      } catch {
        /* ignore */
      }
      void navigate(`/jobs/${(job as { id: string }).id}`);
    },
  });

  const list = ops;
  const problemCount = problem ? problem.errors.length : 0;
  const empty = list.length === 0;
  const previewDown = preview.isError && !problem;
  const why = empty ? "Add an operation first" : problemCount ? `${String(problemCount)} ${problemCount === 1 ? "problem" : "problems"}` : previewDown ? "The preview could not be computed" : null;
  const setAt = (i: number, op: Op) => { setOps(list.map((o, k) => (k === i ? op : o))); };
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    const next = [...list];
    [next[i], next[j]] = [next[j] as Op, next[i] as Op];
    setOps(next);
  };
  const target = resultCols.find((c) => c.role === "target");
  const features = resultCols.filter((c) => c.role === "feature");
  const numericCount = features.filter((c) => c.kind === "numeric").length;
  const byStatus = (result?.counts as { by_status?: Record<string, number> } | undefined)?.by_status ?? {};

  return (
    <div className="scr15">
      <div className="tx-head">
        <div>
          <h1>Transform <span className="mono">{v.version_id}</span><span className="stage-tag">ingest</span></h1>
          <div className="sub">
            {v.dataset} · <span className="mono">{count(v.row_count)}</span> rows · <span className="mono">{count(baseCols.length)}</span> columns
            {v.created_by_job_id ? <> · made by <span className="mono">{v.created_by_job_id}</span></> : null}
            {" · "}<Link className="mono" to={`/versions/${v.version_id}`}>Open the version</Link>
          </div>
        </div>
        <span className="spacer" />
        <div className="tx-run">
          {why ? <span className={problemCount ? "badge err" : "badge"} id="tx-why">{why}</span> : null}
          <fieldset className="tx-modes">
            <legend className="sr-only">What Run does</legend>
            <label><input type="radio" name="tx-mode" checked={mode === "split"} onChange={() => { setMode("split"); }} />Through clean and split</label>
            <label><input type="radio" name="tx-mode" checked={mode === "transform"} onChange={() => { setMode("transform"); }} />Transform only</label>
          </fieldset>
          <Button variant="ghost" onClick={() => { if (JSON.stringify(list) === JSON.stringify(loadedOps) || window.confirm("Discard your edits and go back to the list this page opened with?")) setOps(loadedOps); }}>Reset</Button>
          <Button
            variant="primary"
            aria-disabled={Boolean(why) || run.isPending || undefined}
            aria-describedby={why ? "tx-why" : undefined}
            className={why ? "is-disabled" : undefined}
            onClick={() => { if (!why && !run.isPending) run.mutate(); }}
          >
            <Icon name="play" />
            {run.isPending ? "Starting…" : "Run"}
          </Button>
        </div>
      </div>
      {from ? (
        <div className="tx-note">
          <Icon name="info" />
          <span>
            Starts from the raw version. {draftNote ? <>Your unsaved draft is shown, not the {String(loadedOps.length)} operations of <span className="mono">{from}</span>.</> : <>Loaded the {String(loadedOps.length)} operations of <span className="mono">{from}</span>. Running a changed list makes a new version beside it.</>}
          </span>
          {draftNote ? <Button size="sm" onClick={() => { setOps(loadedOps); setDraftNote(false); }}>Use the loaded list</Button> : null}
        </div>
      ) : null}
      {run.isError ? <ErrorState error={run.error} title="The job could not be started" /> : null}

      <div className="tx-body">
        <div className="tx-col tx-left">
          <h2>Operations <span className="count">{list.length}</span><span className="aside">applied in order</span></h2>
          {empty ? (
            <EmptyState icon="list" title="No operations yet.">The preview shows the raw version&apos;s first rows.</EmptyState>
          ) : null}
          {list.map((op, i) => {
            const sampleCol = typeof op["column"] === "string" ? op["column"] : "";
            const sample = [...new Set((result?.rows ?? []).map((r) => r.values[sampleCol]).filter((x): x is string | number => x !== null && x !== undefined).map(String))].sort();
            return (
              <OpCard
                key={i}
                op={op}
                index={i}
                total={list.length}
                schema={byOp[op.op]}
                columns={stepCols(i)}
                sample={sample}
                errors={errors.get(i)}
                summary={result?.steps[i]?.summary ?? null}
                onChange={(next) => { setAt(i, next); }}
                onMove={(d) => { move(i, d); }}
                onRemove={() => { setOps(list.filter((_, k) => k !== i)); }}
                onDuplicate={() => { setOps([...list.slice(0, i + 1), { ...op }, ...list.slice(i + 1)]); }}
              />
            );
          })}
          <Menu.Root open={adding} onOpenChange={setAdding}>
            <Menu.Trigger asChild>
              <Button size="sm" className="tx-add"><Icon name="plus" />Add operation</Button>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu-pop tx-menu" align="start" sideOffset={4}>
                {ORDER.filter((o) => byOp[o] ?? DEFAULTS[o]).map((o) => (
                  <Menu.Item key={o} className="it" onSelect={() => { setOps([...list, { ...(DEFAULTS[o] as Op) }]); }}>
                    <b>{o}</b><span>{LINES[o]}</span>
                  </Menu.Item>
                ))}
                <div className="note"><Icon name="info" /><span>Scaling, imputing and target encoding are fitted on the train split by the model, not here (ML-1.3).</span></div>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
          <section className={`tx-later${later ? " open" : ""}${mode === "transform" ? " off" : ""}`}>
            <header>
              <button type="button" aria-expanded={later} onClick={() => { setLater(!later); }}>
                <Icon name="chevron" />Later stages
              </button>
              <span className="aside">{mode === "transform" ? "not run" : `clean_table · split ${String(split.train)} / ${String(split.val)} / ${String(split.test)} · seed ${String(split.seed)}`}</span>
            </header>
            {later ? (
              <div className="b">
                {(["train", "val", "test", "seed"] as const).map((k) => (
                  <label key={k} className="field">
                    <span className="mono">split.{k}</span>
                    <input
                      className="input mono"
                      type="number"
                      step={k === "seed" ? 1 : 0.05}
                      min={0}
                      max={k === "seed" ? undefined : 1}
                      value={split[k]}
                      disabled={mode === "transform"}
                      onChange={(e) => { setSplit({ ...split, [k]: Number(e.target.value) }); }}
                    />
                  </label>
                ))}
                <small className="note2">clean_table has no settings: exact duplicates are marked by content hash.</small>
              </div>
            ) : null}
          </section>
        </div>

        <div className="tx-col tx-right">
          <section className="tx-schema">
            <header>
              Resulting schema
              <span className="aside">
                {count(resultCols.length)} columns · {count(features.length)} features ({String(numericCount)} numeric, {String(features.length - numericCount)} categorical)
                {target ? <> · target <span className="mono">{target.name}</span></> : null}
              </span>
            </header>
            <div className="tx-chips">
              {chipOrder(baseCols, resultCols, dropped, ops).map(({ col: c, gone }) =>
                gone ? (
                  <span key={`gone-${c.name}`} className="tx-chip gone" title="dropped">{c.name}</span>
                ) : (
                  <span key={c.name} className={`tx-chip${changed.has(c.name) ? " chg" : ""}${c.role === "target" ? " tgt" : ""}`}>
                    {c.name}
                    {c.role === "target" ? <b>target</b> : null}
                    {changed.has(c.name) && c.role !== "target" ? <small>{renamed.has(c.name) && !baseCols.some((b) => b.name === c.name) ? "renamed" : `${c.kind}${c.encoding ? ` · ${c.encoding}` : ""}`}</small> : null}
                  </span>
                ),
              )}
            </div>
          </section>
          <section className={`tx-prev${preview.isFetching ? " stale" : ""}`}>
            <header>
              Preview
              <span className="aside" aria-live="polite">
                first <b>{count(result?.rows.length ?? 0)}</b> rows of <b>{count(v.row_count)}</b>
                {Object.entries(byStatus).map(([s, n]) => <span key={s}> · <b>{count(n)}</b> {s}</span>)}
              </span>
              <span className="spacer" />
              <span className="fresh">
                {preview.isFetching ? "updating…" : problem ? `waiting for ${String(problemCount)} ${problemCount === 1 ? "fix" : "fixes"}` : preview.dataUpdatedAt ? <><Icon name="check" />updated {since(preview.dataUpdatedAt, now)}</> : null}
              </span>
            </header>
            {problem ? (
              <div className="tx-pad"><div className="callout info"><Icon name="info" /><span>Fix the {problemCount === 1 ? "problem" : `${String(problemCount)} problems`} to update the preview. The rows below are from the last list that passed.</span></div></div>
            ) : null}
            {result && result.rows.length && (byStatus["rejected"] ?? 0) === result.rows.length ? (
              <div className="tx-pad"><div className="callout warn"><Icon name="alert" /><span><b>Every sampled row would be rejected.</b> Look at the operation with cast_failed rows: a value there does not convert to its new kind.</span></div></div>
            ) : null}
            {previewDown ? (
              <ErrorState error={preview.error} title="The preview could not be computed" onRetry={() => void preview.refetch()} />
            ) : result ? (
              <PreviewTable preview={result} changed={changed} stale={preview.isFetching || Boolean(problem)} />
            ) : (
              <div className="tx-pad">{[0, 1, 2, 3, 4].map((i) => <span key={i} className="skeleton" style={{ height: 20, display: "block", marginBottom: 6 }} />)}</div>
            )}
            <div className="tx-foot">Computed by the stage&apos;s own code: these are the first rows the run will write (DATA-7.5.AC1). Scroll sideways for every column; the row key stays.</div>
          </section>
        </div>
      </div>
    </div>
  );
}
