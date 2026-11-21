// SCR-09 v2's fine-tuning parts (RUN-1.10; mockup SCR-09v2_v1, frames E and F; ADR-0014
// Addendum A): the Start from card (fresh weights or a trained version, the compatibility checks
// with both values, Reset the head, where the run registers) and the Freeze layers card. The
// checks come from POST /jobs/validate, which creates nothing.
import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { useDebounced } from "../../api/builder";
import { api, call, type Schemas } from "../../api/client";
import { registryKeys, useLineages, useRetrain } from "../../api/registry";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { bytes, count } from "../../lib/format";
import type { Graph } from "../../lib/graph";

export type TrainValidation = Schemas["TrainValidation"];
type Check = Schemas["FineTuneCheck"];
type Detail = Schemas["ModelVersionDetail"];
type Report = Schemas["SpecReport"];

/** POST /jobs/validate for a network's run; ``null`` body: nothing to check. */
export function useTrainValidate(body: object | null) {
  const key = useDebounced(body ? JSON.stringify(body) : null, 250);
  return useQuery({
    queryKey: ["jobs", "validate", key],
    queryFn: () => call(() => api.POST("/api/v1/jobs/validate", { body: JSON.parse(key ?? "{}") as never })),
    enabled: key !== null,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, sorted((value as Record<string, unknown>)[k])]));
  }
  return value;
}

/** A model spec as it keys a lineage: a graph without its label (ADR-0017 rule 1). */
export function identityOf(model: unknown): string {
  const m = (model ?? {}) as { name?: string; graph?: Record<string, unknown> };
  if (m.name !== "graph" || !m.graph) return JSON.stringify(sorted(model));
  const rest: Record<string, unknown> = { ...m.graph };
  delete rest.label;
  return JSON.stringify(sorted({ name: "graph", graph: rest }));
}

export interface Candidate {
  id: string;
  lineageId: string;
  lineageName: string;
  datasetVersion: string | null;
  metric: number | null;
  present: boolean;
  trainedAt: string | null;
  jobId: string | null;
}

/** Trained versions of this model, from any lineage, newest first (the picker, frame F). */
export function useCandidates(model: unknown, enabled: boolean): { items: Candidate[]; loading: boolean } {
  const lineages = useLineages(null, "");
  const identity = identityOf(model);
  const same = enabled ? (lineages.data?.items ?? []).filter((l) => identityOf(l.model) === identity) : [];
  const details = useQueries({
    queries: same.map((l) => ({
      queryKey: registryKeys.lineage(l.id),
      queryFn: () => call(() => api.GET("/api/v1/models/{lineage_id}", { params: { path: { lineage_id: l.id } } })),
    })),
  });
  const items = details
    .flatMap((d) => (d.data ? d.data.versions.filter((v) => v.state === "trained").map((v): Candidate => ({
      id: v.id,
      lineageId: d.data.id,
      lineageName: d.data.name,
      datasetVersion: v.dataset_version_id,
      metric: v.main_metric,
      present: v.weights_status === "present",
      trainedAt: v.trained_at,
      jobId: v.job_id,
    })) : []))
    .sort((a, b) => (b.trainedAt ?? "").localeCompare(a.trainedAt ?? ""));
  return { items, loading: lineages.isLoading || details.some((d) => d.isLoading) };
}

function candidateText(c: Candidate): string {
  const metric = c.metric === null ? "" : ` · val acc ${c.metric.toFixed(3)}`;
  return `${c.id} · ${c.lineageName}${metric}${c.jobId ? ` · ${c.jobId}` : ""}${c.present ? "" : " · weights deleted"}`;
}

/** One check line's right-hand text (frame E's wording). */
function lineText(c: Check, headText: string, reset: boolean): string {
  if (!c.ok) return `${c.parent} in the parent, ${c.here} here`;
  if (c.rule === "Output" && reset) return `reset: ${headText}, new weights`;
  if (c.rule === "Output") return `${c.here}, the same list in the same order`;
  if (c.rule === "Input") return `${c.parent} = ${c.here}`;
  return `${c.here}, as the parent's`;
}

