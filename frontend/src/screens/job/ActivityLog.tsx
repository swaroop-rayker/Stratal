// SCR-04 activity log (MON-4.3, MON-1.3): newest first; filter by level ("and above"),
// stage, text and, across all jobs, by job. WARN and ERROR rows show Why and Do. While the
// reader is not at the top or has paused it, new lines wait behind "Jump to latest".
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, call, type StreamEvent } from "../../api/client";
import { type Feed, globalFeed } from "../../api/stream";
import { Icon, Kbd } from "../../components/ui";
import { clock } from "../../lib/format";
import { useShortcuts } from "../../shell/hooks";

export type Level = "DEBUG" | "INFO" | "WARN" | "ERROR";
const LEVELS: Level[] = ["DEBUG", "INFO", "WARN", "ERROR"];
const KEEP = 1000;
const TAIL = 300;

export interface LogFilters {
  scope: "job" | "all";
  level: Level;
  stage: string | null;
  job: string | null;
  text: string;
}

export function matches(event: StreamEvent, f: LogFilters, jobId: string): boolean {
  if (LEVELS.indexOf(event.level) < LEVELS.indexOf(f.level)) return false;
  if (f.stage && event.stage !== f.stage) return false;
  if (f.scope === "job" && event.job_id !== jobId) return false;
  if (f.scope === "all" && f.job && event.job_id !== f.job) return false;
  if (f.text && !event.message.toLowerCase().includes(f.text.toLowerCase())) return false;
  return true;
}

