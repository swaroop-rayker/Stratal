// SCR-09 (M9, mockup SCR-09_v1; study part M10, SCR-09hpo_v1): set up one training run, or a
// study of many, and start it (BUILD-1.2, BUILD-1.3, RUN-1.9, UI-2.3, ML-1.1, ML-1.4,
// HPO-1.1). The model is a network (the builder's draft for ?version=, or with ?from=job_…
// that run's model and settings, ?batch_size= overriding) or a classical estimator. ?study=
// prefills the best configuration of a study (HPO-1.5). The VRAM check warns and never
// blocks (decision 6); for a study it takes the worst case in the space.
import type { RJSFSchema } from "@rjsf/utils";
import { useQuery } from "@tanstack/react-query";
import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { type Optimizer, useSpecSchema, useTemplates, useValidate } from "../../api/builder";
import { ApiError, api, call } from "../../api/client";
import { useStageSchema, useSystem, useVersion } from "../../api/queries";
import { useSettings } from "../../api/registry";
import { fieldName, type Marker, useStudy } from "../../api/studies";
import { type FieldDecor, SchemaForm } from "../../components/SchemaForm";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { bytes, count } from "../../lib/format";
import { emptyGraph, type Graph, loadDraft } from "../../lib/graph";
import { useCrumbs } from "../../shell/hooks";
import { inputVersion } from "../job/jobs";
import { layerTypes } from "./layers";
import { AddParameter, type LayerField, StudyCard, type StudyLimits, type SpaceRow, TuneButton, TuneEditor, tuneHelper } from "./StudyParts";
import { isTunable, largest, NETWORK_TUNABLE, paths, spaceErrors, specProblem, startSpec, toMarker, TRIAL_START_S, type TuneSpec } from "./study";
import { vramCheck } from "./vram";

type Values = Record<string, unknown>;
type Device = "auto" | "gpu" | "cpu";
type Mode = "network" | "classical";
type Task = "classification" | "regression";
const REFERENCE_TEMPLATE: Record<string, string> = { mlp: "MLP", small_cnn: "Small CNN" };
const HIDDEN = new Set(["model", "device", "loader_workers", "algorithm_version"]);

/** The objectives a study may optimise, by model kind and task (stratal.hpo.config.OBJECTIVES). */
export const OBJECTIVES: Record<Mode, Record<Task, Record<string, "maximize" | "minimize">>> = {
  network: {
    classification: { val_accuracy: "maximize", val_loss: "minimize" },
    regression: { val_rmse: "minimize", val_loss: "minimize" },
  },
  classical: {
    classification: { cv_accuracy: "maximize", cv_macro_f1: "maximize" },
    regression: { cv_r2: "maximize", cv_rmse: "minimize" },
  },
};

/** The train schema without the fields this page handles itself; momentum only for SGD. */
export function trainForm(schema: RJSFSchema, optimizer: unknown): RJSFSchema {
  const properties = Object.fromEntries(
    Object.entries(schema.properties ?? {}).filter(([name]) => !HIDDEN.has(name) && (name !== "momentum" || optimizer === "sgd")),
  );
  return { type: "object", properties };
}

/** An estimator's parameters as a form: a nullable field becomes its non-null type. */
export function paramsForm(schema: RJSFSchema | undefined): RJSFSchema {
  const properties: Record<string, RJSFSchema> = {};
  for (const [name, prop] of Object.entries((schema?.properties ?? {}) as Record<string, RJSFSchema>)) {
    const options = (prop.anyOf ?? []) as RJSFSchema[];
    const real = options.filter((o) => o.type !== "null");
    if (options.length && real.length === 1) {
      const rest: RJSFSchema = { ...prop };
      delete rest.anyOf;
      properties[name] = { ...rest, ...real[0], default: undefined };
    } else properties[name] = prop;
  }
  return { type: "object", properties };
}

/** A field's value as text; "?" for anything that is not a number or a string. */
function text(value: unknown): string {
  return typeof value === "number" || typeof value === "string" ? String(value) : "?";
}

function optimizerOf(values: Values): Optimizer {
  if (values.optimizer !== "sgd") return "adam";
  return typeof values.momentum === "number" && values.momentum > 0 ? "sgd_momentum" : "sgd";
}

function trainRows(counts: Record<string, unknown> | undefined): number | null {
  const split = counts?.split as { splits?: Record<string, { rows?: number }> } | undefined;
  return split?.splits?.train?.rows ?? null;
}

