// SCR-02 datasets and versions (SCREENS.md §3; mockup SCR-02_v1; DATA-3.1, 3.2, 3.4, UI-2.1).
import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";

import type { Job, JobState, Schemas } from "../../api/client";
import { useDataset, useDatasets, useVersions } from "../../api/queries";
import { useFeedMessage } from "../../api/stream";
import { copy, EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, type IconName, Kbd, Progress, Skeleton, StatePill } from "../../components/ui";
import { bytes, count, stagePercent } from "../../lib/format";
import { currentStage, useActiveJobs } from "../../shell/JobIndicator";
import { useCrumbs, useShortcuts } from "../../shell/hooks";
import { type StartFrom, StartPipelineDrawer } from "./StartPipelineDrawer";

type Dataset = Schemas["Dataset"];
type Version = Schemas["Version"];

const STATUSES = ["accepted", "rejected", "duplicate", "filtered"] as const;
const STACK_CLASS = { accepted: "acc", rejected: "rej", duplicate: "dup", filtered: "fil" };

function statusCount(version: Version, status: (typeof STATUSES)[number]): number {
  const value = version.counts[status];
  return typeof value === "number" ? value : 0;
}

/** "14:21" today, "yesterday", else "Nov 13". */
function when(iso: string, now = new Date(), relative = true): string {
  const date = new Date(iso);
  const day = (d: Date) => d.toDateString();
  if (day(date) === day(now)) return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (relative && day(date) === day(yesterday)) return "yesterday";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function acceptedShare(version: Version | null | undefined): string {
  if (!version || version.row_count === 0) return "—";
  return `${((statusCount(version, "accepted") / version.row_count) * 100).toFixed(1)}%`;
}

const JOB_ICON: Partial<Record<JobState, [IconName, string]>> = {
  completed: ["check", ""],
  failed: ["x", "bad"],
  cancelled: ["ban", ""],
  running: ["play", "run"],
  stalled: ["hourglass", "bad"],
};

function MadeBy({ version }: { version: Version }) {
  const state = version.created_by_job_state;
  const [icon, tone]: [IconName, string] = state ? (JOB_ICON[state] ?? ["clock", ""]) : ["clock", ""];
  return (
    <span className="made">
      {when(version.created_at, new Date(), false)}
      {version.created_by_job_id ? (
        <span className="when-job">
          {" · "}
          <span className="mono">{version.created_by_job_id}</span>
        </span>
      ) : (
        " · CLI"
      )}
      {state ? <Icon name={icon} className={tone} style={{ color: tone ? undefined : `var(--state-${state})` }} /> : null}
      {state ? <span className="sr-only">{state}</span> : null}
    </span>
  );
}

function Counts({ version }: { version: Version }) {
  const total = version.row_count || 1;
  const [acc, rej, dup, fil] = STATUSES.map((s) => statusCount(version, s));
  return (
    <span className="counts" aria-label={`${count(acc)} accepted, ${count(rej)} rejected, ${count(dup)} duplicate, ${count(fil)} filtered`}>
      <span className="stack" aria-hidden="true">
        {STATUSES.map((s) => {
          const n = statusCount(version, s);
          return n ? <i key={s} className={STACK_CLASS[s]} style={{ width: `${String((n / total) * 100)}%` }} /> : null;
        })}
      </span>
      <span className="n-acc">{count(acc)}</span>· {count(rej)} · {count(dup)} · {count(fil)}
    </span>
  );
}

function RowMenu({ version, onContinue }: { version: Version; onContinue: () => void }) {
  const navigate = useNavigate();
  return (
    <Menu.Root>
      <Menu.Trigger aria-label={`Actions for ${version.version_id}`} onClick={(e) => { e.stopPropagation(); }}>
        <Icon name="more" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content className="menu-pop" align="end" sideOffset={4} onClick={(e) => { e.stopPropagation(); }}>
          <Menu.Item className="menu-item" onSelect={() => void navigate(`/versions/${version.version_id}`)}>
            <Icon name="chevron" />
            Open version
            <Kbd>Enter</Kbd>
          </Menu.Item>
          <Menu.Item className="menu-item" onSelect={onContinue}>
            <Icon name="branch" />
            Continue the pipeline from here
          </Menu.Item>
          <Menu.Item className="menu-item" onSelect={() => void copy(version.version_id)}>
            <Icon name="copy" />
            Copy version ID
          </Menu.Item>
          <Menu.Item
            className="menu-item"
            disabled={!version.created_by_job_id}
            onSelect={() => void navigate(`/jobs/${version.created_by_job_id ?? ""}`)}
          >
            <Icon name="list" />
            Open creating job
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function PendingRow({ job }: { job: Job }) {
  const navigate = useNavigate();
  const stage = currentStage(job);
  const base = [...job.stages].reverse().find((s) => s.status === "completed")?.output_version_id;
  const pct = stagePercent(stage?.step, stage?.total);
  return (
    <tr className="pending" onClick={() => void navigate(`/jobs/${job.id}`)}>
      <td>
        <StatePill state={job.state} label="Pending" />
      </td>
      <td>
        <span className="stage-tag">{stage?.name ?? "—"}</span>
      </td>
      <td className="parent col-parent">{base ?? "raw"}</td>
      <td>
        <span className="counts">
          <Progress value={pct} state={job.state} />
          <span className="what">
            {stage?.step === null || stage?.step === undefined ? (stage?.activity ?? "starting…") : `${count(stage.step)} / ${count(stage.total)}`}
          </span>
        </span>
      </td>
      <td className="r" style={{ color: "var(--text-muted)" }}>
        –
      </td>
      <td>
        <span className="made">
          <span className="mono">{job.id}</span>
        </span>
      </td>
      <td />
    </tr>
  );
}

function VersionsTable({ dataset, onContinue }: { dataset: Dataset; onContinue: (v: Version) => void }) {
  const navigate = useNavigate();
  const versions = useVersions(dataset.slug);
  const active = useActiveJobs();
  const [selected, setSelected] = useState(0);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);
  const pending = (active.data?.items ?? []).filter((j) => j.dataset === dataset.slug);
  const items = versions.data?.items ?? [];

  const move = useCallback(
    (delta: number) => {
      setSelected((i) => {
        const next = Math.max(0, Math.min(items.length - 1, i + delta));
        rows.current[next]?.focus();
        return next;
      });
    },
    [items.length],
  );
  const bindings = useMemo(
    () => ({
      j: () => { move(1); },
      k: () => { move(-1); },
    }),
    [move],
  );
  useShortcuts(bindings);

  if (versions.isPending) {
    return (
      <div className="table-wrap" aria-busy="true">
        <table className="t">
          <tbody>
            {[0, 1, 2].map((i) => (
              <tr key={i}>
                <td><Skeleton width={120} /></td>
                <td><Skeleton width={70} /></td>
                <td><Skeleton width={180} /></td>
                <td><Skeleton width={60} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (versions.isError) {
    return (
      <ErrorState
        error={versions.error}
        title={`Versions of ${dataset.slug} could not be loaded`}
        onRetry={() => void versions.refetch()}
      />
    );
  }
  if (items.length === 0 && pending.length === 0) {
    return <EmptyState icon="db" title="No versions yet">A version appears when a stage of a job on this dataset commits.</EmptyState>;
  }
  return (
    <>
      <div className="keyrow" aria-hidden="true">
        <span>Rows by status, in this order:</span>
        {[["success", "accepted"], ["danger", "rejected"], ["text-muted", "duplicate"], ["warning", "filtered"]].map(([c, w]) => (
          <span key={w}>
            <i style={{ "--c": `var(--${c ?? ""})` } as CSSProperties} />
            {w}
          </span>
        ))}
      </div>
      <div className="table-wrap">
        <table className="t" aria-label={`Versions of ${dataset.slug}`}>
          <thead>
            <tr>
              <th aria-sort="descending">
                Version <Icon name="sort" style={{ width: 10, height: 10 }} />
              </th>
              <th>Stage</th>
              <th className="col-parent">Parent</th>
              <th>Rows by status</th>
              <th className="r">New files</th>
              <th>Made</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {pending.map((job) => (
              <PendingRow key={job.id} job={job} />
            ))}
            {items.map((v, i) => (
              <tr
                key={v.version_id}
                ref={(el) => {
                  rows.current[i] = el;
                }}
                tabIndex={i === selected ? 0 : -1}
                aria-selected={i === selected}
                onFocus={() => {
                  setSelected(i);
                }}
                onClick={() => void navigate(`/versions/${v.version_id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void navigate(`/versions/${v.version_id}`);
                }}
              >
                <td className="id">{v.version_id}</td>
                <td>
                  <span className="stage-tag">{v.stage}</span>
                </td>
                <td className={v.parent_version_id ? "parent col-parent" : "parent raw col-parent"}>
                  {v.parent_version_id ?? "raw"}
                </td>
                <td>
                  <Counts version={v} />
                </td>
                <td className="r">{v.new_blob_bytes ? `+${bytes(v.new_blob_bytes)}` : "0 B"}</td>
                <td>
                  <MadeBy version={v} />
                </td>
                <td className="menu">
                  <RowMenu version={v} onContinue={() => { onContinue(v); }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function DatasetDetail({ slug, onContinue }: { slug: string; onContinue: (v: Version) => void }) {
  const dataset = useDataset(slug);
  if (dataset.isPending) {
    return (
      <section className="detail" aria-busy="true">
        <Skeleton width={180} height={20} />
        <Skeleton width={420} />
      </section>
    );
  }
  if (dataset.isError) {
    return (
      <section className="detail">
        <ErrorState error={dataset.error} title={`Dataset ${slug} could not be loaded`} onRetry={() => void dataset.refetch()} />
      </section>
    );
  }
  const d = dataset.data;
  const latest = d.latest_version;
  return (
    <section className="detail" aria-label={`${d.slug} versions`}>
      <div className="dhead">
        <h1>{d.slug}</h1>
        <span className="sub">
          {d.modality}
          {d.source_uri ? (
            <>
              {" · from "}
              <span className="mono">{d.source_uri}</span>
            </>
          ) : null}
          {" · created "}
          {when(d.created_at)}
        </span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => void copy(d.slug)}>
          <Icon name="copy" />
          Copy slug
        </Button>
      </div>
      <div className="facts">
        <div>
          <span className="k">Versions</span>
          <span className="v">{count(d.version_count)}</span>
        </div>
        <div>
          <span className="k">Latest</span>
          <span className="v">{latest?.version_id ?? "—"}</span>
        </div>
        <div>
          <span className="k">Rows in every version</span>
          <span className="v">{count(latest?.row_count)}</span>
        </div>
        <div>
          <span className="k">Accepted in latest</span>
          <span className="v">{latest ? count(statusCount(latest, "accepted")) : "—"}</span>
        </div>
        <div>
          <span className="k">Stored</span>
          <span className="v">{bytes(d.stored_bytes)}</span>
        </div>
      </div>
      <VersionsTable dataset={d} onContinue={onContinue} />
    </section>
  );
}

export function DatasetsScreen() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const datasets = useDatasets();
  const active = useActiveJobs();
  const [filter, setFilter] = useState("");
  const [drawer, setDrawer] = useState<StartFrom | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);

  useCrumbs(slug ? [{ label: "Datasets", to: "/datasets" }, { label: slug }] : [{ label: "Datasets" }]);

  // A stage that commits adds a version: a job message refreshes the lists.
  const onJob = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["datasets"] });
  }, [client]);
  useFeedMessage("job", onJob);

  const items = useMemo(() => datasets.data?.items ?? [], [datasets.data]);
  const shown = items.filter((d) => d.slug.includes(filter.trim().toLowerCase()));
  const selected = slug ?? items[0]?.slug;

  useEffect(() => {
    if (!slug && items[0]) void navigate(`/datasets/${items[0].slug}`, { replace: true });
  }, [slug, items, navigate]);

  const bindings = useMemo(() => ({ "/": () => filterRef.current?.focus() }), []);
  useShortcuts(bindings);

  const running = new Set((active.data?.items ?? []).map((j) => j.dataset));
  const openDrawer = (from: StartFrom) => {
    setDrawer(from);
  };

  if (datasets.isSuccess && items.length === 0) {
    return (
      <div className="scr02 single">
        <EmptyState
          icon="db"
          title="No datasets yet"
          action={
            <Button variant="primary" onClick={() => { openDrawer({ mode: "folder" }); }}>
              <Icon name="plus" />
              Ingest a folder
            </Button>
          }
        >
          Put a folder of images under <span className="mono">.\sources</span> on Windows (it is{" "}
          <span className="mono">/sources</span> inside the containers), then ingest it. Ingesting
          copies the files into Stratal&apos;s store and makes the first version.
        </EmptyState>
        {drawer ? <StartPipelineDrawer open from={drawer} onOpenChange={(o) => { if (!o) setDrawer(null); }} /> : null}
      </div>
    );
  }

  return (
    <div className="scr02">
      <section className="list" aria-label="Datasets">
        <div className="list-head">
          <h2>Datasets</h2>
          <span className="count">{datasets.isSuccess ? items.length : "…"}</span>
          <span className="spacer" />
          <Button size="sm" variant="primary" onClick={() => { openDrawer({ mode: "folder", dataset: selected }); }}>
            <Icon name="plus" />
            Ingest<span className="wide"> a folder</span>
          </Button>
        </div>
        <div className="filter">
          <Icon name="search" />
          <input
            ref={filterRef}
            className="input"
            placeholder="Filter datasets"
            aria-label="Filter datasets"
            value={filter}
            onChange={(e) => { setFilter(e.target.value); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") e.currentTarget.blur();
            }}
          />
          <Kbd>/</Kbd>
        </div>
        {datasets.isPending ? (
          <div aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="ds">
                <Skeleton width={90} />
                <Skeleton width={40} />
              </div>
            ))}
          </div>
        ) : datasets.isError ? (
          <ErrorState error={datasets.error} title="Datasets could not be loaded" onRetry={() => void datasets.refetch()} />
        ) : (
          <div role="listbox" aria-label="Datasets">
            {shown.map((d) => (
              <button
                key={d.slug}
                type="button"
                role="option"
                className="ds"
                aria-selected={d.slug === selected}
                tabIndex={d.slug === selected ? 0 : -1}
                onClick={() => void navigate(`/datasets/${d.slug}`)}
                onKeyDown={(e) => {
                  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
                  e.preventDefault();
                  const sibling = e.key === "ArrowDown" ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling;
                  if (sibling instanceof HTMLElement) sibling.focus();
                }}
              >
                <span className="slug">{d.slug}</span>
                <span className="r">{count(d.latest_version?.row_count)}</span>
                <span className="sub">
                  {d.version_count} {d.version_count === 1 ? "version" : "versions"}
                  {d.latest_version ? (
                    <>
                      {" · "}
                      <span className="wide">latest </span>
                      <span className="mono">{d.latest_version.stage}</span>
                    </>
                  ) : (
                    " · no versions yet"
                  )}
                </span>
                <span className="r">{acceptedShare(d.latest_version)}</span>
                <span className="sub">
                  {when(d.updated_at)}
                  {running.has(d.slug) ? (
                    <>
                      {" · "}
                      <StatePill state="running" />
                    </>
                  ) : null}
                </span>
              </button>
            ))}
            {shown.length === 0 ? <p className="foot-note" style={{ padding: "var(--sp-3)" }}>No dataset matches “{filter}”.</p> : null}
          </div>
        )}
      </section>
      {selected ? (
        <DatasetDetail
          slug={selected}
          onContinue={(v) => {
            openDrawer({ mode: "version", versionId: v.version_id, dataset: v.dataset });
          }}
        />
      ) : (
        <section className="detail" />
      )}
      {drawer ? (
        <StartPipelineDrawer
          open
          from={drawer}
          onOpenChange={(o) => {
            if (!o) setDrawer(null);
          }}
        />
      ) : null}
    </div>
  );
}
