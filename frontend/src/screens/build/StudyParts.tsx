// SCR-09's study part (M10, mockup SCR-09hpo_v1): the Tune toggle beside a field's label,
// the range or choices that replace a tuned field's input, the study card (search space,
// limits, objective, pruning, seed) and the list of layer fields a study can tune.
import type { RJSFSchema } from "@rjsf/utils";
import { type ReactNode, useState } from "react";

import { fieldName } from "../../api/studies";
import { Button, Icon } from "../../components/ui";
import { describe, ownRange, type Scale, specProblem, type TuneSpec, typeWord } from "./study";

export function TuneButton({ name, on, onToggle }: { name: string; on: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="tune" aria-pressed={on} aria-label={`Tune ${name}`} onClick={onToggle}>
      <Icon name="sliders" />Tune
    </button>
  );
}

function num(raw: string): number | undefined {
  if (raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isNaN(n) ? undefined : n;
}

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** A tuned field's input: low, high and a scale; or choice chips with an add box. */
export function TuneEditor({ name, schema, spec, onChange }: { name: string; schema: RJSFSchema; spec: TuneSpec; onChange: (spec: TuneSpec) => void }) {
  const [adding, setAdding] = useState("");
  const enumChoices = schema.enum ?? (schema.type === "boolean" ? [true, false] : null);
  const integer = schema.type === "integer" || (schema.anyOf as RJSFSchema[] | undefined)?.some((o) => o.type === "integer");
  if (spec.scale === "choices") {
    const chosen = spec.choices ?? [];
    const left = enumChoices ? enumChoices.filter((c) => !chosen.some((v) => show(v) === show(c))) : [];
    const add = (value: unknown) => { onChange({ ...spec, choices: [...chosen, value] }); };
    return (
      <div className="chips" role="group" aria-label={`${name}, choices`}>
        {chosen.map((c) => (
          <span key={show(c)} className="chip">{show(c)}
            <button type="button" className="chip-x" aria-label={`Remove ${show(c)}`} onClick={() => { onChange({ ...spec, choices: chosen.filter((v) => show(v) !== show(c)) }); }}><Icon name="x" /></button>
          </span>
        ))}
        {enumChoices ? (
          left.length ? (
            <select className="chip add" aria-label={`${name}, add a choice`} value="" onChange={(e) => { const v = left.find((c) => show(c) === e.target.value); if (v !== undefined) add(v); }}>
              <option value="">add</option>
              {left.map((c) => <option key={show(c)} value={show(c)}>{show(c)}</option>)}
            </select>
          ) : null
        ) : (
          <input className="chip add" aria-label={`${name}, add a choice`} placeholder="add" value={adding} inputMode="decimal"
            onChange={(e) => { setAdding(e.target.value); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); const v = num(adding); if (v !== undefined) { add(v); setAdding(""); } } }}
            onBlur={() => { const v = num(adding); if (v !== undefined) { add(v); setAdding(""); } }} />
        )}
        {!enumChoices ? <button type="button" className="chip-mode" onClick={() => { const nums = chosen.filter((c): c is number => typeof c === "number"); onChange({ scale: "linear", low: nums.length ? Math.min(...nums) : undefined, high: nums.length ? Math.max(...nums) : undefined }); }}>range</button> : null}
      </div>
    );
  }
  return (
    <div className="range">
      <input className="input mono" aria-label={`${name}, low`} inputMode="decimal" defaultValue={spec.low ?? ""} onChange={(e) => { onChange({ ...spec, low: num(e.target.value) }); }} />
      <span className="sep">to</span>
      <input className="input mono" aria-label={`${name}, high`} inputMode="decimal" defaultValue={spec.high ?? ""} onChange={(e) => { onChange({ ...spec, high: num(e.target.value) }); }} />
      {integer ? (
        <input className="input mono step" aria-label={`${name}, step`} placeholder="step" inputMode="numeric" defaultValue={spec.step ?? ""} onChange={(e) => { onChange({ ...spec, step: num(e.target.value) }); }} />
      ) : null}
      <select className="select" aria-label={`${name}, scale`} value={spec.scale} onChange={(e) => {
        const scale = e.target.value as Scale;
        if (scale === "choices") onChange({ scale, choices: [spec.low, spec.high].filter((v) => v !== undefined) });
        else onChange({ ...spec, scale });
      }}>
        <option value="linear">linear</option>
        {integer ? null : <option value="log">log</option>}
        <option value="choices">choices</option>
      </select>
    </div>
  );
}