function splitRows(counts: Record<string, unknown> | undefined): Record<string, number> {
  const split = counts?.split as { splits?: Record<string, { rows?: number }> } | undefined;
  return Object.fromEntries(Object.entries(split?.splits ?? {}).map(([k, v]) => [k, v.rows ?? 0]));
}

/** The graph with each tuned layer field at its largest value: the VRAM check's worst case. */
export function worstGraph(graph: Graph, tuned: Record<string, TuneSpec>): Graph {
  const nodes = graph.nodes.map((n) => {
    let layer = n.layer;
    for (const [path, spec] of Object.entries(tuned)) {
      const prefix = paths.layer(n.id, "");
      if (!path.startsWith(prefix)) continue;
      const top = largest(spec);
      if (top !== null) layer = { ...layer, [path.slice(prefix.length)]: top };
    }
    return layer === n.layer ? n : { ...n, layer };
  });
  return nodes.some((n, i) => n !== graph.nodes[i]) ? { ...graph, nodes } : graph;
}

/** ``tuned`` without one field. */
function without(tuned: Record<string, TuneSpec>, path: string): Record<string, TuneSpec> {
  return Object.fromEntries(Object.entries(tuned).filter(([p]) => p !== path));
}

type Estimators = Record<string, { title: string; tasks: string[]; schema: RJSFSchema }>;

