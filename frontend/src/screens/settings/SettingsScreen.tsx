// SCR-14: defaults and limits stored in the database (OC-9), one save per section
// (mockup docs/design/screens/SCR-14_v1.html, G2 approved 2026-11-18). Fields come from each
// section's JSON schema (UI-2.3's pattern): name and type as the label, description as the
// helper, x-applies as a badge. Leaving with unsaved changes asks first.
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useLocation } from "react-router";

import { ApiError } from "../../api/client";
import { useGpuTemperature } from "../../api/queries";
import {
  type Keep,
  keepText,
  planOf,
  type RetentionPlan,
  type SettingsSection,
  useRegistryLive,
  useSaveSettings,
  useSettings,
} from "../../api/registry";
import { Modal } from "../../components/Modal";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { bytes } from "../../lib/format";
import { useCrumbs, useTheme } from "../../shell/hooks";
import { ConfirmDeletion } from "../models/parts";

type Values = Record<string, unknown>;
interface Prop {
  type?: string;
  minimum?: number;
  maximum?: number;
  default?: unknown;
  description?: string;
  "x-applies"?: string;
}

const RESUME_GAP = 5;

/** A setting's value as text: numbers and words as they are, anything else as JSON. */
export function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return value === undefined ? "" : JSON.stringify(value);
}

/** ``record`` without ``key``: a copy, so state stays immutable. */
export function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  return Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));
}

/** Client-side checks, from the schema's ranges plus SCR-14's cross-field rules. The API
 * checks again; its 422 replaces these. */
export function fieldErrors(section: "training" | "retention" | "monitoring", values: Values, schema: { properties?: Record<string, Prop> }, slowdown: number | null): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [name, prop] of Object.entries(schema.properties ?? {})) {
    const value = values[name];
    if (prop.type !== "integer" && prop.type !== "number") continue;
    if (typeof value !== "number" || (prop.type === "integer" && !Number.isInteger(value))) {
      errors[name] = prop.type === "integer" ? "A whole number is needed." : "A number is needed.";
      continue;
    }
    if (prop.minimum !== undefined && value < prop.minimum) errors[name] = `At least ${String(prop.minimum)}.`;
    if (prop.maximum !== undefined && value > prop.maximum) errors[name] = `At most ${String(prop.maximum)}.`;
  }
  if (section === "training") {
    const pause = values["thermal_pause_c"];
    const resume = values["thermal_resume_c"];
    if (typeof pause === "number" && slowdown !== null && pause > slowdown && !errors["thermal_pause_c"]) {
      errors["thermal_pause_c"] = `At most ${String(slowdown)}: the card's slowdown point.`;
    }
    if (typeof pause === "number" && typeof resume === "number" && resume > pause - RESUME_GAP && !errors["thermal_resume_c"]) {
      errors["thermal_resume_c"] = `Must be at most ${String(pause - RESUME_GAP)}: ${String(RESUME_GAP)} °C below thermal_pause_c (${String(pause)}).`;
    }
  }
  if (section === "monitoring") {
    const active = values["sample_interval_active_s"];
    const idle = values["sample_interval_idle_s"];
    if (typeof active === "number" && typeof idle === "number" && idle < active && !errors["sample_interval_idle_s"]) {
      errors["sample_interval_idle_s"] = `At least ${String(active)}: the active interval.`;
    }
  }
  return errors;
}

/** The changed fields as "name old → new", in schema order. */
export function changes(saved: Values, draft: Values): string[] {
  return Object.keys(draft)
    .filter((k) => JSON.stringify(saved[k]) !== JSON.stringify(draft[k]))
    .map((k) => (k === "overrides" ? "overrides" : `${k} ${text(saved[k])} → ${text(draft[k])}`));
}

function typeLabel(prop: Prop, name: string): string {
  if (name === "keep_intermediate") return 'integer | "all"';
  return prop.type ?? "value";
}

function AppliesBadge({ text }: { text: string | undefined }) {
  if (!text) return null;
  const icon = text === "new jobs only" || text === "at the next cleanup" ? "clock" : "check";
  return (
    <span>
      <span className="badge">
        <Icon name={icon} />
        {text === "at once" ? "applies at once" : text}
      </span>
    </span>
  );
}