export function StartFromCard({
  fineTune, onMode, parent, candidates, candidatesLoading, onPick, resetHead, onReset, validation, validating,
  headText, isStudy, classical, serverError, version,
}: {
  fineTune: boolean;
  onMode: (fineTune: boolean) => void;
  parent: Detail | null;
  candidates: Candidate[];
  candidatesLoading: boolean;
  onPick: (id: string) => void;
  resetHead: boolean;
  onReset: (on: boolean) => void;
  validation: TrainValidation | null;
  validating: boolean;
  headText: string;
  isStudy: boolean;
  classical: boolean;
  serverError: string | undefined;
  version: string | null;
}) {
  const navigate = useNavigate();
  const retrain = useRetrain();
  if (classical) {
    return (
      <section className="card">
        <header><h2>Start from</h2><span className="req">RUN-1.10</span></header>
        <div className="b"><div className="callout info"><Icon name="info" /><span><b>Classical models always start fresh.</b> Starting from a trained version is for networks (RUN-1.10).</span></div></div>
      </section>
    );
  }
  const checks = validation?.checks ?? [];
  const shown = checks.filter((c) => c.rule === "Dataset type" || c.rule === "Input" || c.rule === "Output" || !c.ok);
  const output = checks.find((c) => c.rule === "Output");
  const input = checks.find((c) => c.rule === "Input");
  const outputMismatch = output !== undefined && output.parent !== output.here;
  const deleted = parent !== null && parent.weights_status !== "present";
  const lineage = validation?.lineage ?? null;
  return (
    <section className="card">
      <header>
        <h2>Start from</h2><span className="req">RUN-1.10 · ADR-0015</span><span className="spacer" />
        <span className="group-btns" role="group" aria-label="Start from">
          <Button size="sm" aria-pressed={!fineTune} onClick={() => { onMode(false); }}>Fresh weights</Button>
          <Button size="sm" aria-pressed={fineTune} onClick={() => { onMode(true); }}>A trained version</Button>
        </span>
      </header>
      <div className="b">
        {!fineTune ? (
          <span>{isStudy ? "Each trial starts from its lineage's base if one exists, else from its own initial weights; only the best trial founds or joins a lineage (ADR-0018 rule 7)." : "The run starts from its lineage's base: an existing lineage's, or a new one saved before step 1."}</span>
        ) : (
          <>
            <div className="ft-row">
              <label className="sr-only" htmlFor="init-from">Trained version to start from</label>
              {candidatesLoading && candidates.length === 0 ? <Skeleton width={360} /> : (
                <select id="init-from" className="select mono ft-pick" value={parent?.id ?? ""} onChange={(e) => { onPick(e.target.value); }}>
                  {parent === null ? <option value="">Choose a version of this model…</option> : null}
                  {parent !== null && !candidates.some((c) => c.id === parent.id) ? <option value={parent.id}>{parent.id} · {parent.lineage_name}</option> : null}
                  {candidates.map((c) => <option key={c.id} value={c.id}>{candidateText(c)}</option>)}
                </select>
              )}
              {parent ? <span>{deleted ? "weights deleted" : `weights present · ${bytes(parent.weights_bytes ?? 0)}`}</span> : null}
            </div>
            {candidates.length === 0 && !candidatesLoading && parent === null ? <span style={{ color: "var(--text-muted)" }}>No trained version of this model yet: train it from fresh weights first.</span> : null}
            {deleted ? (
              <>
                <div className="callout err" role="alert"><Icon name="alert" /><span><b>Its weights were deleted by retention</b>{parent.weights_deleted_by_job_id ? <> (<span className="mono">{parent.weights_deleted_by_job_id}</span>)</> : null}. Its config and metrics are kept, so it can be trained again and the result fine-tuned.</span></div>
                <div><Button size="sm" disabled={retrain.isPending} onClick={() => { retrain.mutate(parent.id, { onSuccess: (job) => void navigate(`/jobs/${job.id}`) }); }}><Icon name="refresh" />Re-train it</Button></div>
              </>
            ) : null}
            {parent && !deleted ? (
              validation === null ? <Skeleton width={320} height={48} /> : (
                <>
                  <div className="ft-lines" aria-busy={validating}>
                    {shown.map((c) => (
                      <span key={c.rule} style={{ display: "contents" }}>
                        <span className={c.ok ? "ok" : "no"} aria-label={c.ok ? "fits" : "does not fit"}><Icon name={c.ok ? "check" : "x"} /></span>
                        <span className={c.ok ? undefined : "bad"}>{c.rule === "Input" ? (c.parent.startsWith("vocabulary") ? "Vocabulary" : c.parent.includes("numeric") ? "Columns" : "Input shape") : c.rule}</span>
                        <span className={c.ok ? (c.rule === "Input" ? "mono" : undefined) : "bad"}>{lineText(c, headText, resetHead)}</span>
                      </span>
                    ))}
                  </div>
                  {output && !output.ok ? (
                    <div className="callout err" role="alert"><Icon name="alert" /><span><b>The head does not fit this split.</b> Turn on <i>Reset the head</i> to re-initialise the output node; every other layer keeps the parent's weights.</span></div>
                  ) : null}
                  {input && !input.ok ? (
                    <>
                      <div className="callout err" role="alert"><Icon name="alert" /><span><b>{input.parent.startsWith("vocabulary") ? "This split's tokens come from another vocabulary." : "This version cannot take another input."}</b> {input.parent.startsWith("vocabulary") ? "Fine-tune a text model only on a split tokenized with the parent's vocabulary." : "Resetting the head changes only the output. Build a model for this split, or choose one that matches."}</span></div>
                      <div><Button size="sm" onClick={() => { void navigate(`/models/build${version ? `?version=${version}` : ""}`); }}><Icon name="branch" />Open the builder</Button></div>
                    </>
                  ) : null}
                  <div className="ft-row">
                    <label className="toggle"><input type="checkbox" checked={resetHead} onChange={(e) => { onReset(e.target.checked); }} /><i />Reset the head</label>
                    <span>Re-initialises only the output node ({headText}).{!outputMismatch && !resetHead ? " Not needed here." : ""}</span>
                  </div>
                  {lineage ? (
                    <div className="callout info"><Icon name="info" /><span>
                      {lineage.joins
                        ? <>Registers as a new version of <b>{lineage.name}</b>, with <span className="mono">{parent.id}</span> as its parent. While it keeps its weights, retention will not delete the parent (REG-2.8).</>
                        : <>Founds a new lineage, <b>{lineage.name}</b>, whose first version has <span className="mono">{parent.id}</span> (in {parent.lineage_name}) as its parent.</>}
                      {isStudy ? " Every trial starts from it; it is not tunable." : ""}
                    </span></div>
                  ) : null}
                </>
              )
            ) : null}
            {serverError ? <div className="callout err" role="alert"><Icon name="alert" /><span>{serverError}</span></div> : null}
          </>
        )}
      </div>
    </section>
  );
}

