// SCR-08 (M9, mockup SCR-08_v1): build a model from layers, checked by the server on every
// edit (BUILD-1.1–1.9, UI-2.3, DATA-5.5). ?version= is the split the model is for;
// ?view=list is the keyboard view. The draft lives in session storage per version
// (decision 9) and goes to SCR-09 with Continue to training.
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import {
  type Optimizer,
  type Template,
  useDatasetTypes,
  useDeleteTemplate,
  useSaveTemplate,
  useSpecSchema,
  useTemplates,
  useValidate,
} from "../../api/builder";
import { useDatasets, useVersion, useVersions } from "../../api/queries";
import { Drawer } from "../../components/Drawer";
import { Modal } from "../../components/Modal";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";
import {
  addAfter,
  connect,
  emptyGraph,
  type Graph,
  inputsOf,
  INPUT,
  loadDraft,
  removeNode,
  saveDraft,
  setLayer,
} from "../../lib/graph";
import { useCrumbs, useShortcuts } from "../../shell/hooks";
import { GraphCanvas } from "./GraphCanvas";
import { layerTypes, newLayer, unavailable } from "./layers";
import { Inspector, ListView, nodeViews, Palette, ProblemList, SummaryBar } from "./parts";

/** The first layer that fits each kind of input: a convolution for images, an embedding
 * for tokens, a linear layer for a vector. */
const FIRST_LAYER: Record<string, string> = { image: "conv2d", tokens: "embedding", vector: "linear" };

function mediaList(query: string): MediaQueryList | null {
  const match = (window as { matchMedia?: (q: string) => MediaQueryList }).matchMedia;
  return typeof match === "function" ? match.call(window, query) : null; // jsdom has none
}

function useNarrow(): boolean {
  const query = "(max-width: 1279px)";
  const [narrow, setNarrow] = useState(() => mediaList(query)?.matches ?? false);
  useEffect(() => {
    const list = mediaList(query);
    if (!list) return;
    const on = () => {
      setNarrow(list.matches);
    };
    list.addEventListener("change", on);
    return () => {
      list.removeEventListener("change", on);
    };
  }, []);
  return narrow;
}

export function BuilderScreen() {
  useCrumbs([{ label: "Models", to: "/models" }, { label: "Build" }]);
  const [params] = useSearchParams();
  const version = params.get("version");
  // A new version starts from its own draft: remount rather than reset state in an effect.
  return <Builder key={version ?? ""} version={version} />;
}

