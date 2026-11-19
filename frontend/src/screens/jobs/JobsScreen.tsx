// SCR-07 jobs list, phase-1 slice (SCREENS.md §3; mockup SCR-07_v1; RUN-1.1, RUN-1.2 slice):
// every job with its state and what it is doing or what it made; filters by state, dataset,
// kind and job ID; open, cancel and retry. v2 (M8, mockup SCR-07v2_v1): pause and resume
// (RUN-1.2), training jobs, and the reasons a job is paused or stalled (RUN-1.6, RUN-1.7).
import { type InfiniteData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { ApiError, api, call, type Job, type JobState, type Schemas } from "../../api/client";
import { useDatasets, useGpuTemperature, useSystem } from "../../api/queries";
import { useFeedMessage } from "../../api/stream";
import { Modal, ModalClose } from "../../components/Modal";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Kbd, Progress, Skeleton, StatePill } from "../../components/ui";
import { bytes, clock, duration, errorDetail, stagePercent } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useCrumbs, useShortcuts } from "../../shell/hooks";
import { StartPipelineDrawer } from "../datasets/StartPipelineDrawer";
import { failedStage, jobSeconds, runningStage, suggestedBatch, trainingModel, trainingPosition, valAccuracy, waitingFor } from "../job/jobs";

type JobList = Schemas["JobList"];
type View = "all" | "active" | "failed" | "completed" | "cancelled";

const ACTIVE_STATES: JobState[] = ["queued", "running", "stalled", "paused"];
const FINISHED_STATES: JobState[] = ["completed", "failed", "cancelled"];
const PAGE = 50;
const VIEWS: { view: View; label: string; states: JobState[] }[] = [
  { view: "all", label: "All", states: [...ACTIVE_STATES, ...FINISHED_STATES] },
  { view: "active", label: "Active", states: ACTIVE_STATES },
  { view: "failed", label: "Failed", states: ["failed"] },
  { view: "completed", label: "Completed", states: ["completed"] },
  { view: "cancelled", label: "Cancelled", states: ["cancelled"] },
];

interface Filters {
  view: View;
  dataset: string | null;
  kind: string | null;
  q: string | null;
}

function useFilters(): [Filters, (patch: Partial<Filters>) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("state");
  const filters: Filters = {
    view: VIEWS.some((v) => v.view === raw) ? (raw as View) : "all",
    dataset: params.get("dataset"),
    kind: params.get("kind"),
    q: params.get("q"),
  };
  const set = useCallback(
    (patch: Partial<Filters>) => {
      setParams((p) => {
        const next = new URLSearchParams(p);
        for (const [key, value] of Object.entries(patch)) {
          const name = key === "view" ? "state" : key;
          if (value === null || value === "" || (key === "view" && value === "all")) next.delete(name);
          else next.set(name, value);
        }
        return next;
      }, { replace: true });
    },
    [setParams],
  );
  return [filters, set];
}

function query(filters: Filters, states: JobState[], cursor?: string | null) {
  return call(() =>
    api.GET("/api/v1/jobs", {
      params: {
        query: {
          state: states.join(","),
          limit: PAGE,
          ...(filters.dataset ? { dataset: filters.dataset } : {}),
          ...(filters.kind ? { kind: filters.kind } : {}),
          ...(filters.q ? { q: filters.q } : {}),
          ...(cursor ? { cursor } : {}),
        },
      },
    }),
  );
}

