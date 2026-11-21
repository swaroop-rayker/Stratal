// SCR-11 v2's Try it for a table record and a text (REG-1.4; mockup SCR-11v2_v1, frames C and D):
// a field per feature or a text box, Fill from a test row with that row's true label, the answer
// by task, the served line, and a refused record's errors on its fields.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError, api, call, type Schemas } from "../../api/client";
import { usePredict } from "../../api/registry";
import { Button, Icon } from "../../components/ui";
import { servedText } from "./TryIt";

type Prediction = Schemas["Prediction"];
interface TestRow { record?: string | null; text?: string | null; label?: string | null; target_value?: number | null; row_key: string }

function useTestRows(datasetVersion: string | null) {
  return useQuery({
    queryKey: ["versions", datasetVersion, "rows", "try-it"],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/versions/{version_id}/rows", {
          params: { path: { version_id: datasetVersion ?? "" }, query: { status: "accepted", split: "test", limit: 100 } },
        }),
      ),
    enabled: datasetVersion !== null,
    staleTime: Infinity,
  });
}

function recordOf(row: TestRow): Record<string, unknown> {
  try {
    return typeof row.record === "string" ? (JSON.parse(row.record) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function truthOf(row: TestRow): string | null {
  if (row.label !== null && row.label !== undefined) return row.label;
  return row.target_value === null || row.target_value === undefined ? null : String(row.target_value);
}

/** The answer by task (decision 7): classes as bars, a value, a cluster or a projection. */
export function Answer({ result, truth, target }: { result: Prediction; truth: string | null; target: string | null }) {
  const top = result.top ?? [];
  const guess = result.task === "classification" ? top[0]?.class ?? null : result.value !== null && result.value !== undefined ? String(result.value) : null;
  const matches = truth !== null && guess !== null && (result.task === "classification" ? guess === truth : Math.abs(Number(guess) - Number(truth)) < 1e-9);
  return (
    <>
      <div className="try-answer" role="status" aria-label="Prediction">
        {result.task === "classification" ? top.map((t, i) => (
          <span key={t.class} style={{ display: "contents" }}>
            {i === 0 ? <b>{t.class}</b> : <span>{t.class}</span>}
            <span className="try-bar" aria-hidden="true"><i style={{ ["--p" as string]: `${String(100 * (t.probability ?? 0))}%` }} /></span>
            <span className="try-p">{t.probability === null ? "–" : t.probability.toFixed(3)}</span>
          </span>
        )) : result.task === "regression" ? (
          <><b>{target ?? "value"}</b><span /><span className="try-p">{(result.value ?? 0).toFixed(1)}</span></>
        ) : result.task === "clustering" ? (
          <><b>cluster</b><span /><span className="try-p">{String(result.cluster)}</span></>
        ) : (
          <><b>components</b><span /><span className="try-p">{(result.components ?? []).map((c) => c.toFixed(2)).join(", ")}</span></>
        )}
      </div>
      {truth !== null && (result.task === "classification" || result.task === "regression") ? (
        <div className="callout info"><Icon name={matches ? "check" : "x"} /><span>This row's {result.task === "regression" ? "value" : "label"}: <b>{truth}</b> · {matches ? "matches" : "differs"}</span></div>
      ) : null}
      <span className="try-served">{servedText(result)}</span>
    </>
  );
}

function Refusal({ error, fieldErrors }: { error: unknown; fieldErrors: number }) {
  const problem = error instanceof ApiError ? error.problem : null;
  if (!problem) return null;
  return (
    <div className="callout err" role="alert"><Icon name="alert" />
      <span>{fieldErrors ? <><b>The record does not fit this model's table.</b> <span className="mono">{problem.code}</span> · {String(fieldErrors)} {fieldErrors === 1 ? "field" : "fields"}. Fix {fieldErrors === 1 ? "it" : "them"} and predict again.</> : <><span className="mono">{problem.code}</span> · {problem.detail}{problem.suggested_action ? ` ${problem.suggested_action}` : ""}</>}</span>
    </div>
  );
}

export function RecordTry({ versionId, datasetVersion }: { versionId: string; datasetVersion: string | null }) {
  const rows = useTestRows(datasetVersion);
  const predict = usePredict(versionId);
  const [values, setValues] = useState<Record<string, string>>({});
  const [from, setFrom] = useState<TestRow | null>(null);
  const items = (rows.data?.items ?? []) as unknown as TestRow[];
  const records = items.map(recordOf);
  const columns = Object.keys(records[0] ?? {});
  const numeric = new Set(columns.filter((c) => records.every((r) => r[c] === null || typeof r[c] === "number")));
  const categories = Object.fromEntries(columns.filter((c) => !numeric.has(c)).map((c) => [c, [...new Set(records.map((r) => r[c]).flatMap((v) => (typeof v === "string" || typeof v === "number" ? [String(v)] : [])))].sort()]));
  const problem = predict.error instanceof ApiError ? predict.error.problem : null;
  const errors = new Map((problem?.errors ?? []).filter((e) => e.loc[1] === "record" && typeof e.loc[2] === "string").map((e) => [String(e.loc[2]), e.msg]));

  function fill() {
    const pick = items[Math.floor(Math.random() * items.length)];
    if (!pick) return;
    setFrom(pick);
    setValues(Object.fromEntries(Object.entries(recordOf(pick)).map(([k, v]) => [k, typeof v === "string" || typeof v === "number" ? String(v) : ""])));
    predict.reset();
  }
  function send() {
    const record = Object.fromEntries(columns.map((c) => {
      const raw = (values[c] ?? "").trim();
      if (raw === "") return [c, null];
      return [c, numeric.has(c) && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : raw];
    }));
    predict.mutate({ record });
  }
  return (
    <section className="card" aria-label="Try it">
      <h2>Try it<span className="spacer" /><Button size="sm" disabled={items.length === 0} onClick={fill}><Icon name="refresh" />Fill from a test row</Button></h2>
      <p className="note2">One record: {String(columns.length)} features from the table; the target is left out.</p>
      <div className="try-grid">
        {columns.map((c) => (
          <div key={c} className={errors.has(c) ? "try-field error" : "try-field"}>
            <label htmlFor={`try-${c}`} title={c}>{c}</label>
            <input id={`try-${c}`} className="mono" list={numeric.has(c) ? undefined : `try-${c}-values`} inputMode={numeric.has(c) ? "decimal" : undefined} placeholder="empty: imputed"
              aria-invalid={errors.has(c)} aria-describedby={errors.has(c) ? `try-${c}-error` : undefined}
              value={values[c] ?? ""} onChange={(e) => { setValues({ ...values, [c]: e.target.value }); setFrom(null); }} />
            {numeric.has(c) ? null : <datalist id={`try-${c}-values`}>{(categories[c] ?? []).map((v) => <option key={v} value={v} />)}</datalist>}
            {errors.has(c) ? <small id={`try-${c}-error`}><Icon name="alert" />{errors.get(c)}</small> : null}
          </div>
        ))}
      </div>
      <div className="try-row">
        <span className="note2">{from ? <>from <span className="mono">{from.row_key}</span></> : "An empty field is imputed as in training."}</span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => { setValues({}); setFrom(null); predict.reset(); }}>Clear</Button>
        <Button size="sm" variant="primary" disabled={predict.isPending || columns.length === 0} onClick={send}>Predict</Button>
      </div>
      <div aria-live="polite">{predict.data ? <Answer result={predict.data} truth={from ? truthOf(from) : null} target={null} /> : null}</div>
      <Refusal error={predict.error} fieldErrors={errors.size} />
    </section>
  );
}

export function TextTry({ versionId, datasetVersion }: { versionId: string; datasetVersion: string | null }) {
  const rows = useTestRows(datasetVersion);
  const predict = usePredict(versionId);
  const [value, setValue] = useState("");
  const [from, setFrom] = useState<TestRow | null>(null);
  const items = ((rows.data?.items ?? []) as unknown as TestRow[]).filter((r) => r.text);
  const facts = predict.data?.input;
  return (
    <section className="card" aria-label="Try it">
      <h2>Try it<span className="spacer" /><Button size="sm" disabled={items.length === 0} onClick={() => {
        const pick = items[Math.floor(Math.random() * items.length)];
        if (pick) { setFrom(pick); setValue(pick.text ?? ""); predict.reset(); }
      }}><Icon name="refresh" />Fill from a test row</Button></h2>
      <p className="note2">One text, tokenized like the train split with its vocabulary.</p>
      <label className="sr-only" htmlFor="try-text">Text</label>
      <textarea id="try-text" className="try-text" value={value} onChange={(e) => { setValue(e.target.value); setFrom(null); }} />
      <div className="try-row">
        <span className="try-served">{facts ? `${String(facts.tokens)} tokens · ${String(facts.unknown)} unknown · ${facts.truncated_at === null ? "not cut" : `cut to ${String(facts.truncated_at)}`}` : ""}</span>
        <span className="spacer" />
        <Button size="sm" variant="primary" disabled={predict.isPending || value.trim() === ""} onClick={() => { predict.mutate({ text: value }); }}>Predict</Button>
      </div>
      <div aria-live="polite">{predict.data ? <Answer result={predict.data} truth={from ? truthOf(from) : null} target={null} /> : null}</div>
      <Refusal error={predict.error} fieldErrors={0} />
    </section>
  );
}
