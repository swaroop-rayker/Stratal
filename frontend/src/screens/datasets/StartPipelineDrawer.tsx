// The Start pipeline drawer, type first (SCREENS.md v4 amendment; approved hi-fi
// SCR-02drawer_v1; DATA-1.4, DATA-5.2, DATA-7.1). Shared by SCR-02 and SCR-03. The dataset
// type comes first, then only that type's stages in their order (GET /dataset-types), each
// stage reading the one before it, so a run stops at one point (until_stage, open point 7a).
// transform is reachable through SCR-15 (7c). POST /jobs always names dataset_type.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Tabs from "@radix-ui/react-tabs";
import { Fragment, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, api, call, type Problem } from "../../api/client";
import { useDatasetTypes } from "../../api/builder";
import { useDatasets, useSources, useStageDefaults, useStageSchema, useVersions } from "../../api/queries";
import { Drawer, DrawerClose } from "../../components/Drawer";
import { SchemaForm, show } from "../../components/SchemaForm";
import { Badge, Button, Icon } from "../../components/ui";
import { count } from "../../lib/format";

type Values = Record<string, unknown>;
/** Where the drawer starts: a folder, or a version; `dataset` is the dataset in view, whose
 * versions the "An existing version" tab offers. */
export interface StartFrom {
  mode: "folder" | "version";
  dataset?: string;
  versionId?: string;
}

const SLUG = /^[a-z0-9][a-z0-9_-]{1,62}$/;

/** One line on what each stage does, in the drawer's stage rows. */
const WHAT: Record<string, string> = {
  clean: "exact and near duplicates",
  preprocess: "a resized copy",
  label: "captions and CLIP scores",
  filter: "drops captions with a low CLIP score",
  split: "train / val / test",
  evaluate: "the phase-1 report",
  transform: "your operations on the raw table",
  clean_table: "marks exact duplicate records",
  clean_text: "normalises texts; marks duplicates",
  tokenize: "words to IDs, the vocabulary from train",
  chunk: "at headings, then by size",
  index: "embed, build the index",
};
const INGEST_WHAT: Record<string, string> = {
  image_caption: "reads the images",
  labelled_images: "reads the images and their labels",
  tabular: "reads the table; each record becomes a row",
  labelled_text: "reads the texts and their labels",
  text_corpus: "one row per document",
  instruction_pairs: "one row per pair",
};
/** The one setting a stage row shows (the mockup's "min_size 256"). */
const KEY_FIELD: Record<string, [string, string]> = {
  clean: ["phash_max_distance", "pHash distance"],
  preprocess: ["max_side", "max_side"],
  label: ["caption_batch_size", "batch"],
  filter: ["clip_threshold", "threshold"],
  split: ["seed", "seed"],
  evaluate: ["confidence", "confidence"],
  clean_text: ["max_chars", "max_chars"],
  tokenize: ["vocab_size", "vocab_size"],
  chunk: ["chunk_tokens", "chunk_tokens"],
};
/** The ingest fields each type uses; the others are not shown (their defaults apply). */
const INGEST_FIELDS: Record<string, string[]> = {
  image_caption: ["min_size"],
  labelled_images: ["min_size", "label_from"],
  tabular: [],
  labelled_text: ["text"],
  text_corpus: [],
  instruction_pairs: [],
};

function changed(values: Values, defaults: Values): Values {
  const out: Values = {};
  for (const [key, value] of Object.entries(values)) {
    if (JSON.stringify(value) !== JSON.stringify(defaults[key])) out[key] = value;
  }
  return out;
}

/** The POST /jobs body: a folder or a version, its dataset type, only the settings that
 * differ from the defaults, and `until_stage` only when the run stops before the last
 * stage (SCREENS.md open point 7a). */