/** The helper under a tuned field: its own range, or what is wrong. */
export function tuneHelper(schema: RJSFSchema, spec: TuneSpec, server: string | undefined): ReactNode {
  const problem = server ?? specProblem(schema, spec);
  if (problem) return <span className="bad"><Icon name="alert" style={{ width: 12, height: 12 }} />{problem}</span>;
  const own = ownRange(schema);
  if (spec.scale === "choices") return own.low !== null ? `Choices. Each must be ${String(own.low)}–${String(own.high ?? "")}.` : "Choices.";
  const allows = own.low !== null ? `The field allows ${own.above ? `above ${String(own.low)}` : String(own.low)}${own.high !== null ? `${own.above ? ", at most" : "–"}${String(own.high)}` : ""}` : "";
  return `${allows}${spec.scale === "log" && own.low === 0 ? "; a log scale needs above 0" : ""}.`;
}

export interface SpaceRow {
  path: string;
  schema: RJSFSchema;
  spec: TuneSpec;
  /** Layer fields have no form field: their range is edited in the row. */
  inline: boolean;
}

export interface StudyLimits {
  n_trials: number;
  minutes: number;
  objective: string;
  pruning: boolean;
  seed: number;
  cv_folds: number;
}

function LimitField({ label, type, help, children }: { label: string; type: string; help: string; children: ReactNode }) {
  return (
    <div className="field">
      <label><span className="mono">{label}</span><span className="type">{type}</span></label>
      {children}
      <small>{help}</small>
    </div>
  );
}

export function StudyCard({ classical, rows, errors, limits, objectives, onLimits, onRow, onRemove, onAdd, onOff, addable, fixedStart }: {
  classical: boolean;
  rows: SpaceRow[];
  errors: Map<string, string>;
  limits: StudyLimits;
  objectives: Record<string, "maximize" | "minimize">;
  onLimits: (next: StudyLimits) => void;
  onRow: (path: string, spec: TuneSpec) => void;
  onRemove: (path: string) => void;
  onAdd: (() => void) | null;
  onOff: () => void;
  addable: boolean;
  /** A fine-tune's start, the same for every trial and not tunable (RUN-1.10, frame G). */
  fixedStart?: string | null;
}) {
  return (
    <section className="card study" aria-label="Tune with a study">
      <header><h2>Tune with a study</h2><span className="req">HPO-1.1 – HPO-1.4</span><span className="spacer" />
        <span className="pill completed"><Icon name="check" />on</span>
        <Button size="sm" variant="ghost" onClick={onOff}>Turn off</Button>
      </header>
      <div className="b">
        <div className="sp-head">Search space · {String(rows.length)} {rows.length === 1 ? "field" : "fields"}</div>
        {rows.length ? (
          <table className="t dense space">
            <thead><tr><th>Field</th><th>Type</th><th>Range or choices</th><th aria-label="Remove" /></tr></thead>
            <tbody>
              {rows.map((r) => {
                const problem = errors.get(r.path) ?? specProblem(r.schema, r.spec);
                return (
                  <tr key={r.path} className={problem ? "bad" : undefined}>
                    <td className="mono">{fieldName(r.path)}</td>
                    <td>{typeWord(r.schema, r.spec)}</td>
                    <td>
                      {r.inline ? <TuneEditor name={fieldName(r.path)} schema={r.schema} spec={r.spec} onChange={(s) => { onRow(r.path, s); }} /> : <span className="mono">{describe(r.spec)}</span>}
                      {problem ? <span className="err-line"><Icon name="alert" />{problem}</span> : null}
                    </td>
                    <td className="r"><Button size="sm" variant="ghost" aria-label={`Stop tuning ${fieldName(r.path)}`} onClick={() => { onRemove(r.path); }}><Icon name="x" /></Button></td>
                  </tr>
                );
              })}
              {fixedStart ? (
                <tr className="fixed">
                  <td className="mono">init_from · freeze</td>
                  <td><span className="badge">fixed</span></td>
                  <td><span className="mono">{fixedStart}</span> <span className="note">Every trial starts from it; not tunable.</span></td>
                  <td />
                </tr>
              ) : null}
            </tbody>
          </table>
        ) : (
          <div className="placeholder"><Icon name="sliders" /><span>Turn on <b>Tune</b> beside a field to add it here.</span></div>
        )}
        {onAdd && addable ? <span><Button size="sm" onClick={onAdd}><Icon name="plus" />Add a model parameter</Button></span> : null}
        <div className="sform">
          <LimitField label="n_trials" type="int" help="1–200, default 20.">
            <input className="input mono" aria-label="n_trials" inputMode="numeric" value={String(limits.n_trials)} onChange={(e) => { onLimits({ ...limits, n_trials: Number(e.target.value) || 0 }); }} />
          </LimitField>
          <LimitField label="time_budget" type="min" help="Minutes of running time; pauses don't count. 1–1440.">
            <input className="input mono" aria-label="time_budget in minutes" inputMode="numeric" value={String(limits.minutes)} onChange={(e) => { onLimits({ ...limits, minutes: Number(e.target.value) || 0 }); }} />
          </LimitField>
          <LimitField label="objective" type="enum" help={`${Object.entries(objectives).map(([k, d]) => `${k} (${d === "maximize" ? "maximise" : "minimise"})`).join(", ")}. Whichever limit comes first ends the study.`}>
            <select className="select mono" aria-label="objective" value={limits.objective} onChange={(e) => { onLimits({ ...limits, objective: e.target.value }); }}>
              {Object.entries(objectives).map(([k, d]) => <option key={k} value={k}>{k} · {d === "maximize" ? "maximise" : "minimise"}</option>)}
            </select>
          </LimitField>
          {classical ? (
            <LimitField label="cv_folds" type="int" help="2–10.">
              <input className="input mono" aria-label="cv_folds" inputMode="numeric" value={String(limits.cv_folds)} onChange={(e) => { onLimits({ ...limits, cv_folds: Number(e.target.value) || 0 }); }} />
            </LimitField>
          ) : null}
          <LimitField label="pruning" type="bool" help={classical ? "After 5 trials, from fold 1: a trial behind the median at the same fold stops." : "After 5 trials, from epoch 2: a trial behind the median at the same epoch stops."}>
            <label className="checks"><input type="checkbox" checked={limits.pruning} onChange={(e) => { onLimits({ ...limits, pruning: e.target.checked }); }} />Median pruning</label>
          </LimitField>
          <LimitField label="seed" type="int" help="TPE's suggestions.">
            <input className="input mono" aria-label="study seed" inputMode="numeric" value={String(limits.seed)} onChange={(e) => { onLimits({ ...limits, seed: Number(e.target.value) || 0 }); }} />
          </LimitField>
        </div>
        {classical ? <p className="note">Folds come from the train split, grouped so that duplicates never sit on both sides of a fold (ADR-0018 rule 4). The validation split scores the best configuration once it is refit; it is not used for tuning.</p> : null}
      </div>
    </section>
  );
}

