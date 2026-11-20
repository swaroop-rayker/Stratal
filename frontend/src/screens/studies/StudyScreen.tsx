// SCR-10 (M10, mockup SCR-10_v1): an HPO study's results (HPO-1.2 to HPO-1.5). The header
// with both limits; the optimisation history and the best so far; every trial with its
// state in words; parameter importance once the study closes; the test evaluation,
// computed once. A row opens the trial's drawer (frame C). Pause, resume and cancel are the
// study job's (API.md §3.12).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, call, type Job, type JobState } from "../../api/client";
import { useVersion } from "../../api/queries";
import { useVersionDetail } from "../../api/registry";
import {
  fieldName,
  fixedFields,
  isLive,
  type Marker,
  modelName,
  score,
  showValue,
  type Study,
  stepsOf,
  stepUnit,
  studyKeys,
  type Trial,
  useStudy,
  useStudyLive,
  useTrials,
} from "../../api/studies";
import { Drawer } from "../../components/Drawer";
import { Modal, ModalClose } from "../../components/Modal";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Progress, Skeleton, StatePill } from "../../components/ui";
import { clock, count, stamp } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useCrumbs } from "../../shell/hooks";
import { HistoryChart, HistoryTable, medianAtSteps, TrialChart } from "./History";

type Filter = "all" | Trial["state"];

/** mm:ss, or h:mm:ss from an hour. */
export function minutes(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (v: number) => String(v).padStart(2, "0");
  return h ? `${String(h)}:${pad(m)}:${pad(s % 60)}` : `${String(m)}:${pad(s % 60)}`;
}

/** A trial's state as SCR-10 says it: the pill's word, and why in muted words. */
export function trialWords(trial: Trial, unit: string): { pill: string; why: string | null } {
  if (trial.state === "pruned") {
    return { pill: "pruned", why: `at ${unit} ${String(trial.step ?? "?")} · ${trial.prune_reason ?? "median"}` };
  }
  if (trial.state === "failed") {
    const notRun = trial.code === "invalid_combination" || trial.code === "over_vram";
    return { pill: "failed", why: `${trial.code ?? "failed"}${notRun ? " · not run" : ""}` };
  }
  return { pill: trial.state, why: null };
}

const TRIAL_PILL: Record<Trial["state"], JobState | "pruned"> = {
  running: "running",
  complete: "completed",
  pruned: "pruned",
  failed: "failed",
};

function TrialPill({ trial }: { trial: Trial }) {
  const kind = TRIAL_PILL[trial.state];
  if (kind === "pruned") {
    return <span className="pill pruned"><Icon name="ban" />pruned</span>;
  }
  return <StatePill state={kind} label={trial.state} />;
}

function useJob(id: string | undefined) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id ?? "" } } })),
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data && ["running", "queued", "stalled"].includes(query.state.data.state) ? 3000 : false),
  });
}

/** The pill in the header: the study job's state while it runs, else the study's. */
function headerState(study: Study, job: Job | undefined): JobState {
  if (study.state === "completed") return "completed";
  if (study.state === "cancelled") return "cancelled";
  if (study.state === "failed") return "failed";
  return job?.state ?? "running";
}

function Limits({ study, job }: { study: Study; job: Job | undefined }) {
  const n = study.limits.n_trials ?? 0;
  const budget = study.limits.timeout_s ?? 0;
  const arrow = study.direction === "maximize" ? "↑ maximise" : "↓ minimise";
  const ended = study.ended_by === "n_trials" ? "the trial limit was reached first" : study.ended_by === "timeout" ? "the time limit was reached first" : null;
  return (
    <div className="limits">
      <span>Objective <b>{study.objective}</b> {arrow}</span>
      <span>Trials <b>{String(study.trials.started)}</b> of <b>{String(n)}</b><Progress value={n ? (100 * study.trials.started) / n : 0} />{ended && study.ended_by === "n_trials" ? <span className="muted"> · {ended}</span> : null}</span>
      <span>Time <b>{minutes(study.elapsed_s)}</b> of <b>{minutes(budget)}</b><Progress value={budget ? Math.min(100, (100 * study.elapsed_s) / budget) : 0} /> <span className="muted">{ended && study.ended_by === "timeout" ? ended : "not counting pauses"}</span></span>
      <span className="muted">
        {ended ? null : "Whichever comes first ends the study · "}TPE, seed {String(study.seed)} · {study.pruning ? "median pruning" : "no pruning"}
        {study.kind === "classical" ? ` · ${String(study.cv_folds)}-fold CV` : ""} · study job{" "}
        <Link className="mono" to={`/jobs/${study.job_id}`}>{study.job_id}</Link>
        {job?.pause_reason === "thermal" ? " · paused while the GPU cools" : ""}
      </span>
    </div>
  );
}

