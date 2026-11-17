// SCR-01 region 3: the active-job indicator. GET /jobs, refreshed by `job` messages.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { api, call, type Job } from "../api/client";
import { useFeedMessage } from "../api/stream";
import { Icon, IdlePill, Progress, Skeleton, StatePill } from "../components/ui";
import { ago, duration, stagePercent } from "../lib/format";

export const ACTIVE_STATES = "running,stalled";

export function useActiveJobs() {
  return useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () =>
      call(() => api.GET("/api/v1/jobs", { params: { query: { state: ACTIVE_STATES, limit: 20 } } })),
    refetchInterval: 30_000,
  });
}

function useLastFinished(enabled: boolean) {
  return useQuery({
    queryKey: ["jobs", "last-finished"],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/jobs", {
          params: { query: { state: "completed,failed,cancelled", limit: 1 } },
        }),
      ),
    enabled,
  });
}

/** The stage a job is on now, or null when it has none. */
export function currentStage(job: Job) {
  return job.stages.find((s) => s.name === job.current_stage) ?? null;
}

/** The GPU job first: the sampler tags it as primary (frame C). */
function ordered(jobs: Job[]): Job[] {
  const gpu = (job: Job) => (currentStage(job)?.queue === "gpu" ? 0 : 1);
  return [...jobs].sort((a, b) => gpu(a) - gpu(b) || a.id.localeCompare(b.id));
}

export function JobIndicator() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const active = useActiveJobs();
  const jobs = ordered(active.data?.items ?? []);
  const last = useLastFinished(active.isSuccess && jobs.length === 0);
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // A job message means some job changed: read the lists again (they are small).
  const onJob = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["jobs"] });
  }, [client]);
  useFeedMessage("job", onJob);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event instanceof MouseEvent && popRef.current?.contains(event.target as Node)) return;
      if (event instanceof MouseEvent && buttonRef.current?.contains(event.target as Node)) return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
    };
  }, [open]);

  if (active.isPending) {
    return (
      <span className="jobind" aria-busy="true">
        <Skeleton width={72} />
        <Skeleton width={150} />
      </span>
    );
  }

  if (active.isError && active.data === undefined) {
    // Never claim "no job running" when the jobs could not be read (principle 6).
    return (
      <span className="jobind idle" role="status">
        <IdlePill />
        <span className="stage">Jobs unavailable</span>
      </span>
    );
  }

  if (jobs.length === 0) {
    const previous = last.data?.items[0];
    return (
      <button
        ref={buttonRef}
        type="button"
        className="jobind idle"
        disabled={!previous}
        onClick={() => {
          if (previous) void navigate(`/jobs/${previous.id}`);
        }}
        aria-label={previous ? `No job running. Open the last job, ${previous.id}` : "No job running"}
      >
        <IdlePill />
        <span className="stage">
          No job running
          {previous ? (
            <>
              {" · last "}
              <span className="mono">{previous.id}</span> {previous.state}{" "}
              {ago(previous.finished_at)}
            </>
          ) : null}
        </span>
        {previous ? <Icon name="chevron" /> : null}
      </button>
    );
  }

  if (jobs.length === 1) {
    const job = jobs[0] as Job;
    const stage = currentStage(job);
    const pct = stagePercent(stage?.step, stage?.total);
    const name = `Active job ${job.id}, ${job.state}, ${stage?.name ?? "no"} stage${
      pct === null ? "" : `, ${String(Math.round(pct))} percent`
    }, opens the job`;
    return (
      <button
        type="button"
        className="jobind"
        aria-label={name}
        onClick={() => void navigate(`/jobs/${job.id}`)}
      >
        <StatePill state={job.state} />
        <span className="mono">{job.id}</span>
        <span className="stage">{stage?.name ?? "—"}</span>
        <Progress value={pct} state={job.state} />
        <span className="num">{pct === null ? "—" : `${String(Math.round(pct))}%`}</span>
        <span className="eta num">ETA {duration(stage?.eta_seconds)}</span>
        <Icon name="chevron" />
      </button>
    );
  }

  const summary = jobs
    .map((job) => {
      const stage = currentStage(job);
      const pct = stagePercent(stage?.step, stage?.total);
      return `${stage?.name ?? "—"} ${pct === null ? "—" : `${String(Math.round(pct))}%`}`;
    })
    .join(" · ");
  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="jobind"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`${String(jobs.length)} jobs running: ${summary}`}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        <StatePill state="running" label={`${String(jobs.length)} running`} />
        <span className="stage">{summary}</span>
        <Icon name="down" />
      </button>
      {open ? (
        <div className="pop" role="menu" ref={popRef}>
          {jobs.map((job) => {
            const stage = currentStage(job);
            const pct = stagePercent(stage?.step, stage?.total);
            return (
              <Link
                key={job.id}
                to={`/jobs/${job.id}`}
                className="row"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                }}
              >
                <StatePill state={job.state} />
                <span className="s">
                  <span className="mono" style={{ color: "var(--text-primary)" }}>
                    {job.id}
                  </span>{" "}
                  · {stage?.name ?? "—"} · {job.dataset ?? "—"} · {stage?.queue ?? "—"}
                </span>
                <Progress value={pct} state={job.state} />
                <span className="num">{pct === null ? "—" : `${String(Math.round(pct))}%`}</span>
              </Link>
            );
          })}
          <Link
            to="/jobs"
            className="all"
            role="menuitem"
            onClick={() => {
              setOpen(false);
            }}
          >
            All jobs
            <Icon name="chevron" />
            <kbd>g j</kbd>
          </Link>
        </div>
      ) : null}
    </>
  );
}