function Builder({ version }: { version: string | null }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "canvas";
  const narrow = useNarrow();

  const schema = useSpecSchema();
  const types = useMemo(() => (schema.data ? layerTypes(schema.data.layer) : []), [schema.data]);
  const datasetTypes = useDatasetTypes();
  const [graph, setGraphState] = useState<Graph>(() => (version ? loadDraft(version) : null) ?? emptyGraph("my_model"));
  const [selected, setSelected] = useState<string | null>(null);
  const [optimizer, setOptimizer] = useState<Optimizer>("adam");
  const [dialog, setDialog] = useState<"load" | "save" | null>(null);

  function setGraph(next: Graph) {
    setGraphState(next);
    if (version) saveDraft(version, next);
  }

  const validation = useValidate(version, graph, optimizer);
  const ends = validation.data?.dataset ?? null;
  const report = validation.data?.report ?? null;
  const datasetType = datasetTypes.data?.find((t) => t.name === ends?.dataset_type) ?? null;
  const views = useMemo(() => nodeViews(graph, types, report, ends), [graph, types, report, ends]);
  const problems = report?.problems ?? [];
  const canContinue = report?.valid === true && version !== null && graph.nodes.length > 0 && ends?.trainable === true;

  function set(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  function add(typeName: string) {
    const type = types.find((t) => t.type === typeName);
    if (!type) return;
    const [next, id] = addAfter(graph, selected, newLayer(type));
    setGraph(next);
    setSelected(id);
  }

  function remove(id: string) {
    setGraph(removeNode(graph, id));
    setSelected(null);
  }

  function applyTemplate(template: Template) {
    setGraph(template.spec as unknown as Graph);
    setSelected(null);
    setDialog(null);
  }

  useShortcuts(useMemo(() => ({ a: () => { document.querySelector<HTMLButtonElement>(".scr08 .tile:not([aria-disabled])")?.focus(); } }), []));

  const node = graph.nodes.find((n) => n.id === selected) ?? null;
  const nodeReport = report?.nodes.find((n) => n.id === selected);
  const firstInput = node ? inputsOf(graph, node.id)[0] : undefined;
  const inShape =
    firstInput === undefined
      ? null
      : firstInput === INPUT
        ? ((ends?.input as { dims?: number[] } | null)?.dims ?? null)
        : (((report?.nodes.find((n) => n.id === firstInput)?.shape ?? null) as { dims?: number[] } | null)?.dims ?? null);
  const inspector = node ? (
    <Inspector
      node={node}
      type={types.find((t) => t.type === node.layer.type)}
      report={nodeReport}
      inShape={inShape}
      onChange={(layer) => {
        setGraph(setLayer(graph, node.id, layer));
      }}
      onRemove={() => {
        remove(node.id);
      }}
    />
  ) : (
    <>
      <header>Layer</header>
      <div className="insp"><p className="note">Select a layer to see its settings. Press <kbd>a</kbd> to pick one to add.</p></div>
    </>
  );

  return (
    <div className="scr08">
      <div className="phead">
        <h1>Build a model</h1>
        <span className="spacer" />
        <span className="group-btns" role="group" aria-label="View">
          <Button size="sm" aria-pressed={view === "canvas"} onClick={() => { set("view", null); }}><Icon name="branch" />Canvas</Button>
          <Button size="sm" aria-pressed={view === "list"} onClick={() => { set("view", "list"); }}><Icon name="list" />List</Button>
        </span>
        <Button size="sm" disabled={!ends} onClick={() => { setDialog("load"); }}>Load template…</Button>
        <Button size="sm" disabled={graph.nodes.length === 0 || !ends} onClick={() => { setDialog("save"); }}>Save as template…</Button>
        <Button
          variant="primary"
          disabled={!canContinue}
          title={canContinue ? undefined : problems.length ? "Fix the problems first" : "Choose a trainable dataset and add layers first"}
          onClick={() => {
            if (version) void navigate(`/runs/new?version=${version}`);
          }}
        >
          Continue to training
          <Icon name="chevron" />
        </Button>
      </div>
      <DatasetBar version={version} onVersion={(v) => { set("version", v); }} ends={ends} familiesNote={datasetType?.families ?? []} />
      {version === null ? (
        <EmptyState icon="db" title="Choose the split this model is for.">
          Its images fix the model's input and its classes the output (BUILD-1.3).
        </EmptyState>
      ) : schema.isError || (validation.isError && !validation.data) ? (
        <ErrorState
          error={schema.error ?? validation.error}
          title="The spec could not be checked"
          onRetry={() => { void schema.refetch(); void validation.refetch(); }}
        />
      ) : ends?.problem && ends.dataset_type !== "labelled_images" && !ends.input ? (
        <EmptyState icon="info" title="This version cannot be trained yet" action={<Button size="sm" onClick={() => { set("version", null); }}>Choose another dataset</Button>}>
          {ends.problem}
        </EmptyState>
      ) : !ends ? (
        <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <Skeleton width={260} height={18} />
          <Skeleton width={520} height={90} />
        </div>
      ) : graph.nodes.length === 0 ? (
        <StartFrom
          datasetType={ends.dataset_type}
          version={version}
          onUse={applyTemplate}
          onFirst={() => { add(FIRST_LAYER[datasetType?.input_kind ?? "vector"] ?? "linear"); }}
          problem={ends.problem}
        />
      ) : (
        <>
          <div className="bgrid">
            {!narrow ? (
              <section className="panel side-panel" aria-label="Layers">
                <header>Layers</header>
                {types.length ? <Palette types={types} datasetType={datasetType} onAdd={(t) => { add(t.type); }} /> : <div style={{ padding: 12 }}><Skeleton width={140} /></div>}
              </section>
            ) : null}
            <section className="panel" aria-label="Graph">
              <header>
                {view === "list" ? "Layers in order" : "Graph"}
                <span className="spacer" />
                <span className="sub">{graph.label} · {String(graph.nodes.length)} layers</span>
                {narrow ? <LayersMenu types={types.filter((t) => unavailable(t.type, datasetType) === null).map((t) => ({ type: t.type, title: t.title }))} onAdd={add} /> : null}
              </header>
              {view === "list" ? (
                <ListView graph={graph} views={views} selected={selected} onSelect={setSelected} onChange={setGraph} onRemove={remove} />
              ) : (
                <div className="canvas">
                  <GraphCanvas graph={graph} views={views} selected={selected} onSelect={setSelected} onConnect={(a, b) => { setGraph(connect(graph, a, b)); }} />
                </div>
              )}
            </section>
            {!narrow ? <section className="panel side-panel" aria-label="Selected layer">{inspector}</section> : null}
          </div>
          <ProblemList problems={problems} onSelect={setSelected} />
          <SummaryBar report={report} optimizer={optimizer} onOptimizer={setOptimizer} problems={problems.length} />
        </>
      )}
      {narrow && node ? (
        <Drawer open onOpenChange={(open) => { if (!open) setSelected(null); }} title={`${node.id} settings`} width={320} className="scr08">
          {inspector}
        </Drawer>
      ) : null}
      <LoadTemplate open={dialog === "load"} datasetType={ends?.dataset_type ?? null} onClose={() => { setDialog(null); }} onUse={applyTemplate} replacing={graph.nodes.length > 0} />
      <SaveTemplate open={dialog === "save"} graph={graph} datasetType={ends?.dataset_type ?? null} onClose={() => { setDialog(null); }} onLabel={(label) => { setGraph({ ...graph, label }); }} />
    </div>
  );
}

// ---------------------------------------------------------------------------- dataset

function DatasetBar({ version, onVersion, ends, familiesNote }: {
  version: string | null;
  onVersion: (id: string | null) => void;
  ends: { dataset_type: string; input_text: string | null; output_size: number | null } | null;
  familiesNote: string[];
}) {
  const current = useVersion(version ?? "");
  const [slug, setSlug] = useState<string | undefined>(undefined);
  const datasets = useDatasets();
  const dataset = slug ?? current.data?.dataset;
  const versions = useVersions(dataset);
  const splits = (versions.data?.items ?? []).filter((v) => v.stage === "split");
  return (
    <div className="dsbar">
      <select
        className="select mono"
        aria-label="Dataset"
        value={dataset ?? ""}
        onChange={(e) => {
          setSlug(e.target.value || undefined);
          onVersion(null);
        }}
      >
        <option value="">Choose a dataset…</option>
        {(datasets.data?.items ?? []).map((d) => (
          <option key={d.slug} value={d.slug}>{d.slug}</option>
        ))}
      </select>
      <select
        className="select mono"
        aria-label="Split version"
        value={version ?? ""}
        disabled={!dataset}
        onChange={(e) => {
          onVersion(e.target.value || null);
        }}
      >
        <option value="">{dataset ? "Choose a split…" : "—"}</option>
        {splits.map((v) => (
          <option key={v.version_id} value={v.version_id}>{v.version_id} · {count(v.row_count)} rows</option>
        ))}
        {version && !splits.some((v) => v.version_id === version) ? <option value={version}>{version}</option> : null}
      </select>
      {ends ? (
        <>
          <span>{ends.dataset_type.replaceAll("_", " ")}</span>
          {ends.input_text ? <span className="ends">input {ends.input_text} → {String(ends.output_size ?? "?")} classes</span> : null}
          {familiesNote.length ? <Badge>takes {familiesNote.join(", ")}</Badge> : null}
        </>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- templates

function TemplateCount({ version, spec }: { version: string; spec: Graph }) {
  const v = useValidate(version, spec, "adam");
  const total = v.data?.report?.total_parameters;
  return <>{total === null || total === undefined ? "–" : count(total)}</>;
}

function StartFrom({ datasetType, version, onUse, onFirst, problem }: {
  datasetType: string | null;
  version: string;
  onUse: (t: Template) => void;
  onFirst: () => void;
  problem: string | null;
}) {
  const templates = useTemplates(datasetType);
  const remove = useDeleteTemplate();
  return (
    <div className="state" style={{ padding: "var(--sp-4)" }}>
      <Icon name="branch" />
      <b>Start from a template, or add a first layer.</b>
      {problem ? <p>{problem}</p> : null}
      {templates.isError ? (
        <ErrorState error={templates.error} title="Templates could not be loaded" onRetry={() => void templates.refetch()} />
      ) : (
        <div className="tpl">
          {(templates.data ?? []).map((t) => (
            <div className="tplc" key={t.id}>
              <b>{t.name}</b>
              <p>{t.description}</p>
              <div className="meta">
                {t.builtin ? <Badge>built-in</Badge> : null}
                <TemplateCount version={version} spec={t.spec as unknown as Graph} />
                <span className="spacer" />
                {t.builtin ? null : <Button size="sm" variant="ghost" onClick={() => { remove.mutate(t.id); }}>Delete</Button>}
                <Button size="sm" onClick={() => { onUse(t); }}>Use</Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Button size="sm" onClick={onFirst}><Icon name="plus" />Add a first layer</Button>
    </div>
  );
}

function LoadTemplate({ open, datasetType, onClose, onUse, replacing }: {
  open: boolean;
  datasetType: string | null;
  onClose: () => void;
  onUse: (t: Template) => void;
  replacing: boolean;
}) {
  const templates = useTemplates(open ? datasetType : null);
  return (
    <Modal open={open} onOpenChange={(o) => { if (!o) onClose(); }} title="Load a template" width={520}
      footer={<Button onClick={onClose}>Cancel</Button>}>
      {replacing ? <p>Loading a template replaces the graph you are building.</p> : null}
      <div className="table-wrap">
        <table className="t">
          <tbody>
            {(templates.data ?? []).map((t) => (
              <tr key={t.id}>
                <td>{t.name} {t.builtin ? <Badge>built-in</Badge> : null}</td>
                <td className="r"><Button size="sm" onClick={() => { onUse(t); }}>Use</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

function SaveTemplate({ open, graph, datasetType, onClose, onLabel }: {
  open: boolean;
  graph: Graph;
  datasetType: string | null;
  onClose: () => void;
  onLabel: (label: string) => void;
}) {
  const save = useSaveTemplate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const problem = save.error && "problem" in save.error ? (save.error as { problem: { detail: string } }).problem.detail : null;
  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) { save.reset(); onClose(); } }}
      title="Save as template"
      width={460}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!name.trim() || !datasetType || save.isPending}
            onClick={() => {
              if (!datasetType) return;
              save.mutate(
                { name: name.trim(), description, dataset_type: datasetType, spec: graph },
                { onSuccess: () => { onLabel(name.trim().toLowerCase().replaceAll(/[^a-z0-9_]+/g, "_").slice(0, 64) || graph.label); onClose(); } },
              );
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <div className={problem ? "field error" : "field"}>
        <label htmlFor="tpl-name">Name</label>
        <input id="tpl-name" className="input" value={name} onChange={(e) => { setName(e.target.value); }} aria-invalid={problem ? true : undefined} />
        {problem ? <small>{problem}</small> : null}
      </div>
      <div className="field" style={{ marginTop: "var(--sp-3)" }}>
        <label htmlFor="tpl-desc">Description</label>
        <input id="tpl-desc" className="input" value={description} onChange={(e) => { setDescription(e.target.value); }} />
      </div>
    </Modal>
  );
}

function LayersMenu({ types, onAdd }: { types: { type: string; title: string }[]; onAdd: (type: string) => void }) {
  return (
    <select className="select" aria-label="Add a layer" value="" onChange={(e) => { if (e.target.value) onAdd(e.target.value); }}>
      <option value="">+ Layers</option>
      {types.map((t) => <option key={t.type} value={t.type}>{t.title}</option>)}
    </select>
  );
}