function Line({ e, jobs }: { e: StreamEvent; jobs: boolean }) {
  const loud = e.level === "WARN" || e.level === "ERROR";
  return (
    <div className={e.level === "WARN" ? "warn" : e.level === "ERROR" ? "error" : undefined}>
      <span className="ts">{clock(e.ts)}</span>
      {jobs ? (
        <span className="mono" style={{ color: "var(--text-primary)" }}>
          {e.job_id ?? "system"}
        </span>
      ) : null}
      <span className={`lv-${e.level}`}>{e.level}</span>
      <span className="st">{e.stage ?? "–"}</span>
      <span>{e.message}</span>
      {loud && (e.reason || e.suggested_action) ? (
        <span className="why">
          {e.reason ? (
            <>
              <b>Why</b> {e.reason}{" "}
            </>
          ) : null}
          {e.suggested_action ? (
            <>
              <b>Do</b> {e.suggested_action}
            </>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

export function ActivityLog({ jobId, stages, feed, filters, onFilters }: {
  jobId: string;
  stages: string[];
  feed: Feed | null;
  filters: LogFilters;
  onFilters: (f: LogFilters) => void;
}) {
  const query = filters.scope === "job"
    ? { min_level: filters.level, ...(filters.stage ? { stage: filters.stage } : {}), tail: true, limit: TAIL }
    : { min_level: filters.level, ...(filters.stage ? { stage: filters.stage } : {}), ...(filters.job ? { job_id: filters.job } : {}), tail: true, limit: TAIL };
  const initial = useQuery({
    queryKey: ["events", filters.scope, jobId, query],
    queryFn: () =>
      filters.scope === "job"
        ? call(() => api.GET("/api/v1/jobs/{job_id}/events", { params: { path: { job_id: jobId }, query } }))
        : call(() => api.GET("/api/v1/events", { params: { query } })),
  });
  const jobs = useQuery({
    queryKey: ["jobs", "recent-for-log"],
    queryFn: () => call(() => api.GET("/api/v1/jobs", { params: { query: { limit: 50 } } })),
    enabled: filters.scope === "all",
  });

  const [live, setLive] = useState<StreamEvent[]>([]);
  const [held, setHeld] = useState<StreamEvent[]>([]);
  const [paused, setPaused] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const text = useRef<HTMLInputElement>(null);

  // A new query (filters changed) starts over from its tail.
  const key = JSON.stringify([filters.scope, filters.level, filters.stage, filters.job, jobId]);
  const [shownKey, setShownKey] = useState(key);
  if (shownKey !== key) {
    setShownKey(key);
    setLive([]);
    setHeld([]);
  }

  const onEvent = useCallback(
    (e: StreamEvent) => {
      if (!matches(e, { ...filters, text: "" }, jobId)) return;
      const atTop = (box.current?.scrollTop ?? 0) < 8;
      if (paused || !atTop) setHeld((h) => [e, ...h].slice(0, KEEP));
      else setLive((l) => [e, ...l].slice(0, KEEP));
    },
    [filters, jobId, paused],
  );
  const source = filters.scope === "job" ? feed : globalFeed;
  useEffect(() => (source ? source.on("event", onEvent) : undefined), [source, onEvent]);

  const jump = () => {
    setLive((l) => [...held, ...l].slice(0, KEEP));
    setHeld([]);
    setPaused(false);
    box.current?.scrollTo({ top: 0 });
  };

  const bindings = useMemo(() => ({ "/": () => text.current?.focus() }), []);
  useShortcuts(bindings);

  const seen = new Set<number>();
  const lines = [...live, ...[...(initial.data?.items ?? [])].reverse()]
    .filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)))
    .filter((e) => matches(e, filters, jobId));
  const all = filters.scope === "all";
  const set = (patch: Partial<LogFilters>) => {
    onFilters({ ...filters, ...patch });
  };

  return (
    <section className="logbox" aria-label="Activity log">
      <div className="loghead">
        <h2>Activity</h2>
        <span className="group-btns" role="group" aria-label="Scope">
          <button type="button" className="btn sm" aria-pressed={!all} onClick={() => { set({ scope: "job", job: null }); }}>
            This job
          </button>
          <button type="button" className="btn sm" aria-pressed={all} onClick={() => { set({ scope: "all" }); }}>
            All jobs
          </button>
        </span>
        {all ? (
          <select className="select" aria-label="Job" value={filters.job ?? ""} onChange={(e) => { set({ job: e.target.value || null }); }}>
            <option value="">Job: any</option>
            {(jobs.data?.items ?? []).map((j) => (
              <option key={j.id} value={j.id}>
                {j.id} · {j.dataset ?? j.kind}
              </option>
            ))}
          </select>
        ) : null}
        <select className="select" aria-label="Level" value={filters.level} onChange={(e) => { set({ level: e.target.value as Level }); }}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              Level: {l}
              {l === "ERROR" ? "" : " and above"}
            </option>
          ))}
        </select>
        <select className="select" aria-label="Stage" value={filters.stage ?? ""} onChange={(e) => { set({ stage: e.target.value || null }); }}>
          <option value="">Stage: all</option>
          {stages.map((s) => (
            <option key={s} value={s}>
              Stage: {s}
            </option>
          ))}
        </select>
        <span className="spacer" />
        <input
          ref={text}
          className="input"
          style={{ height: "var(--h-sm)", width: 160, fontSize: "var(--fs-12)" }}
          placeholder="Filter messages"
          aria-label="Filter messages"
          value={filters.text}
          onChange={(e) => { set({ text: e.target.value }); }}
          onKeyDown={(e) => { if (e.key === "Escape") e.currentTarget.blur(); }}
        />
        <Kbd>/</Kbd>
        {held.length ? (
          <button type="button" className="btn sm ghost" onClick={jump}>
            <Icon name="down" />
            Jump to latest · {held.length} new
          </button>
        ) : (
          <button type="button" className="btn sm ghost" aria-pressed={!paused} onClick={() => { setPaused((p) => !p); }}>
            <Icon name={paused ? "play" : "pause"} />
            Auto-scroll {paused ? "off" : "on"}
          </button>
        )}
      </div>
      <div className={all ? "log jobs" : "log"} role="log" aria-live="polite" ref={box}>
        {initial.isPending ? (
          <div><span className="ts">…</span><span /><span /><span className="muted">Loading events…</span></div>
        ) : initial.isError ? (
          <div className="error"><span className="ts">–</span><span className="lv-ERROR">ERROR</span><span /><span>Events could not be loaded. Retry by changing a filter.</span></div>
        ) : lines.length === 0 ? (
          <div><span className="ts">–</span><span /><span /><span className="muted">No events match these filters.</span></div>
        ) : (
          lines.map((e) => <Line key={e.id} e={e} jobs={all} />)
        )}
      </div>
    </section>
  );
}
