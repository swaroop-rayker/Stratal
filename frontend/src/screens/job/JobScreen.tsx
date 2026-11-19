// SCR-04 live job view (SCREENS.md §3; mockup SCR-04_v1; MON-4.1, MON-4.2, MON-4.3,
// MON-1.1–1.5, MON-2.1, 2.4, 2.5; RUN-1.1/1.2 slice).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Job, type StreamEvent, type StreamSample } from "../../api/client";
import { useGpuTemperature } from "../../api/queries";
import { useJobFeed } from "../../api/stream";
import { Modal, ModalClose } from "../../components/Modal";
import { copy, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Progress, Skeleton, StatePill } from "../../components/ui";
import { clock, count, duration, errorDetail, gb } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useCrumbs } from "../../shell/hooks";
import { useActiveJobs } from "../../shell/JobIndicator";
import { ActivityLog, type Level, type LogFilters } from "./ActivityLog";
import { ACTIVE, failedStage, inputVersion, jobSeconds, liveProgress, runningStage, stageSeconds, suggestedBatch, trainingModel, trainingPosition, waitingFor } from "./jobs";
import { type Point, ResourceTiles } from "./ResourceTiles";
import { StageDrawer } from "./StageDrawer";
import { Timeline } from "./Timeline";
import { BenchPanel, deriveTraining, Epochs, StateBanner, type TrainingData, TrainingCharts, useTrainingEvents } from "./Training";

const POINTS = 240;

function useJob(id: string) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id } } })),
    retry: false,
  });
}

function Header({ job, now, onCancel, device }: { job: Job; now: number; onCancel: () => void; device: string | null }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: job.id } } })),
    onSuccess: (next) => {
      client.setQueryData(["jobs", job.id], next);
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  // Pause and resume (RUN-1.2): Pause is secondary beside Cancel; Resume is the primary
  // action of a paused job (SCR-04 v2).
  const pause = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/pause", { params: { path: { job_id: job.id } } })),
    onSuccess: (next) => { client.setQueryData(["jobs", job.id], next); },
  });
  const resume = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/resume", { params: { path: { job_id: job.id } } })),
    onSuccess: (next) => {
      client.setQueryData(["jobs", job.id], next);
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const cancelling = job.desired_state === "cancelled" && (ACTIVE.has(job.state) || job.state === "paused");
  const pausing = job.desired_state === "paused" && job.state === "running";
  const input = inputVersion(job);
  const seconds = jobSeconds(job, now);
  const sub =
    job.state === "queued"
      ? `created ${clock(job.created_at)} · waiting ${duration((now - new Date(job.created_at).getTime()) / 1000)}`
      : `attempt ${String(job.attempt)} · ${job.finished_at ? `ran ${duration(seconds)} · finished ${clock(job.finished_at)}` : `started ${clock(job.started_at)} · ${duration(seconds)}`}`;
  const model = trainingModel(job);
  const error = [retry, pause, resume].find((m) => m.error instanceof ApiError)?.error;
  return (
    <div className="jhead">
      <h1>{job.id}</h1>
      {cancelling ? (
        <span className="pill cancelled lg" style={{ textDecoration: "none" }} role="status">
          <span className="spinner" />
          Cancelling…
        </span>
      ) : pausing ? (
        <span className="pill paused lg" role="status">
          <Icon name="pause" />
          Pausing…
        </span>
      ) : (
        <span role={job.state === "failed" ? "alert" : "status"} style={{ display: "inline-flex" }}>
          <StatePill state={job.state} size="lg" />
        </span>
      )}
      {model ? (
        <span className="sub">
          train · <span className="mono">{model}</span>
          {input ? (
            <>
              {" on "}
              <span className="mono">{input}</span>
            </>
          ) : null}
          {device ? (
            <>
              {" · "}
              <span className="why">{device}</span>
            </>
          ) : null}{" "}
          · {sub}
        </span>
      ) : (
        <span className="sub">
          {job.kind} · {job.dataset ?? "–"}
          {input ? (
            <>
              {" · from "}
              <span className="mono">{input}</span>
            </>
          ) : null}{" "}
          · {sub}
        </span>
      )}
      <span className="spacer" />
      <span className="wide-action">
        <Button size="sm" variant="ghost" onClick={() => void copy(job.id)}>
          <Icon name="copy" />
          Copy ID
        </Button>
      </span>
      {job.state === "running" && job.desired_state !== "cancelled" ? (
        <Button disabled={pausing || pause.isPending} onClick={() => { pause.mutate(); }}>
          <Icon name="pause" />
          Pause
        </Button>
      ) : null}
      {job.state === "paused" ? (
        <Button variant="primary" disabled={resume.isPending} onClick={() => { resume.mutate(); }}>
          <Icon name="play" />
          {job.pause_reason === "thermal" ? "Resume now" : "Resume"}
        </Button>
      ) : null}
      {ACTIVE.has(job.state) || job.state === "paused" ? (
        <Button variant="danger" onClick={onCancel} disabled={cancelling}>
          <Icon name="ban" />
          Cancel job
        </Button>
      ) : null}
      {job.state === "failed" || job.state === "cancelled" ? (
        <>
          {input ? (
            <Button onClick={() => void navigate(`/versions/${input}`)}>
              <Icon name="db" />
              Open input version
            </Button>
          ) : null}
          {job.state === "failed" ? (
            <Button variant="primary" disabled={retry.isPending} onClick={() => { retry.mutate(); }}>
              <Icon name="refresh" />
              {retry.isPending ? "Retrying…" : "Retry"}
            </Button>
          ) : null}
        </>
      ) : null}
      {job.state === "completed" && job.output_version_id ? (
        <Button variant="primary" onClick={() => void navigate(`/versions/${job.output_version_id ?? ""}`)}>
          <Icon name="db" />
          Open {job.output_version_id}
        </Button>
      ) : null}
      {job.state === "completed" && job.model_version_id && job.model_lineage_id ? (
        <Button
          variant="primary"
          onClick={() => void navigate(`/models/${job.model_lineage_id ?? ""}?version=${job.model_version_id ?? ""}`)}
        >
          <Icon name="grid" />
          Open {job.model_version_id}
        </Button>
      ) : null}
      {error instanceof ApiError ? <Badge tone="err">{error.problem.code}</Badge> : null}
    </div>
  );
}