export function pipelineBody({ from, dtype, chain, stop, values, defaults }: {
  from: { dataset: string; path: string } | { versionId: string };
  dtype: string;
  /** The stages this request runs, in order, optional ones left out. */
  chain: string[];
  stop: string;
  values: Record<string, Values>;
  defaults: Record<string, Values | null>;
}) {
  const ran = chain.slice(0, chain.indexOf(stop) + 1);
  const stages: Record<string, Values> = {};
  for (const name of ran) {
    const diff = changed(values[name] ?? {}, defaults[name] ?? {});
    if (Object.keys(diff).length) stages[name] = diff;
  }
  return {
    kind: "pipeline" as const,
    ...("versionId" in from
      ? { from_version: from.versionId }
      : { dataset: from.dataset, source: { kind: "folder" as const, path: from.path } }),
    dataset_type: dtype,
    stages,
    until_stage: stop === chain[chain.length - 1] ? null : stop,
  };
}

/** A stage's schema with only the given properties, for the type's own ingest fields. */
function only(schema: Record<string, unknown>, keep: string[] | null): Record<string, unknown> {
  if (keep === null) return schema;
  const props = (schema["properties"] ?? {}) as Record<string, unknown>;
  return {
    ...schema,
    properties: Object.fromEntries(Object.entries(props).filter(([k]) => keep.includes(k))),
    required: ((schema["required"] ?? []) as string[]).filter((k) => keep.includes(k)),
  };
}

function StageFields({ name, values, keep, onChange, errors }: {
  name: string;
  values: Values;
  keep: string[] | null;
  onChange: (values: Values, valid: boolean) => void;
  errors: Record<string, string>;
}) {
  const schema = useStageSchema(name, true);
  if (!schema.data) return <div className="st-form"><span className="skeleton" style={{ width: 160 }} /></div>;
  return (
    <div className="st-form">
      <SchemaForm schema={only(schema.data, keep)} values={values} onChange={onChange} serverErrors={errors} />
    </div>
  );
}

