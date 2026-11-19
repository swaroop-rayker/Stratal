// SCR-04 timeline (MON-4.2): one bar per stage, width by duration, state colour, icon,
// name and duration; pending stages dashed; a reused stage marked "cached". A click (or
// Enter) selects the stage: its drawer opens and the page's charts and log follow it.
import type { CSSProperties } from "react";

import type { Job, JobStage } from "../../api/client";
import { Icon, type IconName } from "../../components/ui";
import { duration, stagePercent } from "../../lib/format";
import { stageSeconds } from "./jobs";

const ICON: Record<JobStage["status"], IconName> = {
  pending: "clock",
  queued: "clock",
  running: "play",
  paused: "pause",
  completed: "check",
  failed: "x",
  skipped: "ban",
  cancelled: "ban",
};

function detail(stage: JobStage, seconds: number | null): string {
  if (stage.status === "pending") return "pending";
  if (stage.status === "queued") return "queued";
  if (stage.status === "skipped") return "not run";
  if (stage.status === "completed" && stage.cache_hit) return `cached · ${duration(seconds)}`;
  const pct = stage.status === "running" ? stagePercent(stage.step, stage.total) : null;
  if (stage.status === "failed" && stage.step !== null && stage.total !== null) {
    return `${duration(seconds)} · ${String(stage.step)}/${String(stage.total)}`;
  }
  return pct === null ? duration(seconds) : `${duration(seconds)} · ${String(Math.round(pct))}%`;
}

export function Timeline({ job, now, selected, onSelect }: {
  job: Job;
  now: number;
  selected: string | null;
  onSelect: (stage: string) => void;
}) {
  return (
    <div className="timeline" role="list" aria-label="Stages">
      {job.stages.map((stage) => {
        const seconds = stageSeconds(stage, now);
        const status = stage.status === "queued" ? "pending" : stage.status;
        const words = `${stage.name}, ${stage.status}${stage.cache_hit ? ", cached" : ""}, ${detail(stage, seconds)}`;
        return (
          <button
            key={stage.idx}
            type="button"
            role="listitem"
            className={`seg ${status}`}
            style={{ "--w": String(Math.max(1, (seconds ?? 60) / 60)) } as CSSProperties}
            aria-current={selected === stage.name ? true : undefined}
            aria-label={words}
            onClick={() => {
              onSelect(stage.name);
            }}
          >
            <b>
              <Icon name={ICON[stage.status]} />
              {stage.name}
            </b>
            <span>{detail(stage, seconds)}</span>
          </button>
        );
      })}
    </div>
  );
}
