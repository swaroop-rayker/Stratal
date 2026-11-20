// SCR-08's regions (mockup SCR-08_v1, frames A–E): the palette, a node's card, the list
// view, the inspector, the summary bar and the problems list.
import type { CSSProperties } from "react";

import type { DatasetEnds, DatasetType, NodeReport, Optimizer, SpecProblem, SpecReport } from "../../api/builder";
import { SchemaForm } from "../../components/SchemaForm";
import { Badge, Button, Icon } from "../../components/ui";
import { bytes, count } from "../../lib/format";
import {
  type Graph,
  type GraphNode,
  INPUT,
  inputsOf,
  isMerge,
  type LayerSpec,
  moveNode,
  OUTPUT,
} from "../../lib/graph";
import { type LayerType, summary, unavailable } from "./layers";

// ---------------------------------------------------------------------------- palette

export function Palette({ types, datasetType, onAdd }: {
  types: LayerType[];
  datasetType: DatasetType | null;
  onAdd: (type: LayerType) => void;
}) {
  const groups = [...new Set(types.map((t) => t.group))];
  return (
    <div className="palette">
      {groups.map((group) => {
        const members = types.filter((t) => t.group === group);
        const reasons = members.map((t) => unavailable(t.type, datasetType));
        const reason = reasons.find((r) => r !== null) ?? null;
        return (
          <div key={group} style={{ display: "contents" }}>
            <div className="pal-grp">{group}</div>
            {members.map((t, i) => {
              const off = reasons[i] !== null;
              return (
                <button
                  key={t.type}
                  type="button"
                  className="tile"
                  aria-disabled={off || undefined}
                  tabIndex={off ? -1 : undefined}
                  title={off ? (reasons[i] ?? undefined) : `Add ${t.title} after the selected layer`}
                  onClick={() => {
                    if (!off) onAdd(t);
                  }}
                >
                  {off ? null : <Icon name="plus" />}
                  {t.title}
                </button>
              );
            })}
            {reason !== null && reasons.every((r) => r !== null) ? (
              <div className="pal-note">
                <Icon name="info" />
                {reason}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------- node card

export interface NodeView {
  id: string;
  title: string;
  detail: string;
  shape: string;
  parameters: string;
  locked: boolean;
  problem: SpecProblem | null;
  muted: boolean;
}

export function nodeViews(
  graph: Graph,
  types: LayerType[],
  report: SpecReport | null,
  ends: DatasetEnds | null,
): NodeView[] {
  const reports = new Map<string, NodeReport>((report?.nodes ?? []).map((n) => [n.id, n]));
  const problems = new Map<string, SpecProblem>();
  for (const p of report?.problems ?? []) if (p.node_id && !problems.has(p.node_id)) problems.set(p.node_id, p);
  const title = (type: string) => types.find((t) => t.type === type)?.title ?? type;
  const input: NodeView = {
    id: INPUT, title: "locked", detail: "", shape: ends?.input_text ?? "—", parameters: "",
    locked: true, problem: null, muted: false,
  };
  const head = report?.head;
  const output: NodeView = {
    id: OUTPUT, title: "locked",
    detail: head ? `Linear ${String(head.in_features)} → ${String(head.out_features)}` : `→ ${String(ends?.output_size ?? "?")}`,
    shape: ends?.output_size ? `${String(ends.output_size)} classes` : "",
    parameters: head ? count(head.parameters) : "", locked: true, problem: null, muted: false,
  };
  const layers = graph.nodes.map((node): NodeView => {
    const r = reports.get(node.id);
    const problem = problems.get(node.id) ?? null;
    return {
      id: node.id,
      title: title(node.layer.type),
      detail: summary(node.layer),
      shape: problem ? "—" : (r?.shape_text ?? "?"),
      parameters: problem ? problem.code : r?.parameters === null || r?.parameters === undefined ? "–" : count(r.parameters),
      locked: false,
      problem,
      muted: !problem && r !== undefined && r.shape === null,
    };
  });
  return [input, ...layers, output];
}

/** A node's text name for screen readers (accessibility note 2). */
export function nodeLabel(view: NodeView): string {
  const base = `${view.id}, ${view.title}, output ${view.shape.replaceAll("×", " by ")}`;
  return view.problem ? `${base}, problem: ${view.problem.message}` : `${base}, ${view.parameters} parameters`;
}

export function NodeCard({ view, selected }: { view: NodeView; selected: boolean }) {
  const classes = ["gn"];
  if (view.locked) classes.push("lock");
  if (selected) classes.push("on");
  if (view.problem) classes.push("bad");
  if (view.muted) classes.push("muted");
  const showFoot = !(view.id === INPUT);
  return (
    <div className={classes.join(" ")} aria-label={nodeLabel(view)}>
      <div className="h">
        {view.locked ? <Icon name="db" /> : null}
        <span className="id">{view.id}</span>
        <span className="ty">{view.title}</span>
        {view.detail ? <span className="kv" style={view.locked ? { marginLeft: "auto" } : undefined}>{view.detail}</span> : null}
        {view.id === INPUT ? <span className="kv" style={{ marginLeft: "auto" }}>{view.shape}</span> : null}
        {view.problem ? (
          <span className="flag">
            <Icon name="alert" />
          </span>
        ) : null}
      </div>
      {showFoot ? (
        <div className="f">
          {view.shape}
          <span className="p">{view.parameters}</span>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- list view

export function ListView({ graph, views, selected, onSelect, onChange, onRemove }: {
  graph: Graph;
  views: NodeView[];
  selected: string | null;
  onSelect: (id: string) => void;
  onChange: (graph: Graph) => void;
  onRemove: (id: string) => void;
}) {
  const byId = new Map(views.map((v) => [v.id, v]));
  const ids = [INPUT, ...graph.nodes.map((n) => n.id), OUTPUT];
  const sources = (id: string) => [INPUT, ...graph.nodes.map((n) => n.id)].filter((s) => s !== id);
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>#</th><th>ID</th><th>Layer</th><th>Inputs</th><th>Output</th><th className="r">Parameters</th><th />
          </tr>
        </thead>
        <tbody>
          {ids.map((id, index) => {
            const view = byId.get(id);
            const locked = id === INPUT || id === OUTPUT;
            const ins = inputsOf(graph, id);
            return (
              <tr
                key={id}
                aria-selected={selected === id}
                tabIndex={locked ? undefined : 0}
                onClick={() => {
                  if (!locked) onSelect(id);
                }}
                onKeyDown={(event) => {
                  if (locked) return;
                  if (event.key === "Enter") onSelect(id);
                  if (event.key === "Delete") onRemove(id);
                }}
              >
                <td className="id">{locked ? "–" : String(index)}</td>
                <td className="id">{id}</td>
                <td style={locked ? { color: "var(--text-muted)" } : undefined}>
                  {locked ? "locked" : `${view?.title ?? ""} ${view?.detail ?? ""}`.trim()}
                  {view?.problem ? <> <Badge tone="err">{view.problem.code}</Badge></> : null}
                </td>
                <td>
                  {id === INPUT ? null : (
                    <select
                      className="select mono"
                      aria-label={`Inputs of ${id}`}
                      multiple={!locked && isMerge(graph, id)}
                      value={!locked && isMerge(graph, id) ? ins : (ins[0] ?? "")}
                      onChange={(event) => {
                        const chosen = [...event.target.selectedOptions].map((o) => o.value);
                        const edges = graph.edges.filter(([, to]) => to !== id);
                        onChange({ ...graph, edges: [...edges, ...chosen.map((s): [string, string] => [s, id])] });
                      }}
                      onClick={(event) => {
                        event.stopPropagation();
                      }}
                    >
                      {sources(id).map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="id">{id === OUTPUT ? view?.detail : view?.shape}</td>
                <td className="r">{view?.parameters || "–"}</td>
                <td className="acts-cell">
                  {locked ? null : (
                    <span className="acts">
                      <Button size="sm" variant="ghost" aria-label={`Move ${id} up`} onClick={() => { const g = moveNode(graph, id, "up"); if (g) onChange(g); }}>↑</Button>
                      <Button size="sm" variant="ghost" aria-label={`Move ${id} down`} onClick={() => { const g = moveNode(graph, id, "down"); if (g) onChange(g); }}>↓</Button>
                      <Button size="sm" variant="ghost" aria-label={`Remove ${id}`} onClick={() => { onRemove(id); }}><Icon name="x" /></Button>
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------- inspector

/** The selected layer's form (UI-2.3): its schema's fields, ranges and help; inferred sizes
 * shown as placeholders and left out of the spec until typed. */
export function Inspector({ node, type, report, inShape, onChange, onRemove }: {
  node: GraphNode;
  type: LayerType | undefined;
  report: NodeReport | undefined;
  /** The shape the node's (first) input has, for "inferred: 3". */
  inShape: number[] | null;
  onChange: (layer: LayerSpec) => void;
  onRemove: () => void;
}) {
  const values = Object.fromEntries(Object.entries(node.layer).filter(([k]) => k !== "type"));
  const inputs = report?.inputs ?? [];
  return (
    <>
      <header>
        <span className="mono">{node.id}</span> · {type?.title ?? node.layer.type}
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={onRemove}>
          <Icon name="x" />
          Remove
        </Button>
      </header>
      <div className="insp">
        {type ? (
          <SchemaForm
            key={node.id}
            schema={type.form}
            values={values}
            placeholders={Object.fromEntries(type.inferred.map((f) => [f, inferredHint(f, inShape)]))}
            onChange={(next) => {
              const layer: LayerSpec = { type: node.layer.type };
              for (const [k, v] of Object.entries(next)) if (v !== undefined && v !== null && v !== "") layer[k] = v;
              onChange(layer);
            }}
          />
        ) : (
          <p className="note">This layer type is not in the schema.</p>
        )}
        {isMergeType(node.layer.type) ? (
          <p className="note">Inputs, in order: {inputs.join(", ") || "none yet"}. Concat joins them in this order.</p>
        ) : null}
        <div className="io">
          <span>out <b>{report?.shape_text ?? "?"}</b></span>
          <span><b>{report?.parameters === null || report?.parameters === undefined ? "–" : count(report.parameters)}</b> parameters</span>
        </div>
      </div>
    </>
  );
}

function inferredHint(field: string, shape: number[] | null): string {
  if (shape === null || shape.length === 0) return "inferred";
  const value = field === "in_channels" ? shape[0] : shape[shape.length - 1];
  return `inferred: ${String(value)}`;
}

function isMergeType(type: string): boolean {
  return type === "add" || type === "concat";
}

// ---------------------------------------------------------------------------- summary and problems

const OPTIMIZERS: { value: Optimizer; label: string }[] = [
  { value: "adam", label: "Adam" },
  { value: "sgd_momentum", label: "SGD + momentum" },
  { value: "sgd", label: "SGD" },
];

export function SummaryBar({ report, optimizer, onOptimizer, problems }: {
  report: SpecReport | null;
  optimizer: Optimizer;
  onOptimizer: (o: Optimizer) => void;
  problems: number;
}) {
  const memory = report?.memory;
  const total = memory ? memory.total_bytes : 0;
  const part = (n: number): CSSProperties => ({ width: total ? `${String((100 * n) / total)}%` : "0%" });
  const name = OPTIMIZERS.find((o) => o.value === optimizer)?.label ?? optimizer;
  return (
    <div className="sumbar" role="status" aria-live="polite">
      {report?.total_parameters !== null && report?.total_parameters !== undefined && memory ? (
        <>
          <span><span className="big">{count(report.total_parameters)}</span> parameters</span>
          <span className="sep" />
          <select className="select" aria-label="Optimizer for the estimate" value={optimizer} onChange={(e) => { onOptimizer(e.target.value as Optimizer); }}>
            {OPTIMIZERS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <span className="mem" aria-hidden="true">
            <i className="w" style={part(memory.weights_bytes)} />
            <i className="g" style={part(memory.gradients_bytes)} />
            <i className="o" style={part(memory.optimizer_bytes)} />
          </span>
          <span className="key w">weights {bytes(memory.weights_bytes)}</span>
          <span className="key g">gradients {bytes(memory.gradients_bytes)}</span>
          {memory.optimizer_bytes ? <span className="key o">{name} {bytes(memory.optimizer_bytes)}</span> : null}
          <span className="mono" style={{ color: "var(--text-primary)" }}>= {bytes(memory.total_bytes)}</span>
        </>
      ) : (
        <span style={{ color: "var(--text-muted)" }}>— parameters · memory — until the problems are fixed</span>
      )}
      <span className="spacer" />
      {problems === 0 ? (
        <span className="pill completed"><Icon name="check" />No problems</span>
      ) : (
        <span className="pill failed"><Icon name="alert" />{problems === 1 ? "1 problem" : `${String(problems)} problems`}</span>
      )}
      {report?.family ? <Badge>{report.family}</Badge> : null}
    </div>
  );
}

export function ProblemList({ problems, onSelect }: { problems: SpecProblem[]; onSelect: (id: string) => void }) {
  if (problems.length === 0) return null;
  return (
    <section className="problems" aria-labelledby="problems-head">
      <header id="problems-head">
        <Icon name="alert" />
        {problems.length === 1 ? "1 problem" : `${String(problems.length)} problems`}
        <span>· fix them to continue</span>
      </header>
      {problems.map((p) => (
        <div className="prob" key={`${p.node_id ?? ""}:${p.code}:${p.message}`}>
          <Icon name="alert" />
          <span>
            {p.node_id ? (
              <>
                <button type="button" className="link" onClick={() => { onSelect(p.node_id ?? ""); }}>{p.node_id}</button>
                {p.message.startsWith(`${p.node_id}:`) ? p.message.slice(p.node_id.length) : `: ${p.message}`}
              </>
            ) : (
              p.message
            )}
          </span>
          <span className="code">{p.code}</span>
        </div>
      ))}
    </section>
  );
}
