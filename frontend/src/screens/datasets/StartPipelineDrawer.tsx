// The Start pipeline drawer, shared by SCR-02 and SCR-03 (SCREENS.md §3, mockup SCR-02
// frames F and G). POST /jobs with a folder or a version, the last stage, and only the
// settings that differ from the defaults; then opens SCR-04 for the new job.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Tabs from "@radix-ui/react-tabs";
import { Fragment, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError, api, call, type Problem } from "../../api/client";
import {
  useDatasets,
  useSources,
  useStageDefaults,
  useStageSchema,
  useStageSchemas,
  useVersions,
} from "../../api/queries";
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

/** The one setting a stage row shows when folded (the mockup's "min_size 256"). */
const KEY_FIELD: Record<string, [string, string]> = {
  ingest: ["min_size", "min_size"],
  clean: ["phash_max_distance", "max distance"],
  preprocess: ["max_side", "max_side"],
  label: ["caption_batch_size", "batch"],
  filter: ["clip_threshold", "threshold"],
  split: ["seed", "seed"],
  evaluate: ["confidence", "confidence"],
};

function changed(values: Values, defaults: Values): Values {
  const out: Values = {};
  for (const [key, value] of Object.entries(values)) {
    if (JSON.stringify(value) !== JSON.stringify(defaults[key])) out[key] = value;
  }
  return out;
}

/** The POST /jobs body: a folder or a version, only the settings that differ from the
 * defaults, and `until_stage` only when the run stops before the last stage. */
export function pipelineBody({ from, ticked, runnable, values, defaults }: {
  from: { dataset: string; path: string } | { versionId: string };
  ticked: string[];
  runnable: string[];
  values: Record<string, Values>;
  defaults: Record<string, Values | null>;
}) {
  const stages: Record<string, Values> = {};
  for (const name of ticked) {
    const diff = changed(values[name] ?? {}, defaults[name] ?? {});
    if (Object.keys(diff).length) stages[name] = diff;
  }
  const last = ticked[ticked.length - 1];
  return {
    kind: "pipeline" as const,
    ...("versionId" in from
      ? { from_version: from.versionId }
      : { dataset: from.dataset, source: { kind: "folder" as const, path: from.path } }),
    stages,
    until_stage: last === undefined || last === runnable[runnable.length - 1] ? null : last,
  };
}

function StageFields({ name, values, onChange, errors }: {
  name: string;
  values: Values;
  onChange: (values: Values, valid: boolean) => void;
  errors: Record<string, string>;
}) {
  const schema = useStageSchema(name, true);
  if (!schema.data) return <div className="st-form"><span className="skeleton" style={{ width: 160 }} /></div>;
  return (
    <div className="st-form">
      <SchemaForm schema={schema.data} values={values} onChange={onChange} serverErrors={errors} />
    </div>
  );
}