export interface LayerField {
  node: string;
  layer: string;
  field: string;
  value: unknown;
  schema: RJSFSchema;
}

/** Frame D: the graph's numeric and choice fields a study can tune. */
export function AddParameter({ fields, onPick, onClose }: { fields: LayerField[]; onPick: (f: LayerField) => void; onClose: () => void }) {
  const [filter, setFilter] = useState("");
  const shown = fields.filter((f) => `${f.node} ${f.layer} ${f.field}`.includes(filter.trim()));
  return (
    <div className="popover" role="dialog" aria-label="Add a model parameter" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <header>Add a model parameter<span className="spacer" />
        <input className="input" placeholder="Filter…" aria-label="Filter fields" value={filter} autoFocus onChange={(e) => { setFilter(e.target.value); }} />
        <Button size="sm" variant="ghost" aria-label="Close" onClick={onClose}><Icon name="x" /></Button>
      </header>
      <table className="t dense">
        <thead><tr><th>Node · field</th><th className="r">Now</th><th className="r">Field range</th></tr></thead>
        <tbody>
          {shown.map((f) => {
            const own = ownRange(f.schema);
            return (
              <tr key={`${f.node}.${f.field}`} tabIndex={0} className="clickable" onClick={() => { onPick(f); }} onKeyDown={(e) => { if (e.key === "Enter") onPick(f); }}>
                <td className="mono">{f.node} · {f.layer} · {f.field}</td>
                <td className="p">{show(f.value)}</td>
                <td className="p">{f.schema.enum ? f.schema.enum.map(show).join(", ") : `${String(own.low ?? "")}–${String(own.high ?? "")}`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note">Only numeric and choice fields are listed; inferred sizes are left out. A chosen field joins the search space with its own range as the default. Tuning a width changes the parameter count from trial to trial, so the VRAM check takes the widest values.</p>
    </div>
  );
}