function NumberField({ name, prop, value, saved, error, onChange, children, tooltip }: {
  tooltip?: string;
  name: string;
  prop: Prop;
  value: unknown;
  saved: unknown;
  error: string | undefined;
  onChange: (value: number | string) => void;
  children?: ReactNode;
}) {
  const id = `f-${name}`;
  const helpId = `${id}-help`;
  const changed = JSON.stringify(value) !== JSON.stringify(saved);
  return (
    <div className="frow">
      <div className={error ? "field error" : "field"}>
        <label htmlFor={id}>
          <span className="mono">{name}</span>
          <span className="badge">{typeLabel(prop, name)}</span>
        </label>
        <input
          id={id}
          className="input mono num"
          inputMode="numeric"
          aria-invalid={error ? true : undefined}
          aria-describedby={helpId}
          title={tooltip}
          value={text(value ?? "")}
          onChange={(event) => {
            const text = event.target.value.trim();
            const pattern = prop.type === "number" ? /^-?\d+(\.\d+)?$/ : /^-?\d+$/;
            onChange(pattern.test(text) ? Number(text) : text);
          }}
        />
        <small id={helpId}>
          {error ? (
            <>
              <Icon name="alert" />
              {error}
            </>
          ) : (
            prop.description
          )}
        </small>
        {changed || (prop.default !== undefined && value !== prop.default) ? (
          <span className="mod">
            {changed ? <Badge tone="info">changed</Badge> : null}
            {prop.default !== undefined && value !== prop.default ? (
              <Button size="sm" variant="ghost" onClick={() => { if (typeof prop.default === "number") onChange(prop.default); }}>
                <Icon name="refresh" />
                Reset to {text(prop.default)}
              </Button>
            ) : null}
          </span>
        ) : null}
      </div>
      <div className="applies">
        <AppliesBadge text={prop["x-applies"]} />
        {children}
      </div>
    </div>
  );
}

function SaveBar({ title, list, problems, busy, onDiscard, onSave }: {
  title: string;
  list: string[];
  problems: number;
  busy: boolean;
  onDiscard: () => void;
  onSave: () => void;
}) {
  if (list.length === 0) return null;
  return (
    <div className="savebar" role="status">
      <b>
        {list.length} unsaved change{list.length === 1 ? "" : "s"}
        {problems ? ` · ${String(problems)} to fix` : ""}
      </b>
      <span className="mono">{list.join(" · ")}</span>
      <span className="spacer" />
      <Button size="sm" variant="ghost" onClick={onDiscard}>Discard</Button>
      <Button size="sm" variant="primary" disabled={busy || problems > 0} onClick={onSave}>
        Save {title.toLowerCase()}
      </Button>
    </div>
  );
}

/** Field errors from the API's 422 (errors[].loc = ["values", field]). */
function serverErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.problem.code !== "validation_error") return {};
  const out: Record<string, string> = {};
  for (const e of error.problem.errors) {
    const field = e.loc[e.loc.length - 1];
    if (typeof field === "string") out[field] = e.msg.charAt(0).toUpperCase() + e.msg.slice(1) + ".";
  }
  return out;
}

