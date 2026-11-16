// SCR-01 frame G: toasts from `job` messages on the global stream.
// Completed toasts close after 6 s; failed and stalled stay until dismissed.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import type { Job } from "../api/client";
import { useFeedMessage } from "../api/stream";
import { Button, Icon } from "../components/ui";
import { duration } from "../lib/format";

interface Toast {
  key: string;
  job: Job;
}

const LOUD = new Set(["failed", "stalled"]);
const SHOWN = new Set(["completed", "failed", "stalled"]);
const AUTO_CLOSE_MS = 6000;

function seconds(job: Job): number | null {
  if (!job.started_at || !job.finished_at) return null;
  return (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000;
}

function failedStage(job: Job): string | null {
  return job.stages.find((s) => s.status === "failed")?.name ?? null;
}

export function Toasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seen = useRef(new Set<string>());
  const navigate = useNavigate();

  const dismiss = useCallback((key: string) => {
    setToasts((list) => list.filter((t) => t.key !== key));
  }, []);

  const onJob = useCallback((job: Job) => {
    if (!SHOWN.has(job.state)) return;
    const key = `${job.id}:${job.state}:${String(job.attempt)}`;
    if (seen.current.has(key)) return;
    seen.current.add(key);
    setToasts((list) => [...list, { key, job }].slice(-4));
  }, []);
  useFeedMessage("job", onJob);

  useEffect(() => {
    const quiet = toasts.filter((t) => !LOUD.has(t.job.state));
    const timers = quiet.map((t) => window.setTimeout(() => { dismiss(t.key); }, AUTO_CLOSE_MS));
    return () => {
      timers.forEach((timer) => { window.clearTimeout(timer); });
    };
  }, [toasts, dismiss]);

  if (toasts.length === 0) return null;
  return (
    <div className="toasts">
      {toasts.map(({ key, job }) => {
        const loud = LOUD.has(job.state);
        const color = job.state === "completed" ? "var(--success)" : job.state === "stalled" ? "var(--state-stalled)" : "var(--danger)";
        const style = { "--c": color } as React.CSSProperties;
        if (!loud) {
          return (
            <div key={key} className="toast" role="status" style={style}>
              <Icon name="check" />
              <div>
                <b>
                  <span className="mono">{job.id}</span> completed · {duration(seconds(job))}
                </b>
                {job.output_version_id ? (
                  <span>
                    Output <span className="mono">{job.output_version_id}</span>
                  </span>
                ) : null}
              </div>
              <button type="button" className="btn ghost sm x" aria-label="Dismiss" onClick={() => { dismiss(key); }}>
                <Icon name="x" />
              </button>
            </div>
          );
        }
        const stage = failedStage(job) ?? job.current_stage;
        return (
          <div key={key} className="toast" role="alert" style={style}>
            <Icon name={job.state === "stalled" ? "hourglass" : "x"} />
            <div style={{ flex: 1 }}>
              <b>
                <span className="mono">{job.id}</span> {job.state}
                {stage ? ` in ${stage}` : ""}
              </b>
              {job.error ? (
                <span>
                  <span className="mono">{job.error.code}</span>
                  {job.error.reason ? ` · ${job.error.reason}` : ""}
                </span>
              ) : null}
              {job.error?.suggested_action ? (
                <>
                  <br />
                  <span>
                    <b style={{ display: "inline", color: "var(--text-primary)" }}>Do:</b>{" "}
                    {job.error.suggested_action}
                  </span>
                </>
              ) : null}
              <div className="acts">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    dismiss(key);
                    void navigate(`/jobs/${job.id}`);
                  }}
                >
                  Open job
                </Button>
                <Button size="sm" variant="ghost" onClick={() => { dismiss(key); }}>
                  Dismiss
                </Button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