function BestCard({ study, trials, onStart }: { study: Study; trials: Trial[]; onStart: () => void }) {
  const best = study.best;
  const trial = best ? trials.find((t) => t.number === best.trial) : undefined;
  const closed = study.state === "completed";
  return (
    <section className="card">
      <header><h2>{closed ? "Best configuration" : "Best so far"}</h2><span className="spacer" />{best ? <span className="badge best">best · trial {String(best.trial)}</span> : null}</header>
      <div className="b">
        {best ? (
          <>
            <div className="kv2">
              <span>{study.objective}{study.kind === "classical" ? `, ${String(study.cv_folds)} folds` : ""}</span><span>{score(best.value)}</span>
              {study.space.map((m) => {
                const path = (m as Marker).path;
                return [<span key={`${path}-k`}>{fieldName(path)}</span>, <span key={`${path}-v`}>{showValue(best.params[path])}</span>];
              })}
              {fixedFields(study).map(([name, value]) => [<span key={`${name}-k`}>{name} <span className="muted">(fixed)</span></span>, <span key={`${name}-v`}>{showValue(value)}</span>])}
              {trial?.job_id ? (<><span>job</span><span><Link to={`/jobs/${trial.job_id}`}>{trial.job_id}</Link> · {minutes(trial.duration_s)}</span></>) : null}
            </div>
            {study.kind === "deep" && !closed ? <p className="note2">Its weights are kept until a better trial completes; the others are deleted as the study runs (ADR-0018 rule 7).</p> : null}
            <Button size="sm" onClick={onStart}><Icon name="play" />Start a run from this configuration</Button>
          </>
        ) : (
          <div className="placeholder"><Icon name="clock" /><span>No trial has completed yet.</span></div>
        )}
      </div>
    </section>
  );
}

function Importance({ study }: { study: Study }) {
  const [table, setTable] = useState(false);
  const imp = study.importance as { method?: string; values?: Record<string, number>; reason?: string } | null;
  const values = Object.entries(imp?.values ?? {}).sort((a, b) => b[1] - a[1]);
  const complete = study.trials.complete;
  return (
    <section className="card">
      <header><h2>Parameter importance</h2><span className="req">HPO-1.5</span><span className="spacer" />
        {values.length ? (
          <span className="group-btns" role="group" aria-label="Importance view">
            <Button size="sm" aria-pressed={!table} onClick={() => { setTable(false); }}>Bars</Button>
            <Button size="sm" aria-pressed={table} onClick={() => { setTable(true); }}>Table</Button>
          </span>
        ) : null}
      </header>
      <div className="b">
        {values.length ? (
          <>
            {table ? (
              <table className="t dense"><thead><tr><th>Field</th><th className="r">Share</th></tr></thead>
                <tbody>{values.map(([path, share]) => <tr key={path}><td className="mono">{fieldName(path)}</td><td className="p">{share.toFixed(2)}</td></tr>)}</tbody></table>
            ) : (
              <div className="imp">
                {values.map(([path, share]) => [
                  <span key={`${path}-n`}>{fieldName(path)}</span>,
                  <span key={`${path}-b`} className="bar" aria-hidden="true"><i style={{ "--p": `${String(Math.round(100 * share))}%` } as CSSProperties} /></span>,
                  <span key={`${path}-v`}>{share.toFixed(2)}</span>,
                ])}
              </div>
            )}
            <p className="note2">fANOVA over the {String(complete)} complete trials; the shares sum to 1. They say what mattered within this search space, not in general.</p>
          </>
        ) : (
          <div className="placeholder"><Icon name="info" /><span>{imp?.reason ? `Not computed: ${imp.reason}.` : "Computed when the study closes, from its complete trials."}</span></div>
        )}
      </div>
    </section>
  );
}

