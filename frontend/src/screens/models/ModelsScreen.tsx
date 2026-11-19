// SCR-11 frame A: every lineage, filterable by state (REG-1.2), task and name
// (mockup docs/design/screens/SCR-11_v1.html, G2 approved 2026-11-17).
import { useMemo, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { type Lineage, useLineages, useRegistryLive, type VersionState } from "../../api/registry";
import { EmptyState, ErrorState } from "../../components/states";
import { Icon, Kbd, Skeleton } from "../../components/ui";
import { bytes, clock, DASH } from "../../lib/format";
import { useCrumbs, useShortcuts } from "../../shell/hooks";
import { metric, VersionLabel } from "./parts";

const STATES: { value: VersionState | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "trained", label: "Trained" },
  { value: "partial", label: "Partial" },
];

/** Lineages with a version in ``state`` (the API's filter), counted from the full list. */
export function stateCounts(items: Lineage[]): Record<"all" | VersionState, number> {
  return {
    all: items.length,
    trained: items.filter((l) => l.counts.trained > 0).length,
    partial: items.filter((l) => l.counts.partial > 0).length,
    base: items.filter((l) => l.counts.base > 0).length,
  };
}

export function ModelsScreen() {
  useCrumbs([{ label: "Models" }]);
  useRegistryLive();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const state = (params.get("state") as VersionState | null) ?? null;
  const task = params.get("task") ?? "";
  const q = params.get("q") ?? "";
  const all = useLineages(null, "");
  const shown = useLineages(state, q);
  const search = useRef<HTMLInputElement>(null);
  useShortcuts(useMemo(() => ({ "/": () => search.current?.focus() }), []));

  const counts = stateCounts(all.data?.items ?? []);
  const tasks = [...new Set((all.data?.items ?? []).map((l) => l.task))].sort();
  const rows = (shown.data?.items ?? []).filter((l) => !task || l.task === task);

  function set(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  if (all.isError) {
    return (
      <div className="scr11 page">
        <ErrorState error={all.error} title="Models could not be loaded" onRetry={() => void all.refetch()} />
      </div>
    );
  }
  return (
    <div className="scr11 page">
      <div className="phead">
        <h1>Models</h1>
        <span className="spacer" />
        <button type="button" className="btn" disabled title="The model builder arrives in M9 (SCR-08)">
          <Icon name="plus" />
          Build a model…
        </button>
      </div>
      {all.isPending ? (
        <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <Skeleton width={220} height={22} />
          <Skeleton width={900} height={28} />
          <Skeleton width={900} height={28} />
        </div>
      ) : counts.all === 0 ? (
        <EmptyState
          icon="grid"
          title="No models yet"
          action={
            <button type="button" className="btn sm primary" disabled title="The model builder arrives in M9 (SCR-08)">
              <Icon name="plus" />
              Build a model
            </button>
          }
        >
          A model appears here after its first training run.
        </EmptyState>
      ) : (
        <>
          <div className="toolbar">
            <span className="group-btns" role="group" aria-label="State">
              {STATES.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  className="btn sm"
                  aria-pressed={state === s.value}
                  onClick={() => { set("state", s.value); }}
                >
                  {s.label}
                  <span className="count">{counts[s.value ?? "all"]}</span>
                </button>
              ))}
            </span>
            <label className="sr-only" htmlFor="models-task">Task</label>
            <select
              id="models-task"
              className="select"
              value={task}
              onChange={(event) => { set("task", event.target.value || null); }}
            >
              <option value="">Task: any</option>
              {tasks.map((t) => (
                <option key={t} value={t}>{`Task: ${t}`}</option>
              ))}
            </select>
            <span className="spacer" />
            <span className="search">
              <Icon name="search" />
              <input
                ref={search}
                className="input"
                placeholder="Lineage"
                aria-label="Filter lineages by name"
                defaultValue={q}
                onChange={(event) => { set("q", event.target.value.trim() || null); }}
              />
              <Kbd>/</Kbd>
            </span>
          </div>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Lineage</th>
                  <th>Task</th>
                  <th className="r">Versions</th>
                  <th>Latest</th>
                  <th className="r">Main metric</th>
                  <th className="r">Disk</th>
                  <th aria-sort="descending">Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ color: "var(--text-secondary)" }}>
                      No lineage matches these filters.
                    </td>
                  </tr>
                ) : (
                  rows.map((lineage) => (
                    <tr
                      key={lineage.id}
                      tabIndex={0}
                      style={{ cursor: "pointer" }}
                      aria-label={`Open ${lineage.name}`}
                      onClick={() => void navigate(`/models/${lineage.id}`)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void navigate(`/models/${lineage.id}`);
                      }}
                    >
                      <td className="id">{lineage.name}</td>
                      <td>{lineage.task}</td>
                      <td className="r">
                        {lineage.counts.base + lineage.counts.partial + lineage.counts.trained}
                      </td>
                      <td className="m2">
                        {lineage.latest_version_id ?? DASH}{" "}
                        {lineage.latest_state ? <VersionLabel state={lineage.latest_state} /> : null}
                        {lineage.counts.partial > 0 ? (
                          <span className="latest">· {lineage.counts.partial} partial</span>
                        ) : null}
                      </td>
                      <td className="r">
                        {lineage.main_metric === null ? DASH : `val acc ${metric(lineage.main_metric)}`}
                      </td>
                      <td className="r">{bytes(lineage.weights_bytes + lineage.checkpoint_bytes)}</td>
                      <td>{clock(lineage.updated_at).slice(0, 5)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