function TrainingSection({ section, draft, setDraft, onSaved, onConflict }: {
  section: SettingsSection;
  draft: Values;
  setDraft: (values: Values) => void;
  onSaved: (message: string) => void;
  onConflict: () => void;
}) {
  const save = useSaveSettings();
  const now = useGpuTemperature();
  const context = section.context;
  const slowdown = context?.gpu_slowdown_c ?? null;
  const props = (section.schema as { properties?: Record<string, Prop> }).properties ?? {};
  const local = fieldErrors("training", draft, { properties: props }, slowdown);
  const errors = { ...local, ...serverErrors(save.error) };
  const list = changes(section.values, draft);
  const running = context?.running_jobs ?? [];
  const field = (name: string, extra?: ReactNode) => (
    <NumberField
      key={name}
      tooltip={name === "thermal_pause_c" && slowdown !== null ? `The card slows itself down at ${slowdown.toFixed(0)} °C (NVML)` : undefined}
      name={name}
      prop={props[name] ?? {}}
      value={draft[name]}
      saved={section.values[name]}
      error={errors[name]}
      onChange={(value) => { save.reset(); setDraft({ ...draft, [name]: value }); }}
    >
      {extra}
    </NumberField>
  );
  return (
    <section className="sec" id="training" aria-labelledby="h-training">
      <header>
        <h2 id="h-training">Training</h2>
        <span className="req">{section.requirements.join(" · ")}</span>
      </header>
      {field("checkpoint_interval_min", <span>A job's config is fixed when it starts (NFR-4). A checkpoint is also written after every epoch.</span>)}
      {field(
        "thermal_pause_c",
        <div className="ctx" aria-live="off">
          <span><i className="sq" />now <b>{now === null ? "—" : `${now.toFixed(0)} °C`}</b></span>
          <span>
            7-day high <b>{context?.gpu_temp_high_7d ? `${context.gpu_temp_high_7d.celsius.toFixed(0)} °C` : "—"}</b>
            {context?.gpu_temp_high_7d?.job_id ? ` (${context.gpu_temp_high_7d.job_id})` : ""}
          </span>
          <span className="slowdown">slowdown <b>{slowdown === null ? "unknown" : `${slowdown.toFixed(0)} °C`}</b> (NVML)</span>
        </div>,
      )}
      {field("thermal_resume_c")}
      {field(
        "stall_timeout_s",
        running.length ? (
          <span>
            Also to {running.map((id, i) => (
              <span key={id}>{i ? ", " : ""}<Link className="mono" to={`/jobs/${id}`}>{id}</Link></span>
            ))}, which {running.length === 1 ? "is" : "are"} running.
          </span>
        ) : null,
      )}
      <div className="rules-note">
        <Icon name="info" />
        <span>Fixed by the requirements, not settings: a checkpoint after every epoch (RUN-1.3); one GPU job at a time (ADR-0003).</span>
      </div>
      {save.error instanceof ApiError && save.error.problem.code !== "validation_error" && save.error.problem.code !== "conflict" ? (
        <ErrorState error={save.error} title="Training was not saved" />
      ) : null}
      <SaveBar
        title="Training"
        list={list}
        problems={Object.keys(errors).length}
        busy={save.isPending}
        onDiscard={() => { save.reset(); setDraft(section.values); }}
        onSave={() => {
          save.mutate(
            { section: "training", values: draft, version: section.version },
            {
              onSuccess: () => { onSaved("Training saved. The watchdog uses it from its next check."); },
              onError: (error) => { if (error instanceof ApiError && error.problem.code === "conflict") onConflict(); },
            },
          );
        }}
      />
    </section>
  );
}

/** SCR-14's M11 section (ADR-0020 rule 14): sampling intervals (MON-2.2), the alert
 * thresholds (MON-2.6, OC-7) and the diagnostics' disk figure (MON-3.2). */
function MonitoringSection({ section, draft, setDraft, onSaved, onConflict }: {
  section: SettingsSection;
  draft: Values;
  setDraft: (values: Values) => void;
  onSaved: (message: string) => void;
  onConflict: () => void;
}) {
  const save = useSaveSettings();
  const props = (section.schema as { properties?: Record<string, Prop> }).properties ?? {};
  const local = fieldErrors("monitoring", draft, { properties: props }, null);
  const errors = { ...local, ...serverErrors(save.error) };
  const list = changes(section.values, draft);
  const field = (name: string, extra?: ReactNode) => (
    <NumberField
      key={name}
      name={name}
      prop={props[name] ?? {}}
      value={draft[name]}
      saved={section.values[name]}
      error={errors[name]}
      onChange={(value) => { save.reset(); setDraft({ ...draft, [name]: value }); }}
    >
      {extra}
    </NumberField>
  );
  return (
    <section className="sec" id="monitoring" aria-labelledby="h-monitoring">
      <header>
        <h2 id="h-monitoring">Monitoring</h2>
        <span className="req">{section.requirements.join(" · ")}</span>
      </header>
      {field("sample_interval_active_s", <span>The sampler reads it within 30 s.</span>)}
      {field("sample_interval_idle_s")}
      {field("vram_alert_percent", <span>Of the card's total (NVML), OC-7. An alert fires after 5 s above it and re-arms 5 points below.</span>)}
      {field("ram_alert_percent", <span>Of the WSL VM's memory, OC-7.</span>)}
      {field("disk_bottleneck_mb_s", <span>A first guess, checked on the phase-2 acceptance run (ADR-0020 rule 8).</span>)}
      <div className="rules-note">
        <Icon name="info" />
        <span>Fixed, not settings: the 5 s hold and the 30 s diagnostic window (ADR-0020). The temperature limits are in Training.</span>
      </div>
      {save.error instanceof ApiError && save.error.problem.code !== "validation_error" && save.error.problem.code !== "conflict" ? (
        <ErrorState error={save.error} title="Monitoring was not saved" />
      ) : null}
      <SaveBar
        title="Monitoring"
        list={list}
        problems={Object.keys(errors).length}
        busy={save.isPending}
        onDiscard={() => { save.reset(); setDraft(section.values); }}
        onSave={() => {
          save.mutate(
            { section: "monitoring", values: draft, version: section.version },
            {
              onSuccess: () => { onSaved("Monitoring saved. Alerts use it within 5 s, the sampler within 30 s."); },
              onError: (error) => { if (error instanceof ApiError && error.problem.code === "conflict") onConflict(); },
            },
          );
        }}
      />
    </section>
  );
}

function RetentionSection({ section, draft, setDraft, onSaved, onConflict }: {
  section: SettingsSection;
  draft: Values;
  setDraft: (values: Values) => void;
  onSaved: (message: string) => void;
  onConflict: () => void;
}) {
  const save = useSaveSettings();
  const [confirm, setConfirm] = useState<RetentionPlan | null>(null);
  const [adding, setAdding] = useState(false);
  const props = (section.schema as { properties?: Record<string, Prop> }).properties ?? {};
  const keep = draft["keep_intermediate"] as Keep;
  const overrides = (draft["overrides"] ?? {}) as Record<string, Keep>;
  const savedGlobal = section.values["keep_intermediate"] as Keep;
  const lineages = section.lineages ?? [];
  const list = changes(section.values, draft);
  const badNumber = (k: Keep) => k !== "all" && (!Number.isInteger(k) || k < 0 || k > 100);
  const problems = (badNumber(keep) ? 1 : 0) + Object.values(overrides).filter(badNumber).length;
  const setOverrides = (next: Record<string, Keep>) => { save.reset(); setDraft({ ...draft, overrides: next }); };

  function submit(token?: string) {
    save.mutate(
      { section: "retention", values: draft, version: section.version, confirm: token ?? null },
      {
        onSuccess: (saved) => {
          setConfirm(null);
          onSaved(saved.job_id ? `Retention saved. Retention job ${saved.job_id} deletes the confirmed versions.` : "Retention saved. Nothing to delete.");
        },
        onError: (error) => {
          const plan = planOf(error);
          if (plan) setConfirm(plan);
          else if (error instanceof ApiError && error.problem.code === "conflict") onConflict();
        },
      },
    );
  }

  const doomed = confirm ? confirm.lineages.flatMap((l) => l.delete) : [];
  const affected = new Set(confirm?.lineages.filter((l) => l.delete.length).map((l) => l.lineage_id) ?? []);
  const unaffected = lineages.filter((l) => !affected.has(l.lineage_id));
  return (
    <section className="sec" id="retention" aria-labelledby="h-retention">
      <header>
        <h2 id="h-retention">Retention</h2>
        <span className="req">{section.requirements.join(" · ")}</span>
        <span className="spacer" />
        <span className="aside">Base and latest weights are always kept (REG-2.1, REG-2.2)</span>
      </header>
      <div className="frow">
        <div className={badNumber(keep) ? "field error" : "field"}>
          <label htmlFor="f-keep">
            <span className="mono">keep_intermediate</span>
            <span className="badge">integer | "all"</span>
          </label>
          <span style={{ display: "flex", gap: "var(--sp-2)" }}>
            <span className="group-btns" role="group" aria-label="Keep">
              <button type="button" className="btn sm" aria-pressed={keep !== "all"} onClick={() => { setDraft({ ...draft, keep_intermediate: savedGlobal === "all" ? 0 : savedGlobal }); }}>Number</button>
              <button type="button" className="btn sm" aria-pressed={keep === "all"} onClick={() => { setDraft({ ...draft, keep_intermediate: "all" }); }}>All</button>
            </span>
            {keep !== "all" ? (
              <input
                id="f-keep"
                className="input mono"
                inputMode="numeric"
                style={{ width: 72, height: "var(--h-sm)" }}
                aria-invalid={badNumber(keep) || undefined}
                aria-describedby="f-keep-help"
                value={String(keep)}
                onChange={(event) => { save.reset(); setDraft({ ...draft, keep_intermediate: /^\d+$/.test(event.target.value) ? Number(event.target.value) : event.target.value }); }}
              />
            ) : null}
          </span>
          <small id="f-keep-help">{badNumber(keep) ? "0–100, or all." : props["keep_intermediate"]?.description}</small>
        </div>
        <div className="applies">
          <span><span className="badge"><Icon name="alert" />asks first when lowered</span></span>
          <span>Lowering it lists the versions and sizes it would delete before anything is saved (REG-2.10). Raising it saves at once.</span>
        </div>
      </div>
      <div className="ovr">
        <h3>
          Per-lineage overrides
          <span className="req" style={{ font: "var(--fs-11) var(--font-mono)", color: "var(--text-muted)", fontWeight: 400 }}>REG-2.4</span>
          <span className="spacer" />
          <Button size="sm" disabled={lineages.every((l) => l.lineage_id in overrides)} onClick={() => { setAdding(true); }}>
            <Icon name="plus" />
            Add override
          </Button>
        </h3>
        {lineages.length === 0 ? (
          <EmptyState icon="sliders" title="No per-lineage overrides">
            Every lineage keeps the default. Models appear here after their first run.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="t dense">
              <thead>
                <tr><th>Lineage</th><th>keep_intermediate</th><th className="r">Intermediates now</th><th className="r">Their size</th><th><span className="sr-only">Action</span></th></tr>
              </thead>
              <tbody>
                {lineages.map((l) => {
                  const own = overrides[l.lineage_id];
                  return (
                    <tr key={l.lineage_id}>
                      <td className="id">{l.name}</td>
                      {own === undefined ? (
                        <td className="dim">default ({keepText(keep)})</td>
                      ) : (
                        <td>
                          <input
                            className="input mono"
                            aria-label={`keep_intermediate for ${l.name}`}
                            aria-invalid={badNumber(own) || undefined}
                            value={String(own)}
                            onChange={(event) => {
                              const text = event.target.value.trim();
                              setOverrides({ ...overrides, [l.lineage_id]: text === "all" ? "all" : /^\d+$/.test(text) ? Number(text) : (text as unknown as number) });
                            }}
                          />
                        </td>
                      )}
                      <td className="r">{l.intermediates}</td>
                      <td className="r">{bytes(l.intermediates_bytes)}</td>
                      <td style={{ textAlign: "right" }}>
                        {own === undefined ? (
                          <Button size="sm" variant="ghost" aria-label={`Override keep_intermediate for ${l.name}`} onClick={() => { setOverrides({ ...overrides, [l.lineage_id]: keep }); }}>Override…</Button>
                        ) : (
                          <Button size="sm" variant="ghost" aria-label={`Remove the override for ${l.name}`} onClick={() => { setOverrides(without(overrides, l.lineage_id)); }}>Remove</Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="rules-note">
        <Icon name="info" />
        <span>
          Fixed by the requirements: a run's checkpoint is deleted when it completes and kept while it is paused or partial (REG-2.6); pinned versions are never deleted (REG-2.5). A dry run is <i>Preview cleanup</i> on <Link to="/models">SCR-11</Link>.
        </span>
      </div>
      {save.error instanceof ApiError && !planOf(save.error) && save.error.problem.code !== "conflict" ? (
        <ErrorState error={save.error} title="Retention was not saved" />
      ) : null}
      <SaveBar
        title="Retention"
        list={list}
        problems={problems}
        busy={save.isPending}
        onDiscard={() => { save.reset(); setDraft(section.values); }}
        onSave={() => { submit(); }}
      />
      <Modal
        open={adding}
        onOpenChange={setAdding}
        title="Add an override"
        width={420}
        footer={<Button onClick={() => { setAdding(false); }}>Close</Button>}
      >
        <div className="scr14 in-dialog" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
          {lineages.filter((l) => !(l.lineage_id in overrides)).map((l) => (
            <Button key={l.lineage_id} size="sm" onClick={() => { setOverrides({ ...overrides, [l.lineage_id]: keep }); setAdding(false); }}>
              {l.name}
            </Button>
          ))}
        </div>
      </Modal>
      {confirm ? (
        <ConfirmDeletion
          open
          onOpenChange={(open) => { if (!open) setConfirm(null); }}
          title={
            list.some((c) => c.startsWith("keep_intermediate"))
              ? `Keep ${keepText(keep)} intermediate versions by default (now ${keepText(savedGlobal)})?`
              : "Save these overrides?"
          }
          plan={confirm}
          phrase={`delete ${String(doomed.length)} version${doomed.length === 1 ? "" : "s"}`}
          showLineage
          busy={save.isPending}
          kept={
            <>
              {unaffected.length ? <>Not affected: {unaffected.map((l) => l.name).join(", ")}. </> : null}
              Kept in every lineage: the base, the latest and pinned versions.
            </>
          }
          onConfirm={() => { submit(confirm.token); }}
        />
      ) : null}
    </section>
  );
}

/** SCR-14's M12 section (LLM-2.3; ADR-0022 rule 8; SCR-06retrieval_v1 frame B): the
 * abstention threshold of the embedding model, where it was chosen, and the default k. The
 * threshold is the owner's (M12 decision 6): empty until set, and then RAG never abstains. */
export function retrievalErrors(values: Values): Record<string, string> {
  const errors: Record<string, string> = {};
  const t = values["abstention_threshold"];
  if (t !== null && t !== undefined && (typeof t !== "number" || t < 0 || t > 1)) errors["abstention_threshold"] = "A number from 0 to 1, or empty.";
  const k = values["default_k"];
  if (typeof k !== "number" || !Number.isInteger(k)) errors["default_k"] = "A whole number is needed.";
  else if (k < 1 || k > 20) errors["default_k"] = "From 1 to 20.";
  return errors;
}

function RetrievalSection({ section, draft, setDraft, onSaved, onConflict }: {
  section: SettingsSection;
  draft: Values;
  setDraft: (values: Values) => void;
  onSaved: (message: string) => void;
  onConflict: () => void;
}) {
  const save = useSaveSettings();
  const props = (section.schema as { properties?: Record<string, Prop> }).properties ?? {};
  const errors = { ...retrievalErrors(draft), ...serverErrors(save.error) };
  const list = changes(section.values, draft);
  const model = text(section.values["embedding_model"]);
  const source = section.values["threshold_source"];
  const threshold: Prop = {
    type: "number",
    minimum: 0,
    maximum: 1,
    description: props["abstention_threshold"]?.description,
    "x-applies": "at once",
  };
  return (
    <section className="sec" id="retrieval" aria-labelledby="h-retrieval">
      <header>
        <h2 id="h-retrieval">Retrieval</h2>
        <span className="req">{section.requirements.join(" · ")}</span>
      </header>
      <NumberField
        name="abstention_threshold"
        prop={threshold}
        value={draft["abstention_threshold"]}
        saved={section.values["abstention_threshold"]}
        error={errors["abstention_threshold"]}
        onChange={(value) => { save.reset(); setDraft({ ...draft, abstention_threshold: value === "" ? null : value }); }}
      >
        <span>
          For <span className="mono">{model}</span>.{" "}
          {typeof source === "string" && source ? (
            <>
              Chosen from <Link className="mono" to={`/versions/${source}/report?kind=retrieval`}>{source}</Link>&apos;s retrieval report.
            </>
          ) : section.values["abstention_threshold"] === null ? (
            "Not set: pick one from an index's retrieval report."
          ) : null}
        </span>
      </NumberField>
      <NumberField
        name="default_k"
        prop={{ ...(props["default_k"] ?? {}), "x-applies": "at once" }}
        value={draft["default_k"]}
        saved={section.values["default_k"]}
        error={errors["default_k"]}
        onChange={(value) => { save.reset(); setDraft({ ...draft, default_k: value }); }}
      >
        <span>Sources per question, 1 to 20.</span>
      </NumberField>
      <div className="rules-note">
        <Icon name="info" />
        <span>
          Below the threshold, RAG answers &quot;I don&apos;t know&quot; with the nearest source, without asking the model (LLM-2.3). With no threshold
          set, RAG never abstains.
        </span>
      </div>
      {save.error instanceof ApiError && save.error.problem.code !== "validation_error" && save.error.problem.code !== "conflict" ? (
        <ErrorState error={save.error} title="Retrieval was not saved" />
      ) : null}
      <SaveBar
        title="Retrieval"
        list={list}
        problems={Object.keys(errors).length}
        busy={save.isPending}
        onDiscard={() => { save.reset(); setDraft(section.values); }}
        onSave={() => {
          save.mutate(
            { section: "retrieval", values: draft, version: section.version },
            {
              onSuccess: () => { onSaved("Retrieval saved. RAG uses it from the next question."); },
              onError: (error) => { if (error instanceof ApiError && error.problem.code === "conflict") onConflict(); },
            },
          );
        }}
      />
    </section>
  );
}

function AppearanceSection() {
  const [theme, toggle] = useTheme();
  return (
    <section className="sec" id="appearance" aria-labelledby="h-appearance">
      <header>
        <h2 id="h-appearance">Appearance</h2>
        <span className="spacer" />
        <span className="aside">Stored in this browser, not the database</span>
      </header>
      <div className="frow" style={{ borderBottom: 0 }}>
        <div className="field">
          <label><span className="mono">theme</span><span className="badge">enum</span></label>
          <span className="group-btns" role="group" aria-label="Theme">
            <button type="button" className="btn sm" aria-pressed={theme === "dark"} onClick={() => { if (theme !== "dark") toggle(); }}>Dark</button>
            <button type="button" className="btn sm" aria-pressed={theme === "light"} onClick={() => { if (theme !== "light") toggle(); }}>Light</button>
          </span>
          <small>Follows the system setting on first visit. <kbd>t</kbd> switches it anywhere.</small>
        </div>
        <div className="applies"><AppliesBadge text="at once" /></div>
      </div>
    </section>
  );
}

export function SettingsScreen() {
  useCrumbs([{ label: "Settings" }]);
  useRegistryLive();
  const settings = useSettings();
  const location = useLocation();
  const [drafts, setDrafts] = useState<Record<string, Values>>({});
  const [toast, setToast] = useState<string | null>(null);
  const [stale, setStale] = useState<string | null>(null);
  const data = settings.data;
  const byName = useMemo(
    () => Object.fromEntries((data?.sections ?? []).map((s) => [s.name, s])) as Record<string, SettingsSection | undefined>,
    [data],
  );
  const draftOf = useCallback(
    (name: string): Values => drafts[name] ?? byName[name]?.values ?? {},
    [drafts, byName],
  );
  const unsaved = (name: string) => {
    const section = byName[name];
    return section ? changes(section.values, draftOf(name)).length : 0;
  };
  const total = unsaved("training") + unsaved("retention") + unsaved("retrieval");
  const blocker = useBlocker(({ currentLocation, nextLocation }) => total > 0 && currentLocation.pathname !== nextLocation.pathname);
  const toastTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (total === 0) return undefined;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => { window.removeEventListener("beforeunload", warn); };
  }, [total]);

  function saved(name: string, message: string) {
    setDrafts((d) => without(d, name));
    setToast(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => { setToast(null); }, 6000);
  }

  const current = location.hash.slice(1) || "training";
  if (settings.isError) {
    return (
      <div className="scr14 page">
        <ErrorState error={settings.error} title="Settings could not be loaded" onRetry={() => void settings.refetch()} />
      </div>
    );
  }
  return (
    <div className="scr14 page">
      <div className="phead">
        <h1>Settings</h1>
        <span className="sub">Defaults and limits for every job, stored in the database (OC-9)</span>
      </div>
      {settings.isPending ? (
        <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <Skeleton width={160} height={22} />
          <Skeleton width={800} height={32} />
          <Skeleton width={800} height={32} />
        </div>
      ) : (
        <div className="cols">
          <nav className="index" aria-label="Sections">
            {(["training", "retention", "monitoring", "retrieval"] as const).map((name) => (
              <a key={name} href={`#${name}`} aria-current={current === name ? "true" : undefined}>
                {byName[name]?.title ?? name}
                {unsaved(name) ? (
                  <>
                    <span className="count" aria-hidden="true">{unsaved(name)}</span>
                    <span className="sr-only">{unsaved(name)} unsaved change{unsaved(name) === 1 ? "" : "s"}</span>
                  </>
                ) : null}
              </a>
            ))}
            <a href="#appearance" aria-current={current === "appearance" ? "true" : undefined}>Appearance</a>
          </nav>
          <div className="secs">
            {byName["training"] ? (
              <TrainingSection
                section={byName["training"]}
                draft={draftOf("training")}
                setDraft={(values) => { setDrafts((d) => ({ ...d, training: values })); }}
                onSaved={(message) => { saved("training", message); }}
                onConflict={() => { setStale("training"); }}
              />
            ) : null}
            {byName["retention"] ? (
              <RetentionSection
                section={byName["retention"]}
                draft={draftOf("retention")}
                setDraft={(values) => { setDrafts((d) => ({ ...d, retention: values })); }}
                onSaved={(message) => { saved("retention", message); }}
                onConflict={() => { setStale("retention"); }}
              />
            ) : null}
            {byName["monitoring"] ? (
              <MonitoringSection
                section={byName["monitoring"]}
                draft={draftOf("monitoring")}
                setDraft={(values) => { setDrafts((d) => ({ ...d, monitoring: values })); }}
                onSaved={(message) => { saved("monitoring", message); }}
                onConflict={() => { setStale("monitoring"); }}
              />
            ) : null}
            {byName["retrieval"] ? (
              <RetrievalSection
                section={byName["retrieval"]}
                draft={draftOf("retrieval")}
                setDraft={(values) => { setDrafts((d) => ({ ...d, retrieval: values })); }}
                onSaved={(message) => { saved("retrieval", message); }}
                onConflict={() => { setStale("retrieval"); }}
              />
            ) : null}
            <AppearanceSection />
          </div>
        </div>
      )}
      {toast || stale ? (
        <div className="toasts">
          {stale ? (
            /* SCR-14 frame D: a stale section is an alert; only that section reloads. */
            <div className="toast" role="alert" style={{ "--c": "var(--warning)" } as CSSProperties}>
              <Icon name="alert" />
              <div>
                <b>Settings changed elsewhere</b>
                <span>
                  {byName[stale]?.title ?? stale} was saved in another tab after this page loaded (<span className="mono">conflict</span>). Your other changes are kept.
                </span>
                <div style={{ marginTop: "var(--sp-2)" }}>
                  <Button size="sm" onClick={() => { setDrafts((d) => without(d, stale)); setStale(null); void settings.refetch(); }}>
                    Reload {(byName[stale]?.title ?? stale).toLowerCase()}
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
          {toast ? (
            <div className="toast" role="status" style={{ "--c": "var(--success)" } as CSSProperties}>
              <Icon name="check" />
              <div><b>Saved</b><span>{toast}</span></div>
            </div>
          ) : null}
        </div>
      ) : null}
      <Modal
        open={blocker.state === "blocked"}
        onOpenChange={(open) => { if (!open) blocker.reset?.(); }}
        title="Leave with unsaved changes?"
        width={440}
        alert
        icon={<Icon name="alert" />}
        footer={
          <>
            <Button onClick={() => blocker.reset?.()}>Stay</Button>
            <Button variant="danger" onClick={() => blocker.proceed?.()}>Leave and discard</Button>
          </>
        }
      >
        {total} unsaved change{total === 1 ? "" : "s"} would be lost.
      </Modal>
    </div>
  );
}