/** The test figures to show large: two scores and the row count. */
function testFigures(metrics: Record<string, unknown>): [string, string][] {
  const pick = ["test_accuracy", "test_macro_f1", "test_rmse", "test_r2", "test_mae"];
  const out: [string, string][] = [];
  for (const key of pick) {
    const v = metrics[key];
    if (typeof v === "number" && out.length < 2) out.push([score(v), key.replace("test_", "").replace("macro_f1", "macro F1").replace("rmse", "RMSE").replace("mae", "MAE").replace("r2", "R²")]);
  }
  const rows = (metrics.detail as { rows?: number } | null | undefined)?.rows;
  if (typeof rows === "number") out.push([count(rows), "test rows"]);
  return out;
}

function TestCard({ study, job, lineage }: { study: Study; job: Job | undefined; lineage: string | undefined }) {
  const metrics = study.test_metrics;
  const testing = !metrics && study.best_version_id && job && ["running", "queued", "stalled"].includes(job.state);
  return (
    <section className="card">
      <header><h2>Test evaluation</h2><span className="req">HPO-1.4</span><span className="spacer" />
        {metrics ? <Badge tone="info">computed once</Badge> : testing ? <StatePill state="running" label="running" /> : null}
      </header>
      <div className="b">
        {metrics ? (
          <div className="once">
            <div className="figs">
              {testFigures(metrics).map(([value, label]) => (
                <span key={label}><span className="big">{value}</span><span>{label}</span></span>
              ))}
            </div>
            <p className="note2">
              {study.test_evaluated_at ? `${stamp(study.test_evaluated_at)} · ` : ""}on{" "}
              {study.best_version_id && lineage ? <Link className="mono" to={`/models/${lineage}?version=${study.best_version_id}`}>{study.best_version_id}</Link> : <span className="mono">{study.best_version_id ?? "–"}</span>}, registered from trial {String(study.best?.trial ?? "–")} · in{" "}
              <Link className="mono" to={`/jobs/${study.job_id}`}>{study.job_id}</Link>. The confusion matrix and per-class figures are on the version's page (SCR-11).
            </p>
          </div>
        ) : testing ? (
          <div className="placeholder"><Icon name="clock" /><span>The study closed{study.ended_by ? ` at its ${study.ended_by === "timeout" ? "time" : "trial"} limit` : ""} after {String(study.trials.started)} trials. The best trial is registered as <span className="mono">{study.best_version_id}</span>, and its test evaluation is running.</span></div>
        ) : study.state === "completed" ? (
          <div className="placeholder"><Icon name="info" /><span>No trial completed: nothing to register or evaluate.</span></div>
        ) : (
          <div className="placeholder"><Icon name="info" /><span>The test split is used once, on the best configuration, after the last trial.</span></div>
        )}
      </div>
    </section>
  );
}