function Failure({ job, onOpenStage }: { job: Job; onOpenStage: (name: string) => void }) {
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: job.id } } })),
    onSuccess: (next) => {
      client.setQueryData(["jobs", job.id], next);
    },
  });
  const stage = failedStage(job);
  const error = job.error ?? stage?.error ?? null;
  const kept = job.stages.filter((s) => s.status === "completed" && s.output_version_id);
  return (
    <div className="failure" role="alert">
      <Icon name="alert" />
      <div style={{ flex: 1 }}>
        <h2>
          {stage ? `${stage.name} failed` : "The job failed"}
          {error?.reason ? `: ${error.reason}` : ""} {error ? <Badge tone="err">{error.code}</Badge> : null}
        </h2>
        {errorDetail(error?.detail) ? <p>{errorDetail(error?.detail)}</p> : null}
        <p>
          {error?.suggested_action ? (
            <>
              <b>Do</b> {error.suggested_action}{" "}
            </>
          ) : null}
          <b>Kept</b>{" "}
          {kept.length
            ? `the versions made by ${kept.map((s) => s.name).join(", ")}; a retry reuses them and any cached results.`
            : "nothing yet; a retry starts the failed stage again, reusing any cached results."}
        </p>
        <div style={{ display: "flex", gap: "var(--sp-2)", marginTop: "var(--sp-2)" }}>
          {suggestedBatch(job) !== null ? (
            <Link className="btn sm primary" to={`/runs/new?from=${job.id}&batch_size=${String(suggestedBatch(job))}`}>
              <Icon name="refresh" />
              Start again with {String(suggestedBatch(job))}
            </Link>
          ) : null}
          <Button size="sm" disabled={retry.isPending} onClick={() => { retry.mutate(); }}>
            <Icon name="refresh" />
            Retry
          </Button>
          {stage ? (
            <Button size="sm" variant="ghost" onClick={() => { onOpenStage(stage.name); }}>
              Open the failed stage
              <Icon name="chevron" />
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Summary({ job, now }: { job: Job; now: number }) {
  const peak = (k: string) => Math.max(0, ...job.stages.map((s) => Number(s.peaks?.[k] ?? 0)));
  return (
    <div className="table-wrap summary-wrap">
      <table className="t" aria-label="Summary">
        <thead>
          <tr>
            <th>Stage</th>
            <th className="r">Duration</th>
            <th className="r">Steps</th>
            <th className="r">Rate</th>
            <th className="r">Peak RAM</th>
            <th className="r">Peak VRAM</th>
            <th className="r">Max temp</th>
            <th>Output</th>
          </tr>
        </thead>
        <tbody>
          {job.stages.map((s) => {
            const p = (s.peaks ?? {}) as Record<string, number | undefined>;
            return (
              <tr key={s.idx}>
                <td>
                  {s.name}
                  {s.cache_hit ? <span className="muted"> · cached</span> : null}
                </td>
                <td className="r">{duration(stageSeconds(s, now))}</td>
                <td className="r">{count(s.total)}</td>
                <td className="r">{s.throughput !== null ? `${s.throughput.toFixed(1)}/s` : "—"}</td>
                <td className="r">{p.ram_used_mb !== undefined ? `${gb(p.ram_used_mb)} GB` : "—"}</td>
                <td className="r">{s.queue === "gpu" && p.vram_used_mb !== undefined ? `${gb(p.vram_used_mb)} GB` : "—"}</td>
                <td className="r">{p.gpu_temp_c !== undefined ? `${String(Math.round(p.gpu_temp_c))} °C` : "—"}</td>
                <td className="id">{s.output_version_id ? <Link to={`/versions/${s.output_version_id}`}>{s.output_version_id}</Link> : "–"}</td>
              </tr>
            );
          })}
          <tr className="total">
            <td>job</td>
            <td className="r">{duration(jobSeconds(job, now))}</td>
            <td className="r" />
            <td className="r" />
            <td className="r">{gb(peak("ram_used_mb"))} GB</td>
            <td className="r">{peak("vram_used_mb") ? `${gb(peak("vram_used_mb"))} GB` : "—"}</td>
            <td className="r">{peak("gpu_temp_c") ? `${String(Math.round(peak("gpu_temp_c")))} °C` : "—"}</td>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function CancelDialog({ job, open, onOpenChange }: { job: Job; open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } } })),
    onSuccess: (next) => {
      client.setQueryData(["jobs", job.id], next);
      onOpenChange(false);
    },
  });
  const stage = runningStage(job);
  const kept = job.stages.filter((s) => s.status === "completed").map((s) => s.name);
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={`Cancel ${job.id}?`}
      icon={<Icon name="alert" style={{ color: "var(--danger)" }} />}
      footer={
        <>
          <ModalClose asChild>
            <Button>Keep running</Button>
          </ModalClose>
          <Button variant="danger" disabled={cancel.isPending} onClick={() => { cancel.mutate(); }}>
            <Icon name="ban" />
            Cancel job
          </Button>
        </>
      }
    >
      {stage && job.state === "running" ? (
        <>
          The <b>{stage.name}</b> stage stops at its next safe point.{" "}
        </>
      ) : (
        "The job will not start. "
      )}
      <b>Kept</b> {kept.length ? `the versions made by ${kept.join(", ")}, and cached results.` : "cached results."}{" "}
      <b>Lost</b> the rest of this run; a new run continues from the cache.
      {cancel.error instanceof ApiError ? (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {cancel.error.problem.code}: {cancel.error.problem.detail}
        </p>
      ) : null}
    </Modal>
  );
}