/** A layer's main settings in a few words, e.g. "conv2d 32 filters, 3×3". */
function describe(layer: Record<string, unknown> | undefined, type: string): string {
  if (!layer) return type;
  const k = layer.kernel_size;
  if (type === "conv2d" || type === "conv1d") return `${type} ${String(layer.out_channels)} filters, ${String(k)}${type === "conv2d" ? `×${String(k)}` : ""}`;
  if (type === "linear") return `linear → ${String(layer.out_features)}`;
  if (type === "embedding") return `embedding ${String(layer.embedding_dim)}-d`;
  if (type === "lstm" || type === "gru" || type === "rnn") return `${type} hidden ${String(layer.hidden_size)}`;
  return type;
}

export function FreezeCard({ report, graph, freeze, onChange, resetHead, problems, reference }: {
  report: Report | null;
  graph: Graph | null;
  freeze: string[];
  onChange: (next: string[]) => void;
  resetHead: boolean;
  problems: Schemas["FreezeProblem"][];
  reference: string | null;
}) {
  if (reference) {
    return (
      <section className="card">
        <header><h2>Freeze layers</h2><span className="req">RUN-1.10.AC2</span></header>
        <div className="b"><span>{reference} is a reference model: it has no node IDs, so it can be fine-tuned and its head reset, but not frozen.</span></div>
      </section>
    );
  }
  if (!report || !graph) return null;
  const layers = new Map(graph.nodes.map((n) => [n.id, n.layer as Record<string, unknown>]));
  const rows = report.nodes.filter((n) => (n.parameters ?? 0) > 0);
  const head = report.head;
  const chosen = new Set(freeze);
  const frozenCount = rows.filter((n) => chosen.has(n.id)).reduce((s, n) => s + (n.parameters ?? 0), 0) + (head && chosen.has("head") ? head.parameters : 0);
  const total = report.total_parameters ?? 0;
  const frozenLayers = rows.filter((n) => chosen.has(n.id)).length + (chosen.has("head") ? 1 : 0);
  function set(id: string, on: boolean) {
    const next = new Set(freeze);
    if (on) next.add(id);
    else next.delete(id);
    onChange([...next]);
  }
  return (
    <section className="card">
      <header><h2>Freeze layers</h2><span className="req">RUN-1.10.AC2</span><span className="spacer" /><Button size="sm" variant="ghost" disabled={freeze.length === 0} onClick={() => { onChange([]); }}>Clear</Button></header>
      <div className="b">
        <span>Frozen layers keep their weights and, for BatchNorm, their running statistics.</span>
        <div className="table-wrap">
          <table className="t dense ft-table">
            <caption className="sr-only">Layers with weights, to freeze</caption>
            <thead><tr><th className="ft-chkc"><span className="sr-only">Frozen</span></th><th>Node</th><th>Layer</th><th>Output</th><th className="r">Parameters</th><th><span className="sr-only">Shortcut</span></th></tr></thead>
            <tbody>
              {rows.map((n, i) => (
                <tr key={n.id} className={chosen.has(n.id) ? "ft-frozen" : undefined}>
                  <td className="ft-chkc"><input type="checkbox" aria-label={`Freeze ${n.id}`} checked={chosen.has(n.id)} onChange={(e) => { set(n.id, e.target.checked); }} /></td>
                  <td className="id">{n.id}</td>
                  <td>{describe(layers.get(n.id), n.type)}</td>
                  <td className="id">{n.shape_text ?? "–"}</td>
                  <td className="r">{count(n.parameters)}</td>
                  <td className="r"><Button size="sm" variant="ghost" className="ft-upto" aria-label={`Freeze up to ${n.id}`} onClick={() => { onChange([...new Set([...freeze.filter((f) => f === "head"), ...rows.slice(0, i + 1).map((r) => r.id)])]); }}>Freeze up to here</Button></td>
                </tr>
              ))}
              {head ? (
                <tr className={chosen.has("head") ? "ft-frozen" : undefined}>
                  <td className="ft-chkc"><input type="checkbox" aria-label="Freeze the head" disabled={resetHead} checked={chosen.has("head")} onChange={(e) => { set("head", e.target.checked); }} /></td>
                  <td className="id">output</td>
                  <td>linear {String(head.in_features)} → {String(head.out_features)} <Badge>head</Badge>{resetHead ? <> <Badge>reset</Badge></> : null}</td>
                  <td className="id">{String(head.out_features)}</td>
                  <td className="r">{count(head.parameters)}</td>
                  <td className="r">{resetHead ? <span className="note">a reset head must train</span> : null}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {problems.map((p) => <div key={JSON.stringify(p.loc)} className="callout err" role="alert"><Icon name="alert" /><span>{p.msg}</span></div>)}
        <div className="ft-sum"><b>{count(total - frozenCount)}</b> of {count(total)} parameters will train · <b>{count(frozenCount)}</b> frozen in {String(frozenLayers)} layers. Layers without weights (activations, pooling) are not listed.</div>
      </div>
    </section>
  );
}