function TrialTable({ study, trials, onOpen, now }: { study: Study; trials: Trial[]; onOpen: (n: number) => void; now: number }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [desc, setDesc] = useState(true);
  const unit = stepUnit(study);
  const total = stepsOf(study);
  const paths = study.space.map((m) => (m as Marker).path);
  const rows = trials
    .filter((t) => filter === "all" || t.state === filter)
    .sort((a, b) => (desc ? b.number - a.number : a.number - b.number));
  const counts = study.trials;
  const deep = study.kind === "deep";
  return (
    <section className="card">
      <header><h2>Trials</h2><span className="req">HPO-1.3</span>
        <span className="counts">{String(counts.started)} started · {String(counts.complete)} complete · {String(counts.pruned)} pruned · {String(counts.failed)} failed{counts.running ? ` · ${String(counts.running)} running` : ""}{counts.interrupted ? ` · ${String(counts.interrupted)} interrupted, not counted` : ""}</span>
        <span className="spacer" />
        <label className="sr-only" htmlFor="trial-state">State</label>
        <select id="trial-state" className="select sm" value={filter} onChange={(e) => { setFilter(e.target.value as Filter); }}>
          <option value="all">State: all</option><option value="complete">complete</option><option value="pruned">pruned</option><option value="failed">failed</option><option value="running">running</option>
        </select>
      </header>
      <div className="table-wrap trials">
        <table className="t dense">
          <thead>
            <tr>
              <th aria-sort={desc ? "descending" : "ascending"}><button type="button" className="sortbtn" onClick={() => { setDesc(!desc); }}># <Icon name="sort" /></button></th>
              <th>State</th>
              <th className="r">{study.objective}</th>
              {paths.map((p) => <th key={p} className="r">{fieldName(p)}</th>)}
              <th className="r">{unit === "fold" ? "Folds" : "Epochs"}</th>
              <th className="r">Duration</th>
              {deep ? <th>Job</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const words = trialWords(t, unit);
              const isBest = study.best?.trial === t.number;
              const last = t.intermediate[t.intermediate.length - 1];
              const value = t.state === "running" ? last?.[1] ?? null : t.value;
              const own = (t.params["/config/epochs"] as number | undefined) ?? total;
              const reached = t.state === "failed" && !t.job_id ? null : t.step ?? (t.state === "complete" ? own : null);
              const seconds = t.state === "running" && t.started_at ? (now - Date.parse(t.started_at)) / 1000 : t.duration_s;
              return (
                <tr key={t.number} tabIndex={0} className="clickable" onClick={() => { onOpen(t.number); }} onKeyDown={(e) => { if (e.key === "Enter") onOpen(t.number); }}>
                  <td className="p">{String(t.number)}</td>
                  <td><TrialPill trial={t} />{isBest ? <span className="badge best">best</span> : null}{words.why ? <span className="why">{words.why}</span> : null}</td>
                  <td className={`p${isBest ? " best-col" : ""}`}>{score(value)}{t.state === "running" && last ? <span className="muted"> {unit[0]}{String(last[0])}</span> : null}</td>
                  {paths.map((p) => <td key={p} className="p">{showValue(t.params[p])}</td>)}
                  <td className="p">{reached === null ? "–" : `${String(reached)} / ${String(own ?? "?")}`}</td>
                  <td className="p">{deep ? minutes(seconds) : seconds === null ? "–" : `${seconds.toFixed(1)} s`}</td>
                  {deep ? <td className="mono">{t.job_id ? <Link to={`/jobs/${t.job_id}`} onClick={(e) => { e.stopPropagation(); }}>{t.job_id}</Link> : "–"}</td> : null}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 ? <div className="placeholder pad"><Icon name="info" /><span>{trials.length ? "No trial in this state." : "No trial has started yet."}</span></div> : null}
      </div>
    </section>
  );
}

function TrialDrawer({ study, trials, number, onClose }: { study: Study; trials: Trial[]; number: number | null; onClose: () => void }) {
  const trial = trials.find((t) => t.number === number);
  const unit = stepUnit(study);
  const median = useMemo(() => (trial ? medianAtSteps(trials, trial.number) : new Map<number, number>()), [trials, trial]);
  if (!trial) return null;
  const words = trialWords(trial, unit);
  const why =
    trial.state === "pruned" && trial.prune_reason === "budget"
      ? `The study's time budget ran out during ${unit} ${String(trial.step ?? "?")}, so the trial stopped there (HPO-1.2).`
      : trial.state === "pruned"
        ? `The pruner compared ${unit} ${String(trial.step ?? "?")}'s ${study.objective} with the median of earlier trials at that ${unit}, and stopped the trial.`
        : trial.state === "failed"
          ? trial.code === "over_vram"
            ? "The sampled configuration would not fit under the GPU's cap by the builder's estimate, so it was never trained; it counts towards the trial limit."
            : trial.code === "invalid_combination"
              ? "The sampled values break a rule between fields, so the trial was never trained; it counts towards the trial limit."
              : trial.code === "interrupted"
                ? "A pause or a crash stopped the trial before it finished; it does not count towards the trial limit."
                : `The trial's job failed (${trial.code ?? "unknown"}). Its job's page says why.`
          : trial.state === "complete"
            ? `It completed ${String(trial.intermediate.length)} ${unit}s.`
            : `It is running: ${unit} ${String(trial.step ?? 0)} so far.`;
  return (
    <Drawer open={number !== null} onOpenChange={(o) => { if (!o) onClose(); }} title={`Trial ${String(trial.number)}`} width={520} className="scr10-drawer"
      heading={<h2>Trial {String(trial.number)} <span className="h-sub">{words.pill}{words.why ? ` ${words.why}` : ""}</span></h2>}>
      <div className="body">
        {trial.intermediate.length ? (
          <>
            <div className="legend2"><span><i className="l" style={{ background: "var(--cat-1)" }} />trial {String(trial.number)}</span><span><i className="m" />median of earlier trials at each {unit}</span></div>
            <TrialChart trial={trial} median={median} />
          </>
        ) : null}
        <div className="kv2">
          <span>{study.objective}</span><span>{score(trial.value)}</span>
          {Object.entries(trial.params).map(([p, v]) => [<span key={`${p}-k`}>{fieldName(p)}</span>, <span key={`${p}-v`}>{showValue(v)}</span>])}
          {trial.job_id ? (<><span>job</span><span>{trial.job_id} · {minutes(trial.duration_s)}</span></>) : null}
        </div>
        <p>{why}</p>
        {trial.job_id ? <Link className="btn sm" to={`/jobs/${trial.job_id}`}><Icon name="eye" />Open the trial's job (SCR-04)</Link> : null}
      </div>
    </Drawer>
  );
}

function CancelStudy({ study, trials, open, onOpenChange }: { study: Study; trials: Trial[]; open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useQueryClient();
  const keep = useRef<HTMLButtonElement>(null);
  const cancel = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: study.job_id } } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: studyKeys.all });
      void client.invalidateQueries({ queryKey: ["jobs"] });
      onOpenChange(false);
    },
  });
  const running = trials.find((t) => t.state === "running");
  const finished = trials.filter((t) => t.state !== "running").length;
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Cancel this study?" alert initialFocus={keep}
      icon={<Icon name="alert" style={{ color: "var(--danger)" }} />}
      footer={<><ModalClose asChild><Button ref={keep}>Keep running</Button></ModalClose><Button variant="danger" disabled={cancel.isPending} onClick={() => { cancel.mutate(); }}><Icon name="ban" />Cancel study</Button></>}>
      {running ? <>Trial {String(running.number)}{running.job_id ? <> (<span className="mono">{running.job_id}</span>)</> : null} stops now. </> : null}
      Nothing is registered and the test split is not used. The {String(finished)} finished trials stay readable here{study.kind === "deep" ? "; their kept weights are deleted" : ""}.
    </Modal>
  );
}