/** The header's device clause for a training run: what it trains on and, under `auto`, why
 * (RUN-1.9): "GPU, auto: 12 ms/step against 41 on the CPU". */
function deviceText(job: Job, data: TrainingData): string | null {
  const bench = data.bench;
  if (bench?.choice && bench.gpu !== null && bench.cpu !== null) {
    const [mine, other] = bench.choice === "gpu" ? [bench.gpu, bench.cpu] : [bench.cpu, bench.gpu];
    const name = bench.choice === "gpu" ? "GPU" : "CPU";
    return `${name}, auto: ${mine.toFixed(0)} ms/step against ${other.toFixed(0)} on the ${bench.choice === "gpu" ? "CPU" : "GPU"}`;
  }
  if (bench) return "device auto";
  const device = job.stages[0]?.metrics?.device;
  return device === "gpu" ? "GPU" : device === "cpu" ? "CPU" : null;
}

/** The training run's loader workers, from its setup event's metrics. */
function loaderWorkers(job: Job): number | null {
  const value = job.stages[0]?.metrics?.loader_workers;
  return typeof value === "number" ? value : null;
}

/** Region 3 for training: "epoch 7/20 · step 140/312 · 10,640 samples/s". */
function TrainingProgress({ job, data }: { job: Job; data: TrainingData }) {
  const stage = runningStage(job);
  const position = trainingPosition(stage);
  const perEpoch = stage?.total && position.epochs ? Math.round(stage.total / position.epochs) : null;
  const rate = data.steps[data.steps.length - 1]?.samplesPerS ?? null;
  return (
    <>
      {position.epoch && position.epochs ? `epoch ${String(position.epoch)}/${String(position.epochs)} · ` : ""}
      {position.step !== null && perEpoch ? `step ${String(position.step)}/${String(perEpoch)} · ` : ""}
      <b>{rate === null ? "—" : count(Math.round(rate))}</b> samples/s
    </>
  );
}