/** A table's ingest fields: the target from the folder's header, and kinds set by hand. */
function TableFields({ columns, table, onChange, error }: {
  columns: string[] | null;
  table: { target?: string; kinds?: Record<string, string> };
  onChange: (table: { target?: string; kinds?: Record<string, string> }) => void;
  error?: string;
}) {
  const kinds = table.kinds ?? {};
  const [adding, setAdding] = useState("");
  return (
    <div className="st-form one">
      <div className={error ? "field error" : "field"}>
        <label htmlFor="sp-target">
          <span className="mono">table.target</span><span className="type">required</span>
        </label>
        {columns ? (
          <select id="sp-target" className="select mono" value={table.target ?? ""} onChange={(e) => { onChange({ ...table, target: e.target.value }); }}>
            <option value="" disabled>Choose the column to predict</option>
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <input id="sp-target" className="input mono" value={table.target ?? ""} onChange={(e) => { onChange({ ...table, target: e.target.value }); }} />
        )}
        <small>{error ?? "The column to predict, from the table's header. Categorical: classification; numeric: regression."}</small>
      </div>
      <div className="field">
        <label htmlFor="sp-kind-col"><span className="mono">table.kinds</span><span className="type">optional</span></label>
        <div className="sp-kinds">
          {Object.entries(kinds).map(([col, kind]) => (
            <span key={col} className="tag">
              {col} · {kind}
              <button type="button" aria-label={`Infer the kind of ${col}`} onClick={() => { onChange({ ...table, kinds: Object.fromEntries(Object.entries(kinds).filter(([k]) => k !== col)) }); }}><Icon name="x" /></button>
            </span>
          ))}
          {columns ? (
            <>
              <select id="sp-kind-col" className="select mono" value={adding} onChange={(e) => { setAdding(e.target.value); }}>
                <option value="">Set a column&apos;s kind…</option>
                {columns.filter((c) => !(c in kinds) && c !== table.target).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              {adding ? (
                <>
                  <Button size="sm" onClick={() => { onChange({ ...table, kinds: { ...kinds, [adding]: "categorical" } }); setAdding(""); }}>categorical</Button>
                  <Button size="sm" onClick={() => { onChange({ ...table, kinds: { ...kinds, [adding]: "numeric" } }); setAdding(""); }}>numeric</Button>
                </>
              ) : null}
            </>
          ) : null}
        </div>
        <small>Otherwise inferred: numeric if every value is a number. Set one here to keep a code like <span className="mono">007</span> as text.</small>
      </div>
    </div>
  );
}

/** Field errors from a problem's errors[].loc, by stage then field. */
function fieldErrors(problem: Problem | null): { stages: Record<string, Record<string, string>>; other: Record<string, string> } {
  const stages: Record<string, Record<string, string>> = {};
  const other: Record<string, string> = {};
  for (const error of problem?.errors ?? []) {
    const loc = error.loc.filter((part) => part !== "body").map(String);
    if (loc[0] === "stages" && loc[1]) {
      (stages[loc[1]] ??= {})[loc[loc.length - 1] ?? loc[1]] = error.msg;
    } else if (loc[0]) {
      other[loc[0]] = error.msg;
    }
  }
  return { stages, other };
}

export function StartPipelineDrawer({ open, onOpenChange, from }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  from: StartFrom;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [mode, setMode] = useState<"folder" | "version">(from.mode);
  const [source, setSource] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [versionId, setVersionId] = useState(from.versionId ?? "");
  const [chosenType, setChosenType] = useState<string | null>(null);
  const [typeFolded, setTypeFolded] = useState(false);
  const [stop, setStop] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["ingest"]));
  const [values, setValues] = useState<Record<string, Values>>({});
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [cleared, setCleared] = useState<{ names: string[]; kept: string[]; before: Record<string, Values>; type: string } | null>(null);

  const sources = useSources(open && mode === "folder");
  const datasets = useDatasets();
  const versionsOf = from.dataset;
  const versions = useVersions(open && mode === "version" ? versionsOf : undefined);
  const types = useDatasetTypes();
  const version = versions.data?.items.find((v) => v.version_id === versionId);
  const dtype = mode === "version" ? (version ? (version.dtype ?? "image_caption") : null) : chosenType;
  // The type's own defaults (a corpus's max_chars), as the job will resolve them.
  const defaults = useStageDefaults(open, dtype);
  const typeInfo = types.data?.find((t) => t.name === dtype);
  const stages = useMemo(() => typeInfo?.stages ?? [], [typeInfo]);
  const after = mode === "version" && version ? stages.slice(stages.findIndex((s) => s.name === version.stage) + 1) : stages;
  const chain = after.filter((s) => !s.optional).map((s) => s.name);
  const last = stop !== null && chain.includes(stop) ? stop : chain[chain.length - 1];
  const folder = sources.data?.items.find((s) => s.path === source);
  const allDefaults = (defaults.data ?? {}) as Record<string, Values | null>;

  const start = useMutation({
    mutationFn: () => {
      const body = pipelineBody({
        from: mode === "folder" ? { dataset: slug, path: source } : { versionId },
        dtype: dtype ?? "image_caption",
        chain,
        stop: last ?? "",
        values,
        defaults: allDefaults,
      });
      return call(() => api.POST("/api/v1/jobs", { body: body as never }));
    },
    onSuccess: (job) => {
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["datasets"] });
      onOpenChange(false);
      void navigate(`/jobs/${(job as { id: string }).id}`);
    },
  });

  const problem = start.error instanceof ApiError ? start.error.problem : null;
  const errors = fieldErrors(problem);
  const exists = datasets.data?.items.some((d) => d.slug === slug) ?? false;
  const slugOk = SLUG.test(slug);
  const tableTarget = ((values["ingest"]?.["table"] ?? {}) as { target?: string }).target;
  const needsTarget = mode === "folder" && dtype === "tabular" && !tableTarget;
  const ready =
    dtype !== null &&
    chain.length > 0 &&
    !needsTarget &&
    (mode === "folder" ? source !== "" && slugOk : versionId !== "") &&
    invalid.size === 0;
  const why = dtype === null ? "Choose what the folder holds" : chain.length === 0 ? "Nothing left to run" : needsTarget ? "Choose the table's target" : null;

  const pickType = (next: string) => {
    if (chosenType && chosenType !== next) {
      const nextStages = new Set((types.data?.find((t) => t.name === next)?.stages ?? []).map((s) => s.name));
      const names = Object.keys(values).filter((n) => !nextStages.has(n) && Object.keys(values[n] ?? {}).length);
      const kept = Object.keys(values).filter((n) => nextStages.has(n) && n !== "ingest");
      if (names.length || values["ingest"]) setCleared({ names, kept, before: values, type: chosenType });
      const nextValues: Record<string, Values> = {};
      for (const n of kept) nextValues[n] = values[n] ?? {};
      setValues(nextValues);
    }
    setChosenType(next);
    setTypeFolded(true);
    setStop(null);
    start.reset();
  };

  const pickSource = (path: string) => {
    setSource(path);
    if (!slugTouched) setSlug(path.split("/").pop()?.toLowerCase().replace(/[^a-z0-9_-]/g, "-") ?? "");
  };

  const ingestRow = (name: string) => {
    if (name !== "ingest") return null;
    if (dtype === "tabular") {
      const table = (values["ingest"]?.["table"] ?? {}) as { target?: string; kinds?: Record<string, string> };
      return (
        <TableFields
          columns={folder?.columns ?? null}
          table={table}
          error={errors.stages["ingest"]?.["target"] ?? errors.stages["ingest"]?.["table"]}
          onChange={(next) => { setValues((all) => ({ ...all, ingest: { ...(all["ingest"] ?? {}), table: next } })); }}
        />
      );
    }
    return null;
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Start pipeline"
      width={480}
      footer={
        <>
          <DrawerClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DrawerClose>
          {why ? <span className="sr-only" id="sp-why">{why}</span> : null}
          <Button
            variant="primary"
            aria-disabled={!ready || start.isPending || undefined}
            aria-describedby={why ? "sp-why" : undefined}
            className={!ready ? "is-disabled" : undefined}
            onClick={() => { if (ready && !start.isPending) start.mutate(); }}
            style={{ minWidth: 104 }}
          >
            {start.isPending ? (
              <>
                <span className="spinner" />
                Starting…
              </>
            ) : last && ready ? (
              `Start job · to ${last}`
            ) : (
              "Start job"
            )}
          </Button>
        </>
      }
    >
      <Tabs.Root
        value={mode}
        onValueChange={(value) => {
          setMode(value as "folder" | "version");
          setStop(null);
          start.reset();
        }}
      >
        <Tabs.List className="tabs" aria-label="Start from">
          <Tabs.Trigger className="tab" value="folder">
            <Icon name="folder" />A folder
          </Tabs.Trigger>
          <Tabs.Trigger className="tab" value="version" disabled={!versionsOf}>
            <Icon name="branch" />
            An existing version
          </Tabs.Trigger>
        </Tabs.List>
      </Tabs.Root>

      {mode === "folder" ? (
        <>
          <div className={errors.other.source ? "field error" : "field"}>
            <label htmlFor="sp-source">
              Source folder <span className="type">source.path</span>
            </label>
            <select id="sp-source" className="select mono" value={source} onChange={(e) => { pickSource(e.target.value); }} aria-describedby="sp-source-help">
              <option value="" disabled>
                {sources.isPending ? "Loading folders…" : "Choose a folder"}
              </option>
              {sources.data?.items.map((s) => (
                <option key={s.path} value={s.path}>
                  {s.path} · {count(s.files)} files
                </option>
              ))}
            </select>
            <small id="sp-source-help">
              {errors.other.source ?? (folder ? `${count(folder.files)} files${folder.columns ? ` · a table with ${String(folder.columns.length)} columns` : ""}` : <>Put new folders under <span className="mono">.\sources</span>.</>)}
            </small>
          </div>
          {typeFolded && chosenType ? (
            <div className="field">
              <label>What the folder holds <span className="type">dataset_type</span></label>
              <div className="sp-chosen">
                <b>{typeInfo?.title ?? chosenType}</b>
                <span>{typeInfo?.source}</span>
                <span className="spacer" />
                <Button size="sm" variant="ghost" onClick={() => { setTypeFolded(false); }}>Change</Button>
              </div>
            </div>
          ) : (
            <fieldset className="sp-kinds-set">
              <legend>What the folder holds <span className="type">dataset_type</span></legend>
              {(types.data ?? []).map((t) => (
                <label key={t.name} className="kc">
                  <input type="radio" name="sp-type" checked={chosenType === t.name} onChange={() => { pickType(t.name); }} />
                  <b>{t.title}</b>
                  <span className="what">{t.source}</span>
                </label>
              ))}
              {errors.other.dataset_type ? <small className="err">{errors.other.dataset_type}</small> : null}
            </fieldset>
          )}
          {chosenType ? (
            <div className={(slug && !slugOk) || errors.other.dataset ? "field error" : "field"}>
              <label htmlFor="sp-dataset">
                Dataset <span className="type">dataset</span>
              </label>
              <input
                id="sp-dataset"
                className="input mono"
                value={slug}
                onChange={(e) => {
                  setSlug(e.target.value);
                  setSlugTouched(true);
                }}
                aria-describedby="sp-dataset-help"
                aria-invalid={slug !== "" && !slugOk}
              />
              <small id="sp-dataset-help">
                {errors.other.dataset ??
                  (slug && !slugOk
                    ? "2–63 lowercase letters, digits, - or _, starting with a letter or digit."
                    : exists
                      ? "An existing dataset: this adds versions to it."
                      : "New: it will be created. 2–63 lowercase letters, digits, - or _.")}
              </small>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <div className={errors.other.from_version ? "field error" : "field"}>
            <label htmlFor="sp-version">
              Version <span className="type">from_version</span>
            </label>
            <select id="sp-version" className="select mono" value={versionId} onChange={(e) => { setVersionId(e.target.value); setStop(null); }}>
              <option value="" disabled>Choose a version</option>
              {versions.data?.items.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  {v.version_id} · {v.stage} · {v.dataset}
                </option>
              ))}
            </select>
            {errors.other.from_version ? <small>{errors.other.from_version}</small> : null}
          </div>
          {version && typeInfo ? (
            <div className="field">
              <label>What it holds</label>
              <div className="kc" aria-disabled="true">
                <b>{typeInfo.title}</b>
                <span className="what">the version&apos;s own type</span>
              </div>
            </div>
          ) : null}
        </>
      )}

      {cleared ? (
        <div className="sp-note" role="status">
          <Icon name="info" />
          <span>
            Type changed to <b>{typeInfo?.title ?? chosenType}</b>.
            {cleared.names.length ? <> Settings for {cleared.names.map((n) => <span key={n} className="mono"> {n}</span>)} were cleared.</> : null}
            {cleared.kept.length ? <> {cleared.kept.join(", ")}&apos;s were kept.</> : null}
          </span>
          <Button size="sm" variant="ghost" onClick={() => { setValues(cleared.before); setChosenType(cleared.type); setCleared(null); }}>Undo</Button>
        </div>
      ) : null}

      {dtype === null ? (
        <div className="sp-wait">Choose what the folder holds. The dataset name and the stages follow from it.</div>
      ) : mode === "version" && version && chain.length === 0 ? (
        <div className="sp-note"><Icon name="info" /><span><span className="mono">{version.version_id}</span> is a {version.stage}, the last {typeInfo?.title.toLowerCase() ?? ""} stage. There is nothing after it to run.</span></div>
      ) : (
        <div className="field">
          <label id="sp-stages">
            Stages <span className="sp-why-chain">· each stage reads the one before it, so a run stops at one point</span>
          </label>
          <div className="stages" role="radiogroup" aria-labelledby="sp-stages">
            {mode === "version" && version ? (
              <div className="sp-st done">
                <Icon name="check" />
                <span className="name">{stages.slice(0, stages.findIndex((s) => s.name === version.stage) + 1).map((s) => s.name).join(" · ")}</span>
                <span className="what">already done in this version</span>
              </div>
            ) : null}
            {after.map((s) => {
              const name = s.name;
              if (s.optional) {
                const fromRaw = mode === "version" && version?.stage === "ingest";
                return (
                  <div key={name} className="sp-st">
                    <span />
                    <span className="name">{name}</span>
                    <span className="opt">optional</span>
                    <span className="what">
                      {fromRaw
                        ? "your operations on this raw table: switching on opens the transform editor"
                        : "runs on the raw version once it exists: stop after ingest, then Transform…"}
                    </span>
                    {fromRaw ? (
                      <label className="toggle tg">
                        <input
                          type="checkbox"
                          aria-label="transform: open the transform editor"
                          onChange={(e) => {
                            if (e.target.checked) {
                              onOpenChange(false);
                              void navigate(`/versions/${versionId}/transform`);
                            }
                          }}
                        />
                        <i />
                      </label>
                    ) : (
                      <span className="tg"><Button size="sm" onClick={() => { setStop("ingest"); }}>Stop after ingest</Button></span>
                    )}
                  </div>
                );
              }
              const notRun = last !== undefined && chain.indexOf(name) > chain.indexOf(last);
              const stageDefaults = allDefaults[name] ?? {};
              const current = { ...stageDefaults, ...(values[name] ?? {}) };
              const [field, label] = KEY_FIELD[name] ?? ["", ""];
              const value = field ? current[field] : undefined;
              const hasError = Boolean(errors.stages[name]) || invalid.has(name);
              const what = name === "ingest" ? INGEST_WHAT[dtype] ?? "reads the source" : WHAT[name] ?? "";
              // Ingest of a type with no ingest fields, clean_table and index have nothing to set;
              // index's embedding model is one model (M12 decision 4), shown fixed, not editable.
              const hasFields =
                name === "ingest" ? dtype === "tabular" || (INGEST_FIELDS[dtype]?.length ?? 1) > 0 : name !== "clean_table" && name !== "index";
              const isOpen = expanded.has(name) && !notRun && hasFields;
              const model = name === "index" ? stageDefaults.embedding_model : undefined;
              const revision = name === "index" ? stageDefaults.embedding_revision : undefined;
              return (
                <Fragment key={name}>
                  <div className={`sp-st${notRun ? " notrun" : ""}${name === last ? " sel" : ""}`}>
                    <button
                      type="button"
                      className="expand"
                      disabled={notRun || !hasFields}
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Hide" : "Show"} ${name} settings`}
                      onClick={() => {
                        setExpanded((set) => {
                          const next = new Set(set);
                          if (next.has(name)) next.delete(name);
                          else next.add(name);
                          return next;
                        });
                      }}
                    >
                      <Icon name={isOpen ? "down" : "chevron"} />
                    </button>
                    <span className="name">{name}</span>
                    {hasError ? <Badge tone="err">fix</Badge> : null}
                    <span className="what">
                      {notRun ? "not run · " : ""}
                      {what}
                      {value !== undefined && !notRun ? <> · {label} <span className="mono">{show(value)}</span></> : null}
                      {typeof model === "string" && !notRun ? (
                        <>
                          {" · embedding "}
                          <span className="mono">
                            {model}
                            {typeof revision === "string" ? ` · ${revision.slice(0, 7)}` : ""}
                          </span>{" "}
                          fixed
                        </>
                      ) : null}
                    </span>
                    <label className="stop">
                      <input type="radio" name="sp-stop" checked={name === last} onChange={() => { setStop(name); }} aria-label={`Stop after ${name}`} />
                      Stop after
                    </label>
                  </div>
                  {isOpen ? (
                    name === "ingest" && dtype === "tabular" ? (
                      ingestRow(name)
                    ) : (
                      <StageFields
                        name={name}
                        values={current}
                        keep={name === "ingest" ? (INGEST_FIELDS[dtype] ?? null) : null}
                        errors={errors.stages[name] ?? {}}
                        onChange={(next, valid) => {
                          setValues((all) => ({ ...all, [name]: next }));
                          setInvalid((set) => {
                            const out = new Set(set);
                            if (valid) out.delete(name);
                            else out.add(name);
                            return out;
                          });
                        }}
                      />
                    )
                  ) : null}
                </Fragment>
              );
            })}
          </div>
          <small>Fields come from each stage&apos;s config schema, only those of this type; only changed values are sent.</small>
        </div>
      )}

      {problem ? (
        <div className="summary" role="alert">
          <Icon name="alert" />
          <div>
            <b>{problem.errors.length ? "A setting did not pass validation." : problem.title}</b>{" "}
            {problem.errors.length ? "Fix the field marked above." : problem.detail}{" "}
            {problem.suggested_action && !problem.errors.length ? problem.suggested_action : null}
            <Badge tone="err">{problem.code}</Badge>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}