export function StudyScreen() {
  const { studyId = "" } = useParams();
  useCrumbs([{ label: "Models", to: "/models" }, { label: `Study ${studyId}` }]);
  const navigate = useNavigate();
  const client = useQueryClient();
  const study = useStudy(studyId);
  const data = study.data;
  const live = isLive(data);
  const trials = useTrials(studyId, live);
  useStudyLive(data);
  const job = useJob(data?.job_id);
  const version = useVersion(data?.dataset_version ?? "", Boolean(data));
  const best = useVersionDetail(data?.best_version_id ?? null);
  const now = useNow(live ? 1000 : 60_000);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [open, setOpen] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const act = useMutation({
    mutationFn: (action: "pause" | "resume") =>
      call(() => api.POST(action === "pause" ? "/api/v1/jobs/{job_id}/pause" : "/api/v1/jobs/{job_id}/resume", { params: { path: { job_id: data?.job_id ?? "" } } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["jobs", data?.job_id] });
      void client.invalidateQueries({ queryKey: studyKeys.study(studyId) });
    },
  });

  if (study.isError) {
    return (
      <div className="scr10">
        <ErrorState error={study.error} title="The study could not be loaded" onRetry={() => void study.refetch()} />
        <span><Button size="sm" onClick={() => { void navigate("/jobs"); }}>Go to Jobs</Button></span>
      </div>
    );
  }
  if (!data) {
    return <div className="scr10" aria-busy="true"><Skeleton width={360} height={28} /><Skeleton width={720} height={18} /><Skeleton width={960} height={220} /></div>;
  }
  const list = trials.data?.items ?? [];
  const state = headerState(data, job.data);
  const jobLive = job.data ? !["completed", "failed", "cancelled"].includes(job.data.state) : false;
  const finished = list.filter((t) => t.state !== "running");
  const runningTrial = list.find((t) => t.state === "running");
  const startFromBest = () => { void navigate(`/runs/new?study=${data.id}`); };
  const lineage = best.data?.lineage_id;
  return (
    <div className="scr10">
      <div className="phead">
        <h1>{modelName(data)} · {version.data?.dataset ?? data.dataset_version}</h1>
        <StatePill state={state} />
        <Badge>{data.kind === "deep" ? "deep learning · one job per trial" : `classical · trials in one job · ${String(data.cv_folds)}-fold CV`}</Badge>
        <span className="spacer" />
        {data.state === "completed" && data.best ? <Button size="sm" onClick={startFromBest}><Icon name="play" />Start a run from the best configuration</Button> : null}
        {data.best_version_id && lineage ? <Button size="sm" onClick={() => { void navigate(`/models/${lineage}?version=${data.best_version_id ?? ""}`); }}><Icon name="layers" />Open the best model</Button> : null}
        {jobLive && job.data?.state === "paused" ? <Button size="sm" disabled={act.isPending} onClick={() => { act.mutate("resume"); }}><Icon name="play" />Resume</Button> : null}
        {jobLive && job.data?.state === "running" && data.state === "running" ? <Button size="sm" disabled={act.isPending || job.data.desired_state !== null} onClick={() => { act.mutate("pause"); }}><Icon name="pause" />{job.data.desired_state === "paused" ? "Pausing…" : "Pause"}</Button> : null}
        {jobLive && data.state === "running" ? <Button size="sm" variant="danger" onClick={() => { setCancelling(true); }}><Icon name="ban" />Cancel study…</Button> : null}
      </div>
      <Limits study={data} job={job.data} />
      <div aria-live="polite" className="sr-only">{`${String(data.trials.started)} of ${String(data.limits.n_trials ?? 0)} trials; ${minutes(data.elapsed_s)} used`}</div>
      {data.state === "cancelled" ? (
        <div className="callout info"><Icon name="ban" /><span>Cancelled after trial {String(Math.max(data.trials.started, 0))} of {String(data.limits.n_trials ?? 0)}{data.finished_at ? `, ${clock(data.finished_at)}` : ""}. Nothing was registered and the test split was not used.{data.kind === "deep" ? " Trial weights were deleted." : ""}</span></div>
      ) : null}
      <div className="sgrid">
        <section className="card">
          <header><h2>Optimisation history</h2><span className="req">HPO-1.5</span><span className="spacer" />
            <span className="group-btns" role="group" aria-label="View">
              <Button size="sm" aria-pressed={view === "chart"} onClick={() => { setView("chart"); }}>Chart</Button>
              <Button size="sm" aria-pressed={view === "table"} onClick={() => { setView("table"); }}>Table</Button>
            </span>
          </header>
          <div className="b">
            {finished.length === 0 ? (
              <EmptyState icon="chart" title="No trial has finished yet."
                action={runningTrial?.job_id ? <Button size="sm" onClick={() => { void navigate(`/jobs/${runningTrial.job_id ?? ""}`); }}>Open trial {String(runningTrial.number)}'s job</Button> : undefined}>
                {runningTrial ? `Trial ${String(runningTrial.number)} is running${runningTrial.job_id ? ` as ${runningTrial.job_id}` : ""}, ${stepUnit(data)} ${String(runningTrial.step ?? 0)}${stepsOf(data) ? ` of ${String(stepsOf(data))}` : ""}.` : "The first trial starts shortly."}
              </EmptyState>
            ) : view === "chart" ? (
              <>
                <HistoryChart study={data} trials={list} />
                <div className="legend2"><span><i />complete</span><span><i className="h" />pruned, at its last value</span><span><span className="x">✕</span>failed, not run</span><span><i className="l" />best so far</span>{runningTrial ? <span className="muted">Trial {String(runningTrial.number)} is running and appears when it ends.</span> : null}</div>
              </>
            ) : (
              <HistoryTable study={data} trials={list} />
            )}
          </div>
        </section>
        <BestCard study={data} trials={list} onStart={startFromBest} />
      </div>
      <TrialTable study={data} trials={list} onOpen={setOpen} now={now} />
      <div className="sgrid2">
        <Importance study={data} />
        <TestCard study={data} job={job.data} lineage={lineage} />
      </div>
      <TrialDrawer study={data} trials={list} number={open} onClose={() => { setOpen(null); }} />
      <CancelStudy study={data} trials={list} open={cancelling} onOpenChange={setCancelling} />
      {act.isError ? <ErrorState error={act.error} title="The study's job did not take the request" /> : null}
    </div>
  );
}