export function JobScreen() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const job = useJob(jobId);
  const active = useActiveJobs();
  const now = useNow();
  const [cancelOpen, setCancelOpen] = useState(false);
  const [latest, setLatest] = useState<StreamEvent | null>(null);
  const system = useQuery({ queryKey: ["system"], queryFn: () => call(() => api.GET("/api/v1/system")), staleTime: 300_000 });

  useCrumbs([{ label: "Jobs", to: "/jobs" }, { label: jobId, id: true }]);

  const selected = params.get("stage");
  const select = useCallback(
    (stage: string | null) => {
      setParams((p) => {
        const next = new URLSearchParams(p);
        if (stage) next.set("stage", stage);
        else next.delete("stage");
        return next;
      }, { replace: true });
    },
    [setParams],
  );
  const filters: LogFilters = {
    scope: params.get("log") === "all" ? "all" : "job",
    level: (params.get("level") as Level | null) ?? "INFO",
    stage: selected ?? params.get("logstage"),
    job: params.get("logjob"),
    text: params.get("q") ?? "",
  };
  const setFilters = (f: LogFilters) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      const put = (k: string, v: string | null, dflt: string | null = null) => {
        if (v === null || v === dflt || v === "") next.delete(k);
        else next.set(k, v);
      };
      put("log", f.scope, "job");
      put("level", f.level, "INFO");
      if (f.stage !== selected) {
        next.delete("stage");
        put("logstage", f.stage);
      }
      put("logjob", f.job);
      put("q", f.text);
      return next;
    }, { replace: true });
  };

  // History: the job's telemetry (thinned), then live samples from its stream.
  const telemetry = useQuery({
    queryKey: ["jobs", jobId, "telemetry", selected ?? "all"],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/jobs/{job_id}/telemetry", {
          params: { path: { job_id: jobId }, query: { max_points: POINTS, ...(selected ? { stage: selected } : {}) } },
        }),
      ),
    enabled: job.isSuccess,
  });
  const history = useMemo<Point[]>(() => {
    if (!telemetry.data) return [];
    const rss = telemetry.data.process;
    return telemetry.data.resource.map((r) => {
      const t = new Date(r.ts).getTime();
      const p = rss.reduce<(typeof rss)[number] | null>((best, x) => (Math.abs(new Date(x.ts).getTime() - t) < 2000 ? x : best), null);
      return { ...r, rss_mb: p?.rss_mb ?? null, torch_allocated_mb: p?.torch_allocated_mb ?? null, torch_reserved_mb: p?.torch_reserved_mb ?? null };
    });
  }, [telemetry.data]);
  // Live samples since the history was read; a new history (another stage) starts over.
  const [livePoints, setLivePoints] = useState<{ key: unknown; points: Point[] }>({ key: null, points: [] });
  const points = [...history, ...(livePoints.key === (selected ?? "all") ? livePoints.points : [])].slice(-POINTS);

  const live = job.data ? ACTIVE.has(job.data.state) : false;
  // A paused job keeps its feed, so a resume (by the person or the watchdog) shows at once.
  const watching = live || job.data?.state === "paused";
  const { feed, connection } = useJobFeed(jobId, undefined, job.isSuccess && watching);
  const isTraining = job.data?.kind === "train";
  const trainingEvents = useTrainingEvents(jobId, feed, job.isSuccess && isTraining);
  const training = useMemo(() => deriveTraining(trainingEvents), [trainingEvents]);
  const gpuTemp = useGpuTemperature();
  useEffect(() => {
    if (!feed) return;
    const offJob = feed.on("job", (next: Job) => {
      client.setQueryData(["jobs", jobId], next);
      if (!ACTIVE.has(next.state)) void client.invalidateQueries({ queryKey: ["jobs"] });
    });
    const offEvent = feed.on("event", (e: StreamEvent) => {
      if (e.step !== null || e.type === "activity" || e.type === "stage_progress") setLatest(e);
    });
    const offSample = feed.on("sample", (s: StreamSample) => {
      if (selected) return; // a selected stage shows its own history
      setLivePoints((lp) => ({ key: "all", points: [...(lp.key === "all" ? lp.points : []), s as Point].slice(-POINTS) }));
    });
    return () => {
      offJob();
      offEvent();
      offSample();
    };
  }, [feed, client, jobId, selected]);

  const onOpenStage = select;

  const stageNames = useMemo(() => job.data?.stages.map((s) => s.name) ?? [], [job.data]);

  if (job.isPending) {
    return (
      <div className="scr04" aria-busy="true">
        <Skeleton width={220} height={22} />
        <span className="skeleton" style={{ height: 46, display: "block" }} />
        <span className="skeleton" style={{ height: 8, display: "block" }} />
        <span className="skeleton" style={{ height: 40, display: "block" }} />
      </div>
    );
  }
  if (job.isError) {
    const notFound = job.error instanceof ApiError && job.error.problem.status === 404;
    return (
      <div className="scr04">
        {notFound ? (
          <div className="state err" role="alert">
            <Icon name="alert" />
            <b>Job {jobId} not found</b>
            <Badge tone="err">not_found</Badge>
            <p>
              No job &apos;{jobId}&apos;. <b style={{ color: "var(--text-primary)" }}>Do</b> open it from the jobs list.
            </p>
            <Button size="sm" onClick={() => void navigate("/jobs")}>
              <Icon name="list" />
              Go to jobs
            </Button>
          </div>
        ) : (
          <ErrorState error={job.error} title={`Job ${jobId} could not be loaded`} onRetry={() => void job.refetch()} />
        )}
      </div>
    );
  }

  const j = job.data;
  const stage = runningStage(j);
  const progress = liveProgress(stage, latest);
  const pct = progress.total ? Math.min(100, ((progress.step ?? 0) / progress.total) * 100) : null;
  const drawerStage = selected ? j.stages.find((s) => s.name === selected) ?? null : null;
  const reconnecting = live && connection === "reconnecting";
  const trains = j.kind === "train";
  const banner = trains && (j.state === "paused" || j.state === "stalled");
  const benchmarking = trains && j.state === "running" && training.bench !== null && training.bench.choice === null;

  return (
    <div className={live ? "scr04" : "scr04 done"}>
      <Header job={j} now={now} onCancel={() => { setCancelOpen(true); }} device={trains ? deviceText(j, training) : null} />

      {j.state === "failed" ? <Failure job={j} onOpenStage={onOpenStage} /> : null}

      {banner ? (
        <StateBanner
          job={j}
          data={training}
          now={now}
          gpuTemp={gpuTemp}
          limits={{ pause: system.data?.thermal_pause_c ?? null, resume: system.data?.thermal_resume_c ?? null }}
        />
      ) : null}

      {live && !banner ? (
        <>
          <div className="activity" aria-live="polite" style={j.state === "queued" ? ({ "--c": "var(--state-queued)" } as React.CSSProperties) : undefined}>
            {stage ? <span className="stage-tag">{stage.name}</span> : null}
            <span className="msg" style={j.state === "queued" ? { fontWeight: "var(--fw-regular)", color: "var(--text-secondary)" } : undefined}>
              {j.state === "queued" ? (
                waitingFor(j, active.data?.items ?? [])
              ) : j.desired_state === "cancelled" ? (
                "Stopping at the next safe point…"
              ) : (
                <>
                  <Icon name="play" />
                  {progress.activity ?? "Starting…"}
                </>
              )}
            </span>
            {reconnecting ? <span className="muted" style={{ marginLeft: "auto" }}>Reconnecting…</span> : null}
          </div>
          {benchmarking && training.bench ? <BenchPanel bench={training.bench} /> : null}
          {j.state !== "queued" && !benchmarking ? (
            <div className={trains ? "prog train" : "prog"}>
              <Progress value={pct} state={j.state} />
              <b className="r">{pct === null ? "—" : `${String(Math.round(pct))}%`}</b>
              {trains ? (
                <span className="items">
                  <TrainingProgress job={j} data={training} />
                </span>
              ) : (
                <span className="items">
                  {progress.total ? `${count(progress.step)} / ${count(progress.total)} · ` : ""}
                  <b>{progress.rate === null ? "—" : progress.rate.toFixed(1)}</b>/s
                </span>
              )}
              <span className="r">
                ETA <b>{duration(progress.eta)}</b>
              </span>
            </div>
          ) : null}
        </>
      ) : null}

      {trains ? (
        <>
          <Epochs job={j} data={training} />
          <TrainingCharts job={j} data={training} />
        </>
      ) : (
        <Timeline job={j} now={now} selected={selected} onSelect={(name) => { select(selected === name ? null : name); }} />
      )}

      {selected ? (
        <p className="note stage-note">
          Charts and log on this page now show <b style={{ color: "var(--text-primary)" }}>{selected}</b> only.
          <Button size="sm" variant="ghost" onClick={() => { select(null); }}>
            <Icon name="x" />
            Show all stages
          </Button>
        </p>
      ) : null}

      {!live && j.state === "completed" ? <Summary job={j} now={now} /> : null}

      <ResourceTiles
        points={points}
        cores={system.data?.cpu_count ?? null}
        gpuError={system.data?.gpu_error ?? null}
        training={trains ? { loaderWorkers: loaderWorkers(j), pauseAt: system.data?.thermal_pause_c ?? null } : undefined}
      />

      <ActivityLog jobId={j.id} stages={stageNames} feed={feed} filters={filters} onFilters={setFilters} />

      {drawerStage ? <StageDrawer job={j} stage={drawerStage} now={now} onClose={() => { select(null); }} /> : null}
      {cancelOpen ? <CancelDialog job={j} open onOpenChange={setCancelOpen} /> : null}
    </div>
  );
}