/** Active jobs (all of them, pinned above) and finished jobs (paged, newest first). */
function useJobLists(filters: Filters) {
  const key = [filters.dataset, filters.kind, filters.q];
  const showActive = filters.view === "all" || filters.view === "active";
  const finishedStates = filters.view === "all" ? FINISHED_STATES : (VIEWS.find((v) => v.view === filters.view)?.states ?? []);
  const active = useQuery({
    queryKey: ["jobs", "list", "active", ...key],
    queryFn: () => query(filters, ACTIVE_STATES),
    enabled: showActive,
  });
  const finished = useInfiniteQuery({
    queryKey: ["jobs", "list", "finished", finishedStates.join(","), ...key],
    queryFn: ({ pageParam }) => query(filters, finishedStates, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: filters.view !== "active",
  });
  return { active: showActive ? active : null, finished: filters.view !== "active" ? finished : null };
}

interface Context {
  active: Job[];
  now: number;
  /** The GPU's temperature now, for jobs paused for heat (RUN-1.6). */
  gpuTemp: number | null;
  resumeBelow: number | null;
}

function Now({ job, context }: { job: Job; context: Context }) {
  const stage = runningStage(job);
  const training = job.kind === "train";
  const position = training ? trainingPosition(stage) : null;
  switch (job.state) {
    case "running": {
      if (job.desired_state === "paused") {
        return (
          <span className="now why">
            {training && stage?.step ? `stopping after step ${String(position?.step ?? stage.step)} · writing a checkpoint` : "stopping at the next safe point"}
          </span>
        );
      }
      const p = stagePercent(stage?.step, stage?.total);
      return (
        <span className="now">
          <span className="stg">
            {position?.epoch && position.epochs ? `epoch ${String(position.epoch)}/${String(position.epochs)}` : (stage?.name ?? "starting")}
          </span>
          {p !== null ? (
            <>
              <Progress value={p} state={job.state} />
              <span className="num">{Math.floor(p)}%</span>
            </>
          ) : null}
          {stage?.eta_seconds ? (
            <>
              · ETA <span className="num">{duration(stage.eta_seconds)}</span>
            </>
          ) : null}
        </span>
      );
    }
    case "stalled": {
      const since = stage?.last_progress_at ? (context.now - new Date(stage.last_progress_at).getTime()) / 1000 : null;
      return (
        <span className="now why">
          no progress{since !== null ? <> for <b>{duration(since)}</b></> : null}
        </span>
      );
    }
    case "queued":
      return <span className="now muted">{waitingFor(job, context.active)}</span>;
    case "paused":
      if (job.pause_reason === "thermal") {
        return (
          <span className="now why">
            heat · resumes below {context.resumeBelow !== null ? `${String(context.resumeBelow)} °C` : "the limit"}
            {context.gpuTemp !== null ? <> (<b>{Math.round(context.gpuTemp)} °C</b> now)</> : null}
          </span>
        );
      }
      return training ? (
        <span className="now why">
          by you{position?.epoch ? ` at epoch ${String(position.epoch)}` : ""} · checkpoint kept
        </span>
      ) : (
        <span className="now why">
          by you in <span className="stg">{stage?.name ?? "–"}</span>
        </span>
      );
    case "failed": {
      const where = failedStage(job);
      const why = job.error?.reason ?? errorDetail(job.error?.detail);
      return (
        <span className="now err" title={why ?? undefined}>
          <span className="code">{job.error?.code ?? "failed"}</span>
          {where ? (
            <>
              {" "}in <span className="stg">{where.name}</span>
            </>
          ) : null}
          {why ? <span className="why">· {why}</span> : null}
        </span>
      );
    }
    case "cancelled": {
      const where = job.stages.find((s) => s.status === "cancelled") ?? job.stages.find((s) => s.status !== "completed");
      const kept = job.stages.filter((s) => s.status === "completed" && s.output_version_id).length;
      return (
        <span className="now muted">
          cancelled{where ? <> in <span className="stg">{where.name}</span></> : null} · {kept} {kept === 1 ? "version" : "versions"} kept
        </span>
      );
    }
    case "completed":
      if (job.kind === "report") {
        const version = job.stages[0]?.input_version_id ?? job.output_version_id;
        return (
          <span className="now">
            <Icon name="report" />
            report for <span className="mono">{version ?? "–"}</span>
          </span>
        );
      }
      if (training) {
        const accuracy = valAccuracy(job);
        return (
          <span className="now why">
            {job.model_version_id && job.model_lineage_id ? (
              <>
                <Icon name="chevron" />
                <Link
                  className="mono"
                  to={`/models/${job.model_lineage_id}?version=${job.model_version_id}`}
                  onClick={(event) => { event.stopPropagation(); }}
                >
                  {job.model_version_id}
                </Link>{" "}
                ·{" "}
              </>
            ) : null}
            {accuracy !== null ? <>val acc <b>{(100 * accuracy).toFixed(1)}%</b></> : "trained"}
          </span>
        );
      }
      if (job.kind === "retention") return <span className="now">{retentionOutcome(job)}</span>;
      return job.output_version_id ? (
        <span className="now">
          <Icon name="chevron" />
          <span className="mono">{job.output_version_id}</span>
        </span>
      ) : (
        <span className="now muted">no new version</span>
      );
  }
}

/** "retention · freed 412 MB from 3 versions" (SCR-07 v2 frame A); a dry run says what it
 * would have deleted. */
export function retentionOutcome(job: Job): string {
  const m = job.stages[0]?.metrics ?? {};
  const n = (key: string) => (typeof m[key] === "number" ? m[key] : 0);
  if (n("dry_run")) return `retention dry run · would delete ${String(n("would_delete"))} versions, ${bytes(n("bytes_to_free"))}`;
  const versions = n("versions_deleted");
  const checkpoints = n("checkpoints_removed");
  if (!versions && !checkpoints && !n("bytes_freed")) return "retention · nothing to delete";
  const parts = [`${String(versions)} version${versions === 1 ? "" : "s"}`];
  if (checkpoints) parts.push(`${String(checkpoints)} checkpoint${checkpoints === 1 ? "" : "s"}`);
  return `retention · freed ${bytes(n("bytes_freed"))} from ${parts.join(" and ")}`;
}

/** The time a job was made: today as "14:03", earlier as "Nov 14". */
function created(iso: string, now: number): string {
  const d = new Date(iso);
  return new Date(now).toDateString() === d.toDateString()
    ? clock(iso).slice(0, 5)
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function CancelDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["jobs"] });
      onClose();
    },
  });
  const stage = runningStage(job);
  const kept = job.stages.filter((s) => s.status === "completed").map((s) => s.name);
  const started = job.state !== "queued";
  return (
    <Modal
      open
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={`Cancel ${job.id}?`}
      icon={<Icon name="alert" style={{ color: "var(--danger)" }} />}
      footer={
        <>
          <ModalClose asChild>
            <Button>{started ? "Keep running" : "Keep it queued"}</Button>
          </ModalClose>
          <Button variant="danger" disabled={cancel.isPending} onClick={() => { cancel.mutate(); }}>
            <Icon name="ban" />
            Cancel job
          </Button>
        </>
      }
    >
      {started ? (
        <>
          The <b>{stage?.name ?? "current"}</b> stage stops at its next safe point. <b>Kept</b>{" "}
          {kept.length ? `the versions made by ${kept.join(", ")}, and cached results.` : "cached results."}
        </>
      ) : (
        <>
          It has not started: <b>nothing is lost</b> and nothing was made.
        </>
      )}
      {cancel.error instanceof ApiError ? (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {cancel.error.problem.code}: {cancel.error.problem.detail}
        </p>
      ) : null}
    </Modal>
  );
}

