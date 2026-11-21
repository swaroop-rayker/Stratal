// SCR-11 frame B: one lineage. Its versions (REG-1.2: states shown and filterable), pins
// (REG-2.5), disk use (REG-2.11), retention (REG-2.3, REG-2.4, REG-2.9) and Try it
// (REG-1.4). ?version= opens a version's drawer (frame C).
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import {
  type ModelVersion,
  useDisk,
  useLineage,
  usePin,
  useRegistryLive,
  type VersionState,
} from "../../api/registry";
import { ErrorState } from "../../components/states";
import { Icon, Skeleton } from "../../components/ui";
import { bytes, clock } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";
import { DiskCard } from "./DiskCard";
import { metric, PinButton, VersionLabel } from "./parts";
import { RetentionCard } from "./RetentionCard";
import { TryIt } from "./TryIt";
import { VersionDrawer } from "./VersionDrawer";

const STATES: { value: VersionState | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "trained", label: "Trained" },
  { value: "partial", label: "Partial" },
  { value: "base", label: "Base" },
];

/** The versions a filter shows, from the full list (REG-1.2.AC1). */
export function filterVersions(
  versions: ModelVersion[],
  state: VersionState | null,
  weights: "present" | "deleted" | null,
): ModelVersion[] {
  return versions.filter(
    (v) =>
      (state === null || v.state === state) &&
      (weights === null || (weights === "deleted") === (v.weights_status === "weights_deleted")),
  );
}

function weightsCell(v: ModelVersion) {
  if (v.weights_status === "weights_deleted") return <td className="weights-gone">deleted</td>;
  if (v.weights_status === "deleting") return <td className="weights-gone">deleting…</td>;
  if (v.state === "partial") return <td className="m2">in its checkpoint</td>;
  return <td className="m2">{bytes(v.weights_bytes)}{v.state === "base" ? " · kept" : ""}</td>;
}

