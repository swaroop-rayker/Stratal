// SCR-13 — System monitor (approved hi-fi SCR-13_v1, 2026-11-22; History SCR-13history_v1, 2026-11-24). Overview of a range,
// Compare two jobs and History; the range, tab, jobs and history choices live in the URL (SCREENS.md SCR-13).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { api, call } from "../../api/client";
import { PRESETS, useMonitorLive, type Range, type RangeJob } from "../../api/monitor";
import { Icon } from "../../components/ui";
import { ago, stamp } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useCrumbs } from "../../shell/hooks";
import { CompareJobs } from "./CompareJobs";
import { History } from "./History";
import { Overview } from "./Overview";

import "../../styles/scr13.css";

const STALE_S = 30;

function rangeOf(params: URLSearchParams): Range {
  const from = params.get("from");
  const to = params.get("to");
  if (from && to && !Number.isNaN(Date.parse(from)) && !Number.isNaN(Date.parse(to))) return { kind: "custom", from, to };
  const minutes = Number(params.get("range") ?? 60);
  return { kind: "preset", minutes: PRESETS.some((p) => p.minutes === minutes) ? minutes : 60 };
}

function MonitorHealth() {
  const now = useNow(5000);
  const latest = useQuery({
    queryKey: ["monitor", "latest"],
    queryFn: () => call(() => api.GET("/api/v1/telemetry/latest")),
    refetchInterval: 5000,
  });
  const ts = latest.data?.ts ?? null;
  const age = ts ? (now - Date.parse(ts)) / 1000 : null;
  const ok = age !== null && age <= STALE_S;
  return (
    <span className={ok ? "sys-mon" : "sys-mon down"} role="status">
      <Icon name={ok ? "heart" : "alert"} />
      {ok ? <><b>Monitor reporting</b> · {ago(ts, now)}</> : ts ? <><b>Not reporting</b> since {stamp(ts)}</> : <b>No telemetry yet</b>}
    </span>
  );
}

function local(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${String(d.getFullYear())}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The custom form's first values: the range shown, or the last hour. */
function customFrom(range: Range): { from: string; to: string } {
  if (range.kind === "custom") return { from: local(range.from), to: local(range.to) };
  const now = new Date();
  return { from: local(new Date(now.getTime() - 3600_000).toISOString()), to: local(now.toISOString()) };
}

function short(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

export function SystemScreen() {
  useCrumbs([{ label: "System" }]);
  useMonitorLive();
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab");
  const tab = asked === "compare" || asked === "history" ? asked : "overview";
  const range = rangeOf(params);
  const [custom, setCustom] = useState<{ from: string; to: string } | null>(null);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: false });
  };
  const zoom = (job: RangeJob) => {
    const start = Date.parse(job.started_at ?? "") - 60_000;
    const end = (job.finished_at ? Date.parse(job.finished_at) : Date.now()) + 60_000;
    update({ from: new Date(start).toISOString(), to: new Date(end).toISOString(), range: null });
  };

  return (
    <div className="scr13 page">
      <div className="phead">
        <h1>System</h1>
        <span className="sub">The machine over time, beyond one job</span>
        <span className="spacer" />
        {tab === "overview" ? (
          <span className="group-btns" role="group" aria-label="Range">
            {PRESETS.map((p) => (
              <button
                type="button"
                key={p.minutes}
                className="btn sm"
                aria-pressed={range.kind === "preset" && range.minutes === p.minutes}
                onClick={() => { setCustom(null); update({ range: String(p.minutes), from: null, to: null }); }}
              >
                {p.label}
              </button>
            ))}
            <button
              type="button"
              className="btn sm"
              aria-pressed={range.kind === "custom"}
              aria-expanded={custom !== null}
              onClick={() => { setCustom(custom ? null : customFrom(range)); }}
            >
              <Icon name="clock" />
              {range.kind === "custom" ? `${short(range.from)} – ${short(range.to)}` : "Custom…"}
            </button>
          </span>
        ) : null}
        <MonitorHealth />
      </div>
      {custom && tab === "overview" ? (
        <form
          className="sys-custom"
          onSubmit={(e) => {
            e.preventDefault();
            const from = new Date(custom.from);
            const to = new Date(custom.to);
            if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from >= to) return;
            update({ from: from.toISOString(), to: to.toISOString(), range: null });
            setCustom(null);
          }}
        >
          <label>From <input className="input mono" type="datetime-local" value={custom.from} onChange={(e) => { setCustom({ ...custom, from: e.target.value }); }} /></label>
          <label>To <input className="input mono" type="datetime-local" value={custom.to} onChange={(e) => { setCustom({ ...custom, to: e.target.value }); }} /></label>
          <button type="submit" className="btn sm primary" disabled={!custom.from || !custom.to || custom.from >= custom.to}>Apply</button>
          <button type="button" className="btn sm ghost" onClick={() => { setCustom(null); }}>Cancel</button>
        </form>
      ) : null}
      <div className="tabs" role="tablist">
        <button type="button" className="tab" role="tab" aria-selected={tab === "overview"} onClick={() => { update({ tab: null }); }}>Overview</button>
        <button type="button" className="tab" role="tab" aria-selected={tab === "compare"} onClick={() => { update({ tab: "compare" }); }}>Compare two jobs</button>
        <button type="button" className="tab" role="tab" aria-selected={tab === "history"} onClick={() => { update({ tab: "history" }); }}>History</button>
      </div>
      {tab === "overview" ? (
        <Overview range={range} onZoom={zoom} />
      ) : tab === "history" ? (
        <History
          stage={params.get("stage")}
          queue={params.get("queue")}
          group={params.get("group")}
          n={[10, 50, 100].includes(Number(params.get("n"))) ? Number(params.get("n")) : 20}
          reused={params.get("reused") === "1"}
          onChange={update}
          onCompare={(a, b) => { update({ tab: "compare", a, b, align: null }); }}
        />
      ) : (
        <CompareJobs
          a={params.get("a")}
          b={params.get("b")}
          align={params.get("align") === "epoch" ? "epoch" : "time"}
          onChange={(next) => {
            const changes: Record<string, string | null> = {};
            if (next.a !== undefined) changes["a"] = next.a;
            if (next.b !== undefined) changes["b"] = next.b;
            if (next.align !== undefined) changes["align"] = next.align === "time" ? null : next.align;
            update(changes);
          }}
        />
      )}
    </div>
  );
}