/** Field errors from a problem's errors[].loc, by stage then field. */
function fieldErrors(problem: Problem | null): { stages: Record<string, Record<string, string>>; other: Record<string, string> } {
  const stages: Record<string, Record<string, string>> = {};
  const other: Record<string, string> = {};
  for (const error of problem?.errors ?? []) {
    const loc = error.loc.filter((part) => part !== "body").map(String);
    if (loc[0] === "stages" && loc[1] && loc[2]) {
      (stages[loc[1]] ??= {})[loc[2]] = error.msg;
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
  const [until, setUntil] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [values, setValues] = useState<Record<string, Values>>({});
  const [invalid, setInvalid] = useState<Set<string>>(new Set());

  const sources = useSources(open && mode === "folder");
  const datasets = useDatasets();
  const versionsOf = from.dataset;
  const versions = useVersions(open && mode === "version" ? versionsOf : undefined);
  const schemas = useStageSchemas(open);
  const defaults = useStageDefaults(open);

  const allStages = useMemo(() => (schemas.data?.items ?? []).map((s) => s.name), [schemas.data]);
  const version = versions.data?.items.find((v) => v.version_id === versionId);
  const done = mode === "version" && version ? allStages.slice(0, allStages.indexOf(version.stage) + 1) : [];
  const runnable = mode === "version" ? allStages.filter((s) => !done.includes(s)) : allStages;
  const last = until !== null && runnable.includes(until) ? until : runnable[runnable.length - 1];
  const ticked = last === undefined ? [] : runnable.slice(0, runnable.indexOf(last) + 1);

  const start = useMutation({
    mutationFn: () => {
      const body = pipelineBody({
        from: mode === "folder" ? { dataset: slug, path: source } : { versionId },
        ticked,
        runnable,
        values,
        defaults: (defaults.data ?? {}) as Record<string, Values | null>,
      });
      return call(() => api.POST("/api/v1/jobs", { body }));
    },
    onSuccess: (job) => {
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["datasets"] });
      onOpenChange(false);
      void navigate(`/jobs/${job.id}`);
    },
  });

  const problem = start.error instanceof ApiError ? start.error.problem : null;
  const errors = fieldErrors(problem);
  const exists = datasets.data?.items.some((d) => d.slug === slug) ?? false;
  const slugOk = SLUG.test(slug);
  const ready =
    (mode === "folder" ? source !== "" && slugOk : versionId !== "") && ticked.length > 0 && invalid.size === 0;

  const toggle = (name: string, on: boolean) => {
    const i = runnable.indexOf(name);
    setUntil(on ? name : (runnable[i - 1] ?? name));
  };

  const pickSource = (path: string) => {
    setSource(path);
    if (!slugTouched) setSlug(path.split("/").pop()?.toLowerCase().replace(/[^a-z0-9_-]/g, "-") ?? "");
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Start pipeline"
      footer={
        <>
          <DrawerClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DrawerClose>
          <Button
            variant="primary"
            disabled={!ready || start.isPending}
            onClick={() => {
              start.mutate();
            }}
            style={{ minWidth: 104 }}
          >
            {start.isPending ? (
              <>
                <span className="spinner" />
                Starting…
              </>
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
          setUntil(null);
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
            <select
              id="sp-source"
              className="select mono"
              value={source}
              onChange={(e) => {
                pickSource(e.target.value);
              }}
              aria-describedby="sp-source-help"
            >
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
              {errors.other.source ?? (
                <>
                  {source
                    ? `${count(sources.data?.items.find((s) => s.path === source)?.files)} files · `
                    : ""}
                  from <span className="mono">GET /sources</span>. Put new folders under{" "}
                  <span className="mono">.\sources</span>.
                </>
              )}
            </small>
          </div>
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
                    ? "Exists: the new versions are added to it."
                    : "New: it will be created. 2–63 lowercase letters, digits, - or _.")}
            </small>
          </div>
        </>
      ) : (
        <div className={errors.other.from_version ? "field error" : "field"}>
          <label htmlFor="sp-version">
            Version <span className="type">from_version</span>
          </label>
          <select
            id="sp-version"
            className="select mono"
            value={versionId}
            onChange={(e) => {
              setVersionId(e.target.value);
              setUntil(null);
            }}
            aria-describedby="sp-version-help"
          >
            <option value="" disabled>
              Choose a version
            </option>
            {versions.data?.items.map((v) => (
              <option key={v.version_id} value={v.version_id}>
                {v.version_id} · {v.stage} · {v.dataset}
              </option>
            ))}
          </select>
          <small id="sp-version-help">
            {errors.other.from_version ??
              (version ? (
                runnable.length ? (
                  <>
                    The stages after <span className="mono">{version.stage}</span> run. The dataset
                    is the version&apos;s own.
                  </>
                ) : (
                  "This version was made by the last stage: nothing follows it."
                )
              ) : (
                "The stages after the one that made it run."
              ))}
          </small>
        </div>
      )}

      <div className="field">
        <label>Stages</label>
        <div className="stages">
          {done.length ? (
            <div className="st off">
              <input type="checkbox" disabled checked aria-label="Already in this version" />
              <span className="name">{done.join(" · ")}</span>
              <span className="key">already in this version</span>
            </div>
          ) : null}
          {runnable.map((name, i) => {
            const on = ticked.includes(name);
            const open = expanded.has(name) && on;
            const stageDefaults = ((defaults.data ?? {}) as Record<string, Values | null>)[name] ?? {};
            const current = { ...stageDefaults, ...(values[name] ?? {}) };
            const [field, label] = KEY_FIELD[name] ?? ["", ""];
            const value = field ? current[field] : undefined;
            const hasError = Boolean(errors.stages[name]) || invalid.has(name);
            return (
              <Fragment key={name}>
                <div className={on ? "st" : "st off"}>
                  <input
                    type="checkbox"
                    id={`sp-stage-${name}`}
                    checked={on}
                    disabled={i === 0}
                    onChange={(e) => {
                      toggle(name, e.target.checked);
                    }}
                  />
                  <label htmlFor={`sp-stage-${name}`}>
                    <span className="name">{name}</span>
                  </label>
                  {hasError ? <Badge tone="err">fix</Badge> : null}
                  <span className="key">{value === undefined ? "" : `${label} ${show(value)}`}</span>
                  <button
                    type="button"
                    className="expand"
                    disabled={!on}
                    aria-expanded={open}
                    aria-label={`${open ? "Hide" : "Show"} ${name} settings`}
                    onClick={() => {
                      setExpanded((set) => {
                        const next = new Set(set);
                        if (next.has(name)) next.delete(name);
                        else next.add(name);
                        return next;
                      });
                    }}
                  >
                    <Icon name={open ? "down" : "chevron"} />
                  </button>
                </div>
                {open ? (
                  <StageFields
                    name={name}
                    values={current}
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
                ) : null}
              </Fragment>
            );
          })}
        </div>
        <small>
          Untick the later stages to stop early. Fields come from each stage&apos;s config schema;
          only changed values are sent.
        </small>
      </div>

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