export function RunScreen() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const fromJob = params.get("from");
  const fromStudy = params.get("study");
  const job = useQuery({
    queryKey: ["jobs", fromJob],
    queryFn: () => call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: fromJob ?? "" } } })),
    enabled: fromJob !== null,
  });
  const study = useStudy(fromStudy ?? "");
  const bestConfig = useQuery({
    queryKey: ["studies", fromStudy, "best-config"],
    queryFn: () => call(() => api.GET("/api/v1/studies/{study_id}/best-config", { params: { path: { study_id: fromStudy ?? "" } } })) as Promise<{ config?: Values; classical?: { model: { estimator: string; params?: Values }; seed?: number } }>,
    enabled: fromStudy !== null,
  });
  const classicalSeed = bestConfig.data?.classical ?? (job.data?.kind === "train" && job.data.stages[0]?.name === "train_classical" ? (job.data.stages[0].config as { model: { estimator: string; params?: Values }; seed?: number }) : null);
  const jobConfig: Values | null = bestConfig.data?.config ?? (classicalSeed ? null : (job.data?.stages[0]?.config ?? null));
  const version = params.get("version") ?? (job.data ? inputVersion(job.data) : null) ?? study.data?.dataset_version ?? null;
  const overrideBatch = params.get("batch_size");

  const schema = useStageSchema("train", true);
  const settings = useSettings();
  const system = useSystem();
  const versionInfo = useVersion(version ?? "", version !== null);
  const templates = useTemplates(null);
  const specSchema = useSpecSchema();
  const estimators = useQuery({
    queryKey: ["ml", "estimators"],
    queryFn: () => call(() => api.GET("/api/v1/ml/estimators/schema")) as Promise<Estimators>,
  });

  // The model: a reference model from the job, else the graph in the config, else the draft.
  const reference = typeof (jobConfig?.model as { name?: string } | undefined)?.name === "string" && (jobConfig?.model as { name: string }).name !== "graph"
    ? (jobConfig?.model as { name: string }).name
    : null;
  const graph: Graph | null = useMemo(() => {
    const fromConfig = (jobConfig?.model as { graph?: Graph } | undefined)?.graph;
    if (fromConfig) return fromConfig;
    if (reference) {
      const template = templates.data?.find((t) => t.name === REFERENCE_TEMPLATE[reference]);
      return template ? (template.spec as unknown as Graph) : null;
    }
    return version ? loadDraft(version) : null;
  }, [jobConfig, reference, templates.data, version]);
  const tabular = versionInfo.data?.dtype === "tabular";
  const [modeChoice, setMode] = useState<Mode | null>(null);
  const mode: Mode = modeChoice ?? (classicalSeed ? "classical" : graph || reference || !tabular ? "network" : "classical");
  useCrumbs([{ label: "Models", to: "/models" }, { label: "Build", to: "/models/build" }, { label: "New run" }]);

  // Initial training values: the schema's defaults, SCR-14's checkpoint interval, then the
  // job's or study's settings and ?batch_size=. State holds only the edits.
  const initial = useMemo(() => {
    if (!schema.data || (fromJob && !job.data) || (fromStudy && !bestConfig.data && !bestConfig.isError)) return null;
    const defaults: Values = {};
    for (const [name, prop] of Object.entries((schema.data as RJSFSchema).properties ?? {})) {
      const value = typeof prop === "object" ? prop.default : undefined;
      if (!HIDDEN.has(name) && value !== undefined && value !== null) defaults[name] = value;
    }
    const training = settings.data?.sections.find((s) => s.name === "training")?.values as { checkpoint_interval_min?: number } | undefined;
    if (training?.checkpoint_interval_min) defaults.checkpoint_every_s = 60 * training.checkpoint_interval_min;
    const fromConfig = Object.fromEntries(Object.entries(jobConfig ?? {}).filter(([k]) => !HIDDEN.has(k)));
    const next = { ...defaults, ...fromConfig };
    const was: Record<string, unknown> = {};
    if (overrideBatch) {
      was.batch_size = next.batch_size;
      next.batch_size = Number(overrideBatch);
    }
    return { values: next, was };
  }, [schema.data, settings.data, jobConfig, fromJob, job.data, fromStudy, bestConfig.data, bestConfig.isError, overrideBatch]);
  const [edits, setValues] = useState<Values | null>(null);
  const values = edits ?? initial?.values ?? null;
  const [deviceChoice, setDevice] = useState<Device | null>(null);
  const device: Device = deviceChoice ?? (jobConfig?.device as Device | undefined) ?? "auto";
  const [changedEdit, setChanged] = useState<Record<string, unknown> | null>(null);
  const changed = changedEdit ?? initial?.was ?? {};
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);

  // Classical: the estimator and its parameters.
  const [estimatorChoice, setEstimator] = useState<string | null>(null);
  const [paramEdits, setParams] = useState<Values | null>(null);
  const [classicalSeedValue, setClassicalSeed] = useState<number | null>(null);

  // The study: tuned fields by marker path, the limits, and whether it is on.
  const [tuned, setTuned] = useState<Record<string, TuneSpec>>({});
  const [studyOn, setStudyOn] = useState(false);
  const [limits, setLimits] = useState<StudyLimits | null>(null);
  const [spaceProblems, setSpaceProblems] = useState<Map<string, string>>(new Map());
  const [adding, setAdding] = useState(false);

  const optimizer = optimizerOf(values ?? {});
  const validation = useValidate(version, graph ?? emptyGraph(), optimizer);
  const report = validation.data?.report ?? null;
  const ends = validation.data?.dataset ?? null;
  const task: Task = ends && ends.classes.length === 0 && ends.output_size === 1 ? "regression" : "classification";
  const objectives = OBJECTIVES[mode][task];
  const limitsNow: StudyLimits = limits ?? { n_trials: 20, minutes: 60, objective: Object.keys(objectives)[0] ?? "", pruning: true, seed: 0, cv_folds: 5 };
  const objective = limitsNow.objective in objectives ? limitsNow.objective : (Object.keys(objectives)[0] ?? "");

  const estimatorList = Object.entries(estimators.data ?? {}).filter(([, e]) => e.tasks.includes(task) || (!studyOn && !e.tasks.some((t) => t === "classification" || t === "regression")));
  const estimator = estimatorChoice ?? classicalSeed?.model.estimator ?? (estimators.data?.random_forest ? "random_forest" : estimatorList[0]?.[0] ?? null);
  const estimatorSchema = estimator ? estimators.data?.[estimator]?.schema : undefined;
  const paramValues: Values = paramEdits ?? (classicalSeed && classicalSeed.model.estimator === estimator ? (classicalSeed.model.params ?? {}) : {});

  // The worst case for the VRAM check: the widest layers and the largest batch in the space.
  const graphTuned = Object.keys(tuned).some((p) => p.startsWith("/config/model/"));
  const worst = useMemo(() => (graph && studyOn && graphTuned ? worstGraph(graph, tuned) : graph ?? emptyGraph()), [graph, studyOn, graphTuned, tuned]);
  const worstValidation = useValidate(studyOn && graphTuned ? version : null, worst, optimizer);
  const worstReport = studyOn && graphTuned ? worstValidation.data?.report ?? null : report;
  const batchSpec = studyOn ? tuned[paths.train("batch_size")] : undefined;
  const batch = typeof values?.batch_size === "number" ? values.batch_size : 128;
  const worstBatch = batchSpec ? largest(batchSpec) ?? batch : batch;
  const memory = worstReport?.memory;
  const check = memory && system.data?.vram_total_mb ? vramCheck(memory.total_bytes, memory.activation_bytes_per_sample, worstBatch, system.data.vram_total_mb) : null;
  const choices = batchSpec?.scale === "choices" ? (batchSpec.choices ?? []).filter((c): c is number => typeof c === "number").sort((a, b) => b - a) : [];
  const nextBatch = choices[1];
  const nextCheck = nextBatch !== undefined && memory && system.data?.vram_total_mb ? vramCheck(memory.total_bytes, memory.activation_bytes_per_sample, nextBatch, system.data.vram_total_mb) : null;
  const rows = trainRows(versionInfo.data?.counts);
  const steps = rows === null ? null : Math.ceil(rows / batch);

  // Search space rows, in the order fields were tuned.
  const trainSchema = (schema.data?.properties ?? {}) as Record<string, RJSFSchema>;
  const layerFields: LayerField[] = useMemo(() => {
    if (!graph || !specSchema.data) return [];
    const types = new Map(layerTypes(specSchema.data.layer).map((t) => [t.type, t]));
    return graph.nodes.flatMap((n) => {
      const type = types.get(n.layer.type);
      if (!type) return [];
      return Object.entries((type.form.properties ?? {}) as Record<string, RJSFSchema>)
        .filter(([name, s]) => !type.inferred.includes(name) && isTunable(s) && s.type !== "boolean")
        .map(([field, s]) => ({ node: n.id, layer: n.layer.type, field, value: n.layer[field] ?? s.default, schema: s }));
    });
  }, [graph, specSchema.data]);
  const schemaOf = (path: string): RJSFSchema | undefined => {
    if (path.startsWith("/classical/model/params/")) return paramsForm(estimatorSchema).properties?.[path.split("/").pop() ?? ""] as RJSFSchema | undefined;
    const layer = layerFields.find((f) => paths.layer(f.node, f.field) === path);
    if (layer) return layer.schema;
    return trainSchema[path.split("/").pop() ?? ""];
  };
  const prefix = mode === "classical" ? "/classical/" : "/config/";
  const space: SpaceRow[] = Object.entries(tuned)
    .filter(([p]) => p.startsWith(prefix))
    .flatMap(([path, spec]) => {
      const s = schemaOf(path);
      return s ? [{ path, schema: s, spec, inline: path.startsWith("/config/model/") }] : [];
    });
  const markers: Marker[] = space.map((r) => toMarker(r.path, r.schema, r.spec));

  function toggle(path: string, s: RJSFSchema, value: unknown) {
    setSpaceProblems(new Map());
    if (tuned[path]) {
      setTuned(without(tuned, path));
      return;
    }
    setTuned({ ...tuned, [path]: startSpec(s, value) });
    setStudyOn(true);
  }

  function decor(pathOf: (name: string) => string, tunable: (name: string) => boolean, current: Values): FieldDecor {
    return {
      label: (name, s) => (studyOn || tunable(name) ? (tunable(name) && isTunable(s) ? <TuneButton name={name} on={Boolean(tuned[pathOf(name)])} onToggle={() => { toggle(pathOf(name), s, current[name]); }} /> : null) : null),
      replace: (name, s) => {
        const spec = tuned[pathOf(name)];
        return spec ? <TuneEditor name={name} schema={s} spec={spec} onChange={(next) => { setTuned({ ...tuned, [pathOf(name)]: next }); }} /> : null;
      },
      helper: (name, s) => {
        const spec = tuned[pathOf(name)];
        if (spec) return tuneHelper(s, spec, spaceProblems.get(pathOf(name)));
        return studyOn && tunable(name) ? null : studyOn ? "The same for every trial." : null;
      },
      className: (name) => (tuned[pathOf(name)] ? "tuned" : null),
      key: JSON.stringify([studyOn, tuned, [...spaceProblems]]),
    };
  }

  async function start() {
    if (!version) return;
    if (studyOn && space.some((r) => specProblem(r.schema, r.spec) !== null)) {
      // Frame C: the button stays enabled; a refused marker is shown where it is, and focus
      // moves to the first one.
      document.querySelector<HTMLElement>(".scr09 .field.tuned:has(small .bad) input, .scr09 tr.bad input, .scr09 tr.bad button")?.focus();
      return;
    }
    setStarting(true);
    setServerErrors({});
    setSpaceProblems(new Map());
    setFailure(null);
    const model = reference ? { name: reference } : { name: "graph", graph };
    const base = mode === "classical"
      ? { classical: { model: { kind: "sklearn", estimator, params: paramValues }, seed: classicalSeedValue ?? classicalSeed?.seed ?? 0 } }
      : { config: { ...values, model, device } };
    const body = studyOn && markers.length
      ? { kind: "hpo_study", dataset_version: version, ...base, space: markers, objective, n_trials: limitsNow.n_trials, timeout_s: Math.round(limitsNow.minutes * 60), pruning: limitsNow.pruning, seed: limitsNow.seed, ...(mode === "classical" ? { cv_folds: limitsNow.cv_folds } : {}) }
      : { kind: "train", dataset_version: version, ...base };
    try {
      const created = (await call(() => api.POST("/api/v1/jobs", { body: body as never }))) as { id: string; stages?: { config?: { study_id?: string } }[] };
      const studyId = created.stages?.[0]?.config?.study_id;
      void navigate(studyId ? `/studies/${studyId}` : `/jobs/${created.id}`);
    } catch (error) {
      setStarting(false);
      if (error instanceof ApiError) {
        const fields: Record<string, string> = {};
        for (const e of error.problem.errors) {
          const at = e.loc[1];
          if ((e.loc[0] === "config" || e.loc[0] === "classical") && typeof at === "string" && at !== "model") fields[at] = e.msg;
        }
        const rowErrors = spaceErrors(error.problem.errors);
        setSpaceProblems(new Map([...rowErrors].flatMap(([i, msg]) => (markers[i] ? [[markers[i].path, msg] as [string, string]] : []))));
        setServerErrors(fields);
        if (Object.keys(fields).length === 0 && rowErrors.size === 0) setFailure(error);
        document.querySelector<HTMLElement>(".scr09 .bad, .scr09 [aria-invalid=true]")?.focus();
      } else setFailure(error);
    }
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) void start();
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  });

  if (mode === "network" && !graph && !reference && !(fromJob && job.isLoading) && !(fromStudy && bestConfig.isLoading)) {
    return (
      <div className="scr09">
        <EmptyState icon="branch" title="Build or pick a model first."
          action={<span style={{ display: "flex", gap: "var(--sp-2)" }}><Button variant="primary" size="sm" onClick={() => { void navigate(`/models/build${version ? `?version=${version}` : ""}`); }}>Open the builder</Button>{tabular ? <Button size="sm" onClick={() => { setMode("classical"); }}>Use a classical model</Button> : null}</span>}>
          A run needs a model and a split to train it on.
        </EmptyState>
      </div>
    );
  }
  if (schema.isError || job.isError || bestConfig.isError) {
    return <div className="scr09"><ErrorState error={schema.error ?? job.error ?? bestConfig.error} title="The run setup could not be loaded" onRetry={() => { void schema.refetch(); void job.refetch(); void bestConfig.refetch(); }} /></div>;
  }

  const counts = splitRows(versionInfo.data?.counts);
  const problems = mode === "network" ? report?.problems ?? [] : [];
  const pct = (n: number): CSSProperties => ({ width: check ? `${String(Math.min(100, (100 * n) / check.card))}%` : "0%" });
  const isStudy = studyOn && markers.length > 0;
  const epochs = typeof values?.epochs === "number" ? values.epochs : null;
  const startWord = isStudy ? "Start study" : "Start run";

  const modeSwitch = (
    <span className="group-btns" role="group" aria-label="Model kind">
      <Button size="sm" aria-pressed={mode === "network"} onClick={() => { setMode("network"); }}>Network</Button>
      <Button size="sm" aria-pressed={mode === "classical"} disabled={!tabular} title={tabular ? undefined : "Classical models train on tables"} onClick={() => { setMode("classical"); }}>Classical</Button>
    </span>
  );

  return (
    <div className="scr09">
      <div className="phead"><h1>{isStudy ? "New study" : "New training run"}</h1><span className="sub">{isStudy ? "Many runs of one model, each with tuned fields drawn by TPE; the best is registered and tested once (HPO-1.4)" : "One run of a model on a split; it registers a version when it completes (REG-1.1)"}</span></div>
      {fromJob && job.data ? (
        <div className="callout info">
          <Icon name="info" />
          <span>
            Prefilled from <span className="mono">{fromJob}</span>
            {overrideBatch ? <>, which ran out of GPU memory at batch {text(changed.batch_size)}: <b>batch_size</b> is set to <b>{overrideBatch}</b>, everything else as in that run.</> : <>: the same model and settings.</>}
          </span>
        </div>
      ) : null}
      {fromStudy && study.data ? (
        <div className="callout info"><Icon name="info" /><span>Prefilled with the best configuration of <span className="mono">{fromStudy}</span>{study.data.best ? <> (trial {String(study.data.best.trial)}, {study.data.objective} {study.data.best.value?.toFixed(3) ?? "–"})</> : null}.</span></div>
      ) : null}
      <div className="runcols">
        <div className="cards">
          <section className="card">
            <header><h2>Model</h2><span className="req">{mode === "classical" ? "ML-1.1 · ML-1.4" : "BUILD-1.5"}</span>{modeSwitch}<span className="spacer" />
              {mode === "network" && !reference ? <Button size="sm" variant="ghost" onClick={() => { void navigate(`/models/build?version=${version ?? ""}`); }}><Icon name="branch" />Edit in builder</Button> : null}</header>
            <div className="b">
              {mode === "network" ? (
                <>
                  <div className="facts">
                    <span><b>{reference ?? graph?.label ?? "—"}</b> {reference ? <Badge>reference model</Badge> : <Badge>from the builder</Badge>}</span>
                    {report?.family ? <span>{report.family}</span> : null}
                    <span><b>{count(report?.total_parameters)}</b> parameters</span>
                    {report?.memory ? <span><b>{bytes(report.memory.total_bytes)}</b> with {optimizer === "adam" ? "Adam" : "SGD"}</span> : null}
                    {validation.data ? (problems.length === 0 ? <span className="pill completed"><Icon name="check" />No problems</span> : <span className="pill failed"><Icon name="alert" />{String(problems.length)} problems</span>) : <Skeleton width={90} />}
                  </div>
                  {problems.length ? (
                    <div className="callout err" role="alert">
                      <Icon name="alert" />
                      <span><span className="mono">{problems[0]?.code}</span> · {problems[0]?.message}</span>
                    </div>
                  ) : null}
                </>
              ) : (
                <>
                  <div className="facts">
                    <label className="sr-only" htmlFor="estimator">Estimator</label>
                    <select id="estimator" className="select mono" value={estimator ?? ""} onChange={(e) => { setEstimator(e.target.value); setParams({}); setTuned(Object.fromEntries(Object.entries(tuned).filter(([p]) => !p.startsWith("/classical/")))); }}>
                      {estimatorList.map(([name]) => <option key={name} value={name}>{name}</option>)}
                    </select>
                    <span>{estimator ? estimators.data?.[estimator]?.title : null}, after imputing, scaling and one-hot encoding fitted on the train split (ML-1.3).</span>
                  </div>
                  {estimatorSchema ? (
                    <div className="fgrid">
                      <SchemaForm
                        schema={paramsForm(estimatorSchema)}
                        values={paramValues}
                        serverErrors={serverErrors}
                        onChange={(next) => { setParams({ ...paramValues, ...next }); }}
                        decor={decor(paths.param, () => true, paramValues)}
                      />
                    </div>
                  ) : <Skeleton width={480} height={40} />}
                  <div className="field small">
                    <label htmlFor="classical-seed"><span className="mono">random_state</span><span className="type">int</span></label>
                    <input id="classical-seed" className="input mono" inputMode="numeric" value={String(classicalSeedValue ?? classicalSeed?.seed ?? 0)} onChange={(e) => { setClassicalSeed(Number(e.target.value) || 0); }} />
                    <small>{isStudy ? "The same for every trial." : "Where the estimator takes one."}</small>
                  </div>
                </>
              )}
            </div>
          </section>
          <section className="card">
            <header><h2>Data</h2><span className="req">BUILD-1.3</span></header>
            <div className="b">
              <div className="facts">
                <span className="mono" style={{ color: "var(--text-primary)" }}>{versionInfo.data?.dataset ?? "—"} · {version ?? "—"}</span>
                {Object.entries(counts).map(([k, v]) => <span key={k}>{k} <b>{count(v)}</b></span>)}
                {ends ? <Badge tone="info">{task}</Badge> : null}
                {ends?.input_text ? <span>{task === "classification" ? `${String(ends.output_size ?? "?")} classes · ` : ""}{ends.input_text}</span> : null}
              </div>
            </div>
          </section>
          {mode === "network" ? (
            <section className="card">
              <header><h2>Training</h2><span className="req">BUILD-1.2 · UI-2.3{studyOn ? " · HPO-1.1" : ""}</span><span className="spacer" />{studyOn ? <span className="note">{String(space.filter((r) => !r.inline).length)} fields tuned</span> : <span className="note">form from <span className="mono">GET /config/schemas/train</span></span>}</header>
              <div className="b">
                {schema.data && values ? (
                  <div className="fgrid">
                    <SchemaForm
                      schema={trainForm(schema.data, values.optimizer)}
                      values={values}
                      serverErrors={serverErrors}
                      onChange={(next) => { setValues({ ...values, ...next }); }}
                      decor={decor(paths.train, (name) => NETWORK_TUNABLE.has(name), values)}
                    />
                  </div>
                ) : (
                  <Skeleton width={480} height={40} />
                )}
                {Object.keys(changed).length ? (
                  <span className="note">
                    <Badge tone="info">changed</Badge> batch_size was {text(changed.batch_size)} in <span className="mono">{fromJob}</span>{" "}
                    <Button size="sm" variant="ghost" onClick={() => { setValues({ ...values, batch_size: changed.batch_size }); setChanged({}); }}><Icon name="refresh" />Back to {text(changed.batch_size)}</Button>
                  </span>
                ) : null}
              </div>
            </section>
          ) : null}
          {mode === "network" ? (
            <>
              <section className="card">
                <header><h2>Start from</h2><span className="req">ADR-0015</span></header>
                <div className="b"><span>{isStudy ? "Each trial starts from its lineage's base if one exists, else from its own initial weights; only the best trial founds or joins a lineage (ADR-0018 rule 7)." : "The run starts from its lineage's base: an existing lineage's, or a new one saved before step 1. Choosing a trained version to start from is not offered yet."}</span></div>
              </section>
              <section className="card">
                <header><h2>Device</h2><span className="req">RUN-1.9</span></header>
                <div className="b">
                  <div style={{ display: "flex", gap: "var(--sp-3)", alignItems: "center" }}>
                    <span className="group-btns" role="group" aria-label="Device">
                      {(["auto", "gpu", "cpu"] as const).map((d) => (
                        <Button key={d} size="sm" aria-pressed={device === d} onClick={() => { setDevice(d); }}>{d === "auto" ? "Auto" : d.toUpperCase()}</Button>
                      ))}
                    </span>
                    <span>{device === "auto" ? "Under 1 M parameters, Auto times 20 steps on each device and trains on the faster; larger models use the GPU." : device === "gpu" ? "Trains on the GPU queue." : "Trains on the CPU worker."}</span>
                  </div>
                </div>
              </section>
            </>
          ) : null}
          {studyOn ? (
            <StudyCard
              classical={mode === "classical"}
              rows={space}
              errors={spaceProblems}
              limits={{ ...limitsNow, objective }}
              objectives={objectives}
              onLimits={setLimits}
              onRow={(path, spec) => { setTuned({ ...tuned, [path]: spec }); }}
              onRemove={(path) => { setTuned(without(tuned, path)); document.querySelector<HTMLElement>(`[aria-label="Tune ${fieldName(path)}"]`)?.focus(); }}
              onAdd={mode === "network" && graph && !reference ? () => { setAdding(true); } : null}
              addable={layerFields.length > 0}
              onOff={() => { setStudyOn(false); setTuned({}); }}
            />
          ) : (
            <section className="card collapsed">
              <header><Icon name="sliders" /><h2>Tune with a study</h2><span className="req">HPO-1.1</span><span className="spacer" />
                <Button size="sm" onClick={() => { setStudyOn(true); }}>Turn on</Button></header>
            </section>
          )}
          {adding ? (
            <AddParameter
              fields={layerFields.filter((f) => !tuned[paths.layer(f.node, f.field)])}
              onClose={() => { setAdding(false); }}
              onPick={(f) => { setTuned({ ...tuned, [paths.layer(f.node, f.field)]: startSpec(f.schema, f.value) }); setAdding(false); }}
            />
          ) : null}
        </div>
        <aside className="card check" aria-label={mode === "network" ? "VRAM check" : "Where it runs"}>
          {mode === "network" ? (
            <>
              <header><h2>VRAM check</h2><span className="req">NFR-1</span>{isStudy ? <span className="note">worst case in the space</span> : null}</header>
              <div className="b">
                {check ? (
                  <>
                    <div className="vram">
                      <div className="vbar" aria-hidden="true">
                        <i className="fix" style={pct(check.fixed)} />
                        <i className="act" style={pct(check.activations)} />
                        <i className="ctx" style={pct(check.context)} />
                        <span className="cap" style={{ left: `${String((100 * check.cap) / check.card)}%` }} />
                      </div>
                      <div className="vscale"><span>0</span><span style={{ marginLeft: "auto", marginRight: "9%" }}>cap {bytes(check.cap)}</span><span>{bytes(check.card)}</span></div>
                    </div>
                    <div className="kv2">
                      <span>Weights, gradients, optimizer</span><span>{bytes(check.fixed)}</span>
                      <span>Activations × {String(worstBatch)}{batchSpec ? " (largest batch)" : " (estimate)"}</span><span>{bytes(check.activations)}</span>
                      <span>CUDA context</span><span>{bytes(check.context)}</span>
                      <span className="tot">{isStudy ? "Worst case" : "Total"}</span><span className="tot">{bytes(check.total)}</span>
                      {nextCheck && nextBatch !== undefined ? (<><span>Next largest, batch {String(nextBatch)}</span><span>{bytes(nextCheck.total)}</span></>) : null}
                    </div>
                    {check.fits ? (
                      <div className="callout ok" role="status"><Icon name="check" /><span>{isStudy ? "Every configuration in the space fits under the cap." : "Fits under the cap."}</span></div>
                    ) : isStudy ? (
                      <>
                        <div className="callout warn" role="status">
                          <Icon name="alert" />
                          <span><b>{batchSpec ? `Batch ${String(worstBatch)}` : "The widest configuration"} is over the {bytes(check.cap)} cap by the estimate.</b> Trials that draw it fail as <span className="mono">over_vram</span> without training, and count towards the {String(limitsNow.n_trials)}.{nextCheck?.fits ? " Every other value fits." : ""}</span>
                        </div>
                        {batchSpec?.scale === "choices" && choices.length > 2 ? (
                          <Button size="sm" variant="primary" onClick={() => { setTuned({ ...tuned, [paths.train("batch_size")]: { ...batchSpec, choices: (batchSpec.choices ?? []).filter((c) => c !== worstBatch) } }); }}>Drop {String(worstBatch)} from batch_size</Button>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <div className="callout warn" role="status">
                          <Icon name="alert" />
                          <span><b>Over the {bytes(check.cap)} cap by the estimate.</b>{check.suggested ? ` Batch ${String(check.suggested)} fits.` : " Even a batch of 1 does not."} The estimate is rough; if the run does run out of memory, it stops cleanly and suggests a batch (RUN-1.5).</span>
                        </div>
                        {check.suggested ? (
                          <Button size="sm" variant="primary" onClick={() => { setChanged({ batch_size: batch }); setValues({ ...(values ?? {}), batch_size: check.suggested }); }}>Use batch {String(check.suggested)}</Button>
                        ) : null}
                      </>
                    )}
                  </>
                ) : (
                  <Skeleton width={300} height={60} />
                )}
                <div className="plan">
                  {isStudy ? (
                    <>Up to <b>{String(limitsNow.n_trials)} trials</b> or <b>{String(limitsNow.minutes)} min</b>, whichever comes first. Each trial is its own job on the <b>{device === "cpu" ? "CPU" : "GPU"} queue</b>, one at a time, {text(epochs)} epochs each{steps ? ` of ${count(steps)} steps` : ""}, plus about {String(TRIAL_START_S)} s to start (S-09). Only the best trial is registered; the test split is used once, on it, at the end.</>
                  ) : (
                    <>Will run <b>{text(values?.epochs)} epochs</b>{steps ? <> of <b>{count(steps)} steps</b></> : null} on the <b>{device === "cpu" ? "CPU" : "GPU"} queue</b>, checkpointing every epoch and every {String(Math.round(Number(values?.checkpoint_every_s ?? 300) / 60))} min.</>
                  )}
                </div>
                {failure ? <ErrorState error={failure} title={`The ${isStudy ? "study" : "run"} could not be started`} /> : null}
                <Button variant="primary" disabled={starting || problems.length > 0 || values === null || !version} onClick={() => { void start(); }}>
                  {check && !check.fits && !isStudy ? "Start anyway" : startWord}
                  <Icon name="play" />
                </Button>
                <span className="note">Opens {isStudy ? "the study (SCR-10)" : "the run's live view (SCR-04)"}. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> starts it.</span>
              </div>
            </>
          ) : (
            <>
              <header><h2>Where it runs</h2><span className="req">ML-1.1</span></header>
              <div className="b">
                <span>On the <b>CPU queue</b>{isStudy ? ", with every trial in one job (a trial takes seconds here; S-09)" : ""}. There is no device choice and no VRAM check for classical models.</span>
                <div className="plan">
                  {isStudy ? <>Up to <b>{String(limitsNow.n_trials)} trials</b> or <b>{String(limitsNow.minutes)} min</b>. The best configuration is refit on the train split, scored on validation, registered, then <b>tested once</b>.</> : <>Fits <b>{estimator ?? "the estimator"}</b> on the train split, scores it on validation and test, and registers it.</>}
                </div>
                {failure ? <ErrorState error={failure} title={`The ${isStudy ? "study" : "run"} could not be started`} /> : null}
                <Button variant="primary" disabled={starting || !estimator || !version} onClick={() => { void start(); }}>{startWord}<Icon name="play" /></Button>
                <span className="note">Opens {isStudy ? "the study (SCR-10)" : "the run's live view (SCR-04)"}. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> starts it.</span>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