function RetryToast({ job, onClose }: { job: Job; onClose: () => void }) {
  const navigate = useNavigate();
  useEffect(() => {
    const timer = window.setTimeout(onClose, 8000);
    return () => { window.clearTimeout(timer); };
  }, [onClose]);
  return (
    <div className="toasts">
      <div className="toast" role="status" style={{ "--c": "var(--info)" } as CSSProperties}>
        <Icon name="refresh" />
        <div>
          <b>
            {/* The worker counts an attempt when it starts one, so the queued job's is the next. */}
            <span className="mono">{job.id}</span> queued again · attempt {job.attempt + 1}
          </b>
          <span>Its finished stages are reused.</span>
          <div style={{ marginTop: "var(--sp-2)" }}>
            <Button size="sm" variant="ghost" onClick={() => void navigate(`/jobs/${job.id}`)}>
              Open
              <Icon name="chevron" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Frame B: a job reached its safe point and paused. Training names the step and keeps its
 * checkpoint; a pipeline names the stage it stopped in. */
function PausedToast({ job, onResume, onClose }: { job: Job; onResume: () => void; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 10000);
    return () => { window.clearTimeout(timer); };
  }, [onClose]);
  const stage = runningStage(job);
  const position = job.kind === "train" ? trainingPosition(stage) : null;
  const where = position?.epoch
    ? `at epoch ${String(position.epoch)}${position.step ? `, step ${String(position.step)}` : ""}`
    : `in ${stage?.name ?? "its stage"}`;
  return (
    <div className="toasts">
      <div className="toast" role="status" style={{ "--c": "var(--state-paused)" } as CSSProperties}>
        <Icon name="pause" />
        <div>
          <b>
            <span className="mono">{job.id}</span> paused {where}
            {job.pause_reason === "thermal" ? " · the GPU is too hot" : ""}
          </b>
          <span>
            {job.kind === "train"
              ? "The checkpoint is kept until you resume or cancel."
              : "Its finished work is kept; resuming continues from there."}
          </span>
          <div style={{ marginTop: "var(--sp-2)" }}>
            <Button size="sm" variant="primary" aria-label={`Resume ${job.id}`} onClick={onResume}>
              <Icon name="play" />
              Resume
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** A running job asked to pause, until it reaches its safe point (frame B). */
function pausing(job: Job): boolean {
  return job.state === "running" && job.desired_state === "paused";
}

function pillLabel(job: Job): string | undefined {
  if (job.state === "completed" || job.state === "failed" || job.state === "cancelled") return undefined;
  if (job.desired_state === "cancelled") return "Cancelling…";
  return pausing(job) ? "Pausing…" : undefined;
}

function plural(n: number): string {
  return `${String(n)} ${n === 1 ? "job" : "jobs"}`;
}

export function JobsScreen() {
  useCrumbs([{ label: "Jobs" }]);
  const navigate = useNavigate();
  const client = useQueryClient();
  const now = useNow(1000);
  const [filters, setFilters] = useFilters();
  const { active, finished } = useJobLists(filters);
  const datasets = useDatasets();
  const [drawer, setDrawer] = useState(false);
  const [cancelling, setCancelling] = useState<Job | null>(null);
  const [retried, setRetried] = useState<Job | null>(null);
  const [paused, setPaused] = useState<Job | null>(null);
  const [announce, setAnnounce] = useState("");
  const system = useSystem();
  const gpuTemp = useGpuTemperature();
  const [selected, setSelected] = useState(0);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);
  const search = useRef<HTMLInputElement>(null);
  const sentinel = useRef<HTMLTableRowElement>(null);

  const activeJobs = useMemo(() => active?.data?.items ?? [], [active?.data]);
  const finishedJobs = useMemo(() => finished?.data?.pages.flatMap((p) => p.items) ?? [], [finished?.data]);
  const all = useMemo(() => [...activeJobs, ...finishedJobs], [activeJobs, finishedJobs]);
  const counts = (finished?.data?.pages[0] ?? active?.data)?.counts ?? null;
  const viewCount = (v: (typeof VIEWS)[number]) => (counts ? v.states.reduce((a, s) => a + (counts[s] ?? 0), 0) : null);
  const current = VIEWS.find((v) => v.view === filters.view) ?? VIEWS[0];
  const total = current ? viewCount(current) : null;

  // Live: a job message updates its row in place; a new job or a change of state reloads
  // the lists (rows move between Active and Finished) and is announced once.
  const onJob = useCallback(
    (job: Job) => {
      const lists = client.getQueriesData<JobList | InfiniteData<JobList>>({ queryKey: ["jobs", "list"] });
      const known = lists
        .flatMap(([, data]) => (data ? ("pages" in data ? data.pages.flatMap((p) => p.items) : data.items) : []))
        .find((j) => j.id === job.id);
      if (known && known.state === job.state) {
        client.setQueriesData<JobList>({ queryKey: ["jobs", "list", "active"] }, (data) =>
          data ? { ...data, items: data.items.map((j) => (j.id === job.id ? job : j)) } : data,
        );
        return;
      }
      if (known) setAnnounce(`${job.id} ${job.state}`);
      if (known && job.state === "paused") setPaused(job);
      void client.invalidateQueries({ queryKey: ["jobs", "list"] });
    },
    [client],
  );
  useFeedMessage("job", onJob);

  const retry = useMutation({
    mutationFn: (id: string) => call(() => api.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: id } } })),
    onSuccess: (job) => {
      setRetried(job);
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  // Pause and resume (RUN-1.2). A pause shows "Pausing…" until the job reaches its safe point.
  const pause = useMutation({
    mutationFn: (id: string) => call(() => api.POST("/api/v1/jobs/{job_id}/pause", { params: { path: { job_id: id } } })),
    onSettled: () => { void client.invalidateQueries({ queryKey: ["jobs"] }); },
  });
  const resume = useMutation({
    mutationFn: (id: string) => call(() => api.POST("/api/v1/jobs/{job_id}/resume", { params: { path: { job_id: id } } })),
    onSuccess: () => { setPaused(null); },
    onSettled: () => { void client.invalidateQueries({ queryKey: ["jobs"] }); },
  });
  const actionError = [retry, pause, resume].find((m) => m.error instanceof ApiError) ?? null;

  // Older finished jobs load as the end of the table scrolls into view.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = finished ?? {};
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage?.();
    });
    observer.observe(el);
    return () => { observer.disconnect(); };
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const move = useCallback(
    (delta: number) => {
      setSelected((i) => {
        const next = Math.max(0, Math.min(all.length - 1, i + delta));
        rows.current[next]?.focus();
        return next;
      });
    },
    [all.length],
  );
  const bindings = useMemo(
    () => ({
      "/": () => search.current?.focus(),
      j: () => { move(1); },
      k: () => { move(-1); },
    }),
    [move],
  );
  useShortcuts(bindings);

  const filtered = filters.dataset !== null || filters.kind !== null || filters.q !== null || filters.view !== "all";
  const pending = (active?.isPending ?? false) || (finished?.isPending ?? false);
  const error = active?.error ?? finished?.error ?? null;
  const context: Context = { active: activeJobs, now, gpuTemp, resumeBelow: system.data?.thermal_resume_c ?? null };

  const row = (job: Job, i: number) => (
    <tr
      key={job.id}
      ref={(el) => { rows.current[i] = el; }}
      tabIndex={i === selected ? 0 : -1}
      aria-selected={i === selected}
      onFocus={() => { setSelected(i); }}
      onClick={() => void navigate(`/jobs/${job.id}`)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) void navigate(`/jobs/${job.id}`);
      }}
    >
      <td className="id">
        <Link to={`/jobs/${job.id}`} tabIndex={-1} onClick={(e) => { e.stopPropagation(); }} style={{ color: "inherit", textDecoration: "none" }}>
          {job.id}
        </Link>
      </td>
      <td>
        <StatePill state={pausing(job) ? "paused" : job.state} label={pillLabel(job)} />
      </td>
      {trainingModel(job) ? (
        <td className="m2 col-wide">
          <b>{trainingModel(job)}</b> on {job.dataset ?? "—"}
        </td>
      ) : (
        <td className="id col-wide">{job.dataset ?? "—"}</td>
      )}
      <td>
        <Now job={job} context={context} />
      </td>
      <td className="r col-wide">{job.attempt}</td>
      <td className="col-wide">{created(job.created_at, now)}</td>
      <td className="r">{duration(jobSeconds(job, now))}</td>
      <td className="acts-cell">
        <span className="acts">
          {job.state === "running" && job.desired_state !== "cancelled" ? (
            <Button
              size="sm"
              title={`Pause ${job.id}`}
              aria-label={`Pause ${job.id}`}
              disabled={job.desired_state === "paused" || (pause.isPending && pause.variables === job.id)}
              onClick={(e) => { e.stopPropagation(); pause.mutate(job.id); }}
            >
              <Icon name="pause" />
              <span className="lbl">Pause</span>
            </Button>
          ) : null}
          {job.state === "paused" ? (
            <Button
              size="sm"
              variant="primary"
              title={`Resume ${job.id}`}
              aria-label={`Resume ${job.id}`}
              disabled={resume.isPending && resume.variables === job.id}
              onClick={(e) => { e.stopPropagation(); resume.mutate(job.id); }}
            >
              <Icon name="play" />
              <span className="lbl">Resume</span>
            </Button>
          ) : null}
          {ACTIVE_STATES.includes(job.state) && job.desired_state !== "cancelled" ? (
            <Button size="sm" variant="danger" aria-label={`Cancel ${job.id}`} onClick={(e) => { e.stopPropagation(); setCancelling(job); }}>
              <Icon name="ban" />
              Cancel
            </Button>
          ) : null}
          {job.state === "failed" && suggestedBatch(job) !== null ? (
            <Link
              className="btn sm"
              to={`/runs/new?from=${job.id}&batch_size=${String(suggestedBatch(job))}`}
              onClick={(e) => { e.stopPropagation(); }}
            >
              <Icon name="refresh" />
              Start again with {String(suggestedBatch(job))}
            </Link>
          ) : job.state === "failed" ? (
            <Button
              size="sm"
              aria-label={`Retry ${job.id}`}
              disabled={retry.isPending && retry.variables === job.id}
              onClick={(e) => { e.stopPropagation(); retry.mutate(job.id); }}
            >
              <Icon name="refresh" />
              Retry
            </Button>
          ) : null}
        </span>
      </td>
    </tr>
  );

  let body;
  if (pending) {
    body = (
      <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        <Skeleton width={220} height={22} />
        {[0, 1, 2].map((i) => <span key={i} className="skeleton" style={{ height: 28, display: "block" }} />)}
      </div>
    );
  } else if (error) {
    body = (
      <ErrorState
        error={error}
        title="Jobs could not be loaded"
        onRetry={() => { void client.invalidateQueries({ queryKey: ["jobs", "list"] }); }}
      />
    );
  } else if (all.length === 0 && !filtered) {
    body = (
      <EmptyState
        icon="list"
        title="No jobs yet"
        action={
          <Button size="sm" variant="primary" onClick={() => void navigate("/datasets")}>
            <Icon name="db" />
            Go to datasets
          </Button>
        }
      >
        Jobs appear here when you ingest a folder or continue from a version.
      </EmptyState>
    );
  } else if (all.length === 0) {
    body = (
      <EmptyState
        icon="list"
        title={`No ${filters.view === "all" ? "" : `${current?.label.toLowerCase() ?? ""} `}jobs${filters.dataset ? ` for ${filters.dataset}` : ""}${filters.q ? ` matching “${filters.q}”` : ""}`}
        action={
          <Button size="sm" onClick={() => { setFilters({ view: "all", dataset: null, kind: null, q: null }); if (search.current) search.current.value = ""; }}>
            <Icon name="x" />
            Clear filters
          </Button>
        }
      />
    );
  } else {
    const sections = filters.view === "all";
    body = (
      <>
        <div className="table-wrap">
          <table className="t" aria-label="Jobs">
            <thead>
              <tr>
                <th>Job</th>
                <th>State</th>
                <th className="col-wide">Dataset</th>
                <th>Now / outcome</th>
                <th className="r col-wide">Attempt</th>
                <th className="col-wide" aria-sort="descending">
                  Created <Icon name="sort" style={{ width: 10, height: 10 }} />
                </th>
                <th className="r">Duration</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sections && activeJobs.length ? (
                <tr className="sec">
                  <td colSpan={8}>
                    Active<span className="count">{activeJobs.length}</span>
                  </td>
                </tr>
              ) : null}
              {activeJobs.map((job, i) => row(job, i))}
              {sections && finishedJobs.length ? (
                <tr className="sec">
                  <td colSpan={8}>
                    Finished<span className="count">{counts ? FINISHED_STATES.reduce((a, s) => a + (counts[s] ?? 0), 0) : finishedJobs.length}</span>
                  </td>
                </tr>
              ) : null}
              {finishedJobs.map((job, i) => row(job, activeJobs.length + i))}
              {hasNextPage ? (
                <tr ref={sentinel} className="sec">
                  <td colSpan={8}>{isFetchingNextPage ? "Loading older jobs…" : "Older jobs"}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <p className="cap">
          Showing {all.length} of {total ?? all.length}
          {hasNextPage ? (
            <>
              {" "}· older jobs load as you scroll{" "}
              <Button size="sm" variant="ghost" disabled={isFetchingNextPage} onClick={() => void fetchNextPage?.()}>
                Load more
              </Button>
            </>
          ) : null}
        </p>
      </>
    );
  }

  return (
    <div className="scr07">
      <div className="phead">
        <h1>Jobs</h1>
        <span className="spacer" />
        <Button onClick={() => { setDrawer(true); }}>
          <Icon name="branch" />
          Start a pipeline…
        </Button>
      </div>
      <div className="toolbar">
        <span className="group-btns" role="group" aria-label="State">
          {VIEWS.map((v) => {
            const n = viewCount(v);
            return (
              <button
                key={v.view}
                type="button"
                className="btn sm"
                aria-pressed={filters.view === v.view}
                aria-label={n === null ? v.label : `${v.label}, ${plural(n)}`}
                onClick={() => { setFilters({ view: v.view }); setSelected(0); }}
              >
                {v.label}
                {n !== null ? <span className="count">{n}</span> : null}
              </button>
            );
          })}
        </span>
        <select
          className="select"
          aria-label="Dataset"
          value={filters.dataset ?? ""}
          onChange={(e) => { setFilters({ dataset: e.target.value || null }); }}
        >
          <option value="">Dataset: any</option>
          {(datasets.data?.items ?? []).map((d) => (
            <option key={d.slug} value={d.slug}>
              Dataset: {d.slug}
            </option>
          ))}
        </select>
        <select className="select" aria-label="Kind" value={filters.kind ?? ""} onChange={(e) => { setFilters({ kind: e.target.value || null }); }}>
          <option value="">Kind: any</option>
          <option value="pipeline">Kind: pipeline</option>
          <option value="report">Kind: report</option>
          <option value="train">Kind: train</option>
          <option value="retention">Kind: retention</option>
        </select>
        <span className="spacer" />
        <span className="search">
          <Icon name="search" />
          <input
            ref={search}
            className="input"
            placeholder="Job ID"
            aria-label="Filter by job ID"
            defaultValue={filters.q ?? ""}
            onKeyDown={(e) => {
              if (e.key === "Enter") setFilters({ q: e.currentTarget.value.trim() || null });
              if (e.key === "Escape") e.currentTarget.blur();
            }}
            onBlur={(e) => {
              const q = e.currentTarget.value.trim() || null;
              if (q !== filters.q) setFilters({ q });
            }}
          />
          <Kbd>/</Kbd>
        </span>
      </div>
      {body}
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
      {cancelling ? <CancelDialog job={cancelling} onClose={() => { setCancelling(null); }} /> : null}
      {retried ? <RetryToast job={retried} onClose={() => { setRetried(null); }} /> : null}
      {paused ? (
        <PausedToast
          job={paused}
          onResume={() => { resume.mutate(paused.id); }}
          onClose={() => { setPaused(null); }}
        />
      ) : null}
      {actionError?.error instanceof ApiError ? (
        <div className="toasts">
          <div className="toast" role="alert" style={{ "--c": "var(--danger)" } as CSSProperties}>
            <Icon name="alert" />
            <div>
              <b>
                {actionError === retry ? "Retry" : actionError === pause ? "Pause" : "Resume"} of {actionError.variables} failed
              </b>
              <span>
                {actionError.error.problem.code}: {actionError.error.problem.detail}
              </span>
            </div>
          </div>
        </div>
      ) : null}
      {drawer ? <StartPipelineDrawer open from={{ mode: "folder" }} onOpenChange={(o) => { if (!o) setDrawer(false); }} /> : null}
    </div>
  );
}