export function LineageScreen() {
  const { lineageId = "" } = useParams();
  useRegistryLive();
  const lineage = useLineage(lineageId);
  const disk = useDisk(lineageId);
  const pin = usePin();
  const [params, setParams] = useSearchParams();
  const [queued, setQueued] = useState<string | null>(null);
  const [tryId, setTryId] = useState<string | null>(null);
  const [tab, setTab] = useState<"versions" | "disk" | "try">("versions");
  const state = (params.get("state") as VersionState | null) ?? null;
  const weights = (params.get("weights") as "present" | "deleted" | null) ?? null;
  const open = params.get("version");
  const data = lineage.data;
  useCrumbs([{ label: "Models", to: "/models" }, { label: data?.name ?? lineageId }]);

  function set(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: name !== "version" });
  }

  if (lineage.isError) {
    return (
      <div className="scr11 page">
        <ErrorState error={lineage.error} title="The lineage could not be loaded" onRetry={() => void lineage.refetch()} />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="scr11 page" aria-busy="true">
        <Skeleton width={320} height={22} />
        <Skeleton width={900} height={200} />
      </div>
    );
  }
  const all = data.versions;
  const shown = filterVersions(all, state, weights);
  const count = (s: VersionState | null) => (s === null ? all.length : all.filter((v) => v.state === s).length);
  const datasets = [...new Set(all.map((v) => v.dataset_version_id).filter(Boolean))];
  const usable = (id: string | null) => {
    const v = all.find((x) => x.id === id);
    return v && v.weights_status === "present" && v.state !== "partial" ? v.id : null;
  };
  const tryVersion = usable(tryId) ?? usable(data.latest_version_id);
  const tryDataset = all.find((v) => v.id === tryVersion)?.dataset_version_id ?? null;

  return (
    <div className="scr11 page">
      <div className="phead">
        <h1><span className="mono">{data.name}</span></h1>
        <span className="sub">
          {data.task} · input <span className="mono">{data.input_shape.join("×")}</span> → {data.task === "regression" ? "1 value" : `${String(data.classes.length)} classes`} ·{" "}
          <span className="mono">{data.parameters >= 100_000 ? `${(data.parameters / 1e6).toFixed(2)} M` : data.parameters.toLocaleString("en-US")}</span> parameters
        </span>
        <span className="spacer" />
        {(() => {
          const latest = data.versions.find((v) => v.id === data.latest_version_id && v.job_id);
          return latest?.job_id ? (
            <Link className="btn" to={`/runs/new?from=${latest.job_id}`}>
              <Icon name="refresh" />
              Train again…
            </Link>
          ) : (
            <button type="button" className="btn" disabled title="No run to start again from yet">
              <Icon name="refresh" />
              Train again…
            </button>
          );
        })()}
      </div>
      {data.founded_from ? (
        // RUN-1.10 (SCR-11 v2 frame E): a lineage whose base is a fine-tune's starting weights.
        <div className="callout info" style={{ maxWidth: 720 }}>
          <Icon name="branch" />
          <span>
            Founded by fine-tuning <Link className="mono" to={`/models/${data.founded_from.lineage_id}?version=${data.founded_from.version_id}`}>{data.founded_from.version_id}</Link> from <b>{data.founded_from.lineage_name}</b>
            {data.founded_from.reset_head ? `, head reset for ${data.task === "regression" ? "one value" : `${String(data.classes.length)} classes`}` : ""}. Its base is that run's starting weights.
          </span>
        </div>
      ) : null}
      {queued ? (
        <p className="note2" role="status">
          Retention job <Link className="mono" to={`/jobs/${queued}`}>{queued}</Link> queued; this page updates when it finishes.
        </p>
      ) : null}
      {/* Frame G: below 1280 px the right-hand column becomes tabs beside Versions. */}
      <span className="group-btns narrow-tabs" role="group" aria-label="Show">
        {(
          [
            ["versions", "Versions"],
            ["disk", "Disk and retention"],
            ["try", "Try it"],
          ] as const
        ).map(([value, label]) => (
          <button key={value} type="button" className="btn sm" aria-pressed={tab === value} onClick={() => { setTab(value); }}>
            {label}
          </button>
        ))}
      </span>
      <div className="cols" data-tab={tab}>
        <div className="main-col" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", minWidth: 0 }}>
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
                  <span className="count">{count(s.value)}</span>
                </button>
              ))}
            </span>
            <label className="sr-only" htmlFor="lineage-weights">Weights</label>
            <select
              id="lineage-weights"
              className="select"
              value={weights ?? ""}
              onChange={(event) => { set("weights", event.target.value || null); }}
            >
              <option value="">Weights: any</option>
              <option value="present">Weights: on disk</option>
              <option value="deleted">Weights: deleted</option>
            </select>
          </div>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>State</th>
                  <th>Weights</th>
                  <th className="r">val acc</th>
                  <th className="c-created">Created</th>
                  <th><span className="sr-only">Pinned</span></th>
                </tr>
              </thead>
              <tbody>
                {shown.length === 0 ? (
                  <tr><td colSpan={6} style={{ color: "var(--text-secondary)" }}>No version matches these filters.</td></tr>
                ) : (
                  shown.map((v) => (
                    <tr
                      key={v.id}
                      data-version={v.id}
                      tabIndex={0}
                      style={{ cursor: "pointer" }}
                      aria-selected={open === v.id}
                      aria-label={`${v.id}, ${v.state}${v.is_latest ? ", latest" : ""}`}
                      onClick={() => { set("version", v.id); }}
                      onKeyDown={(event) => { if (event.key === "Enter") set("version", v.id); }}
                    >
                      <td className="id">{v.id}</td>
                      <td>
                        <VersionLabel state={v.state} />
                        {v.is_latest ? <span className="latest">latest</span> : null}
                        {v.state === "partial" && v.job_state ? <span className="latest">{v.job_state}</span> : null}
                      </td>
                      {weightsCell(v)}
                      <td className="r" style={v.main_metric === null ? { color: "var(--text-muted)" } : undefined}>
                        {metric(v.main_metric)}
                      </td>
                      <td className="c-created">{clock(v.trained_at ?? v.created_at).slice(0, 5)}</td>
                      <td>
                        {v.state === "trained" ? (
                          <PinButton
                            version={v}
                            busy={pin.isPending}
                            onToggle={(pinned) => { pin.mutate({ id: v.id, pinned }); }}
                          />
                        ) : null}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <p className="note2">
            {datasets.length === 1 ? (
              <>All on <span className="mono">{datasets[0]}</span>. </>
            ) : null}
            Deleted weights keep their metadata: open a version to re-train it with its exact config and data.
          </p>
        </div>
        <div className="side-col" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
          <DiskCard report={disk.data} pending={disk.isPending} />
          <RetentionCard lineage={data} onQueued={setQueued} />
          <TryIt key={tryVersion ?? "none"} versionId={tryVersion} datasetVersion={tryDataset} />
        </div>
      </div>
      <VersionDrawer
        versionId={open}
        onClose={() => { set("version", null); }}
        onTry={(id) => { setTryId(id); set("version", null); }}
      />
    </div>
  );
}
