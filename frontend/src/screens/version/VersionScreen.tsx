// SCR-03 version detail and lineage (SCREENS.md §3; mockup SCR-03_v1; DATA-3.3, DATA-2.1,
// DATA-2.2, DATA-6.1, DATA-5.1, UI-2.1).
import * as Menu from "@radix-ui/react-dropdown-menu";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type ReactNode, useCallback, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Schemas } from "../../api/client";
import { type RowFilters, useLatestReport, useLineage, useRows, useStats, useVersion, useVersionModels } from "../../api/queries";
import { useFeedMessage } from "../../api/stream";
import { Modal, ModalClose } from "../../components/Modal";
import { copy, ErrorState } from "../../components/states";
import { Badge, Button, Icon, type IconName, Skeleton } from "../../components/ui";
import { bytes, count } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";
import { TrainDrawer, trainable } from "../build/TrainDrawer";
import { StartPipelineDrawer } from "../datasets/StartPipelineDrawer";
import { ColumnsTab, transformOf } from "./ColumnsTab";
import { CompareTab } from "./CompareVersions";
import { DistributionsTab } from "./Distributions";
import { LineageStrip } from "./LineageStrip";
import { ModelsTab } from "./ModelsTab";
import { RowDrawer } from "./RowDrawer";
import { count as versionCount, type Row, STATUS_COLOR, type Status, type Version } from "./rows";
import { RowsPanel, RowStatus, STATUSES } from "./RowsPanel";

type Stats = Schemas["Stats"];
type Lineage = Schemas["Lineage"];

const JOB_ICON: Record<string, IconName> = { completed: "check", failed: "x", cancelled: "ban", running: "play", stalled: "hourglass" };
const ADVICE: Record<string, string> = {
  below_min_size: "ingest again with a smaller min_size, if these images are meant to be used.",
  corrupt_image: "check the files open in an image viewer; re-export them if they do not.",
  unsupported_format: "convert the files to JPEG or PNG, then ingest again.",
  clip_below_threshold: "continue from the label version with a lower filter threshold, if the captions look right.",
};

function when(iso: string): string {
  const date = new Date(iso);
  const today = new Date().toDateString() === date.toDateString();
  return today
    ? date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function configOf(lineage: Lineage | undefined, version: Version, stage: string): Record<string, unknown> | null {
  if (version.stage === stage) return version.config;
  const v = lineage?.ancestors.find((a) => a.stage === stage);
  return v ? v.config : null;
}

interface SemanticCounts {
  clusters: number;
  rows_in_clusters: number;
  largest: number;
  threshold: number;
}

/** The label version's semantic clusters (DATA-2.3, ADR-0013 rule 5), carried by its children. */
function semanticOf(version: Version, lineage: Lineage | undefined): SemanticCounts | null {
  const own = (version.counts as Record<string, unknown>)["semantic"];
  const label = lineage?.ancestors.find((a) => a.stage === "label");
  const found = own ?? (label ? (label.counts as Record<string, unknown>)["semantic"] : undefined);
  if (!found || typeof found !== "object") return null;
  const s = found as SemanticCounts;
  return s.clusters > 0 ? s : null;
}

function Tiles({ version, stats, lineage, pressed, onPress, semantic, onSemantic }: {
  version: Version;
  stats: Stats | undefined;
  lineage: Lineage | undefined;
  pressed: Status | null;
  onPress: (s: Status | null) => void;
  semantic: boolean;
  onSemantic: (on: boolean) => void;
}) {
  const clusters = semanticOf(version, lineage);
  const reasons = stats?.reasons ?? [];
  const rows = (code: string) => reasons.find((r) => r.reason_code === code)?.rows ?? 0;
  const total = version.row_count || 1;
  const pre = configOf(lineage, version, "preprocess");
  const filter = configOf(lineage, version, "filter");
  const rejected = reasons.filter((r) => r.status === "rejected");
  const rejectStages = [...new Set(rejected.map((r) => r.decided_at_stage))];
  const context: Record<Status, string> = {
    accepted: `${((versionCount(version, "accepted") / total) * 100).toFixed(1)}%${pre && typeof pre.max_side === "number" ? ` · copies ≤ ${String(pre.max_side)} px` : ""}`,
    rejected: rejected.length ? `at ${rejectStages.join(", ")} · ${String(rejected.length)} ${rejected.length === 1 ? "reason" : "reasons"}` : "none",
    duplicate: configOf(lineage, version, "clean") ? `${count(rows("exact_duplicate"))} exact · ${count(rows("near_duplicate"))} near` : "no clean stage yet",
    filtered: filter
      ? `${((versionCount(version, "filtered") / total) * 100).toFixed(1)}% · CLIP below ${String(filter.clip_threshold)}`
      : "no filter stage yet",
  };
  return (
    <div className={clusters ? "tiles five" : "tiles"}>
      {STATUSES.map((s) => {
        const n = versionCount(version, s);
        const word = s.charAt(0).toUpperCase() + s.slice(1);
        return (
          <button
            key={s}
            type="button"
            className="stile"
            style={{ "--c": STATUS_COLOR[s] } as CSSProperties}
            aria-pressed={pressed === s}
            aria-label={`${word}, ${count(n)} rows${pressed === s ? ", filtering the rows" : ""}`}
            onClick={() => { onPress(pressed === s ? null : s); }}
          >
            <span className="k">
              <i />
              {word}
            </span>
            <span className="v">{count(n)}</span>
            <span className="d">{context[s]}</span>
          </button>
        );
      })}
      {clusters ? (
        <button
          type="button"
          className="stile sem"
          aria-pressed={semantic}
          aria-label={`In a semantic cluster, ${count(clusters.rows_in_clusters)} rows in ${count(clusters.clusters)} clusters${semantic ? ", filtering the rows" : ""}`}
          onClick={() => { onSemantic(!semantic); }}
        >
          <span className="k"><i />Semantic clusters</span>
          <span className="v">{count(clusters.clusters)}</span>
          <span className="d">{count(clusters.rows_in_clusters)} rows · largest {count(clusters.largest)}</span>
        </button>
      ) : null}
    </div>
  );
}

function Reasons({ stats, onShow }: { stats: Stats; onShow: (code: string) => void }) {
  const max = Math.max(1, ...stats.reasons.map((r) => r.rows));
  if (stats.reasons.length === 0) return <p className="foot-note">Every row is accepted: no reasons to show.</p>;
  return (
    <div className="table-wrap" style={{ maxWidth: 820 }}>
      <table className="t" aria-label="Reasons">
        <thead>
          <tr>
            <th>Reason</th>
            <th>Decided at</th>
            <th>Status</th>
            <th className="r">Rows</th>
            <th className="r">Share</th>
            <th style={{ width: 180 }}><span className="sr-only">Bar</span></th>
            <th><span className="sr-only">Show rows</span></th>
          </tr>
        </thead>
        <tbody>
          {stats.reasons.map((r) => (
            <tr key={`${r.reason_code}-${r.decided_at_stage}`}>
              <td className="reason">{r.reason_code}</td>
              <td>{r.decided_at_stage}</td>
              <td>
                <RowStatus status={r.status as Status} />
              </td>
              <td className="r">{count(r.rows)}</td>
              <td className="r">{(r.share * 100).toFixed(1)}%</td>
              <td aria-hidden="true">
                <span className="reason-bar" style={{ "--c": STATUS_COLOR[r.status as Status], width: Math.max(2, (r.rows / max) * 160) } as CSSProperties} />
              </td>
              <td>
                <Button size="sm" variant="ghost" onClick={() => { onShow(r.reason_code); }}>
                  Show rows
                  <Icon name="chevron" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/g;

/** Resolved config as JSON, keys, strings and numbers coloured as the design system's code view. */
function Json({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    out.push(text.slice(last, m.index));
    if (m[1]) {
      out.push(<span key={m.index} className={m[2] ? "k" : "s"}>{m[1]}</span>);
      if (m[2]) out.push(m[2]);
    } else {
      out.push(<span key={m.index} className="n">{m[3]}</span>);
    }
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <pre className="code">{out}</pre>;
}

function Config({ version }: { version: Version }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: "var(--sp-6)", overflow: "auto" }}>
      <div>
        <h3 className="sec">Stage config (resolved)</h3>
        <Json value={version.config} />
      </div>
      <div>
        <h3 className="sec">Identity and storage</h3>
        <div className="kv">
          <span>Version ID</span>
          <span className="mono">{version.version_id}</span>
          <span>Content hash</span>
          <span className="mono">{version.content_hash}</span>
          <span>Config hash</span>
          <span className="mono">{version.config_hash}</span>
          <span>Manifest</span>
          <span>
            <span className="mono">{version.manifest_path}</span> · {bytes(version.manifest_bytes)}
          </span>
          <span>New files</span>
          <span>{bytes(version.new_blob_bytes)}</span>
          <span>Dataset type</span>
          <span className={version.dtype ? "mono" : "muted"}>{version.dtype ?? "not validated yet"}</span>
          <span>Schema version</span>
          <span className="mono">{version.schema_version}</span>
          <span>Code version</span>
          <span className={version.code_version ? "mono" : "muted"}>{version.code_version ?? "not recorded"}</span>
        </div>
      </div>
    </div>
  );
}

/** Half-width in points of a 95% interval for an acceptance near 90% (frame B's hint). */
function halfWidth(n: number): number {
  return Math.round(196 * Math.sqrt((0.9 * 0.1) / Math.max(1, n)));
}

function ReviewModal({ version, open, onOpenChange }: { version: Version; open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const accepted = versionCount(version, "accepted");
  const [size, setSize] = useState(String(Math.min(50, accepted)));
  const [seed, setSeed] = useState("");
  const start = useMutation({
    mutationFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/reviews", {
          params: { path: { version_id: version.version_id } },
          body: { sample_size: Number(size), ...(seed ? { seed: Number(seed) } : {}) },
        }),
      ),
    onSuccess: (review) => {
      onOpenChange(false);
      void navigate(`/reviews/${(review as { id: string }).id}`);
    },
  });
  const n = Number(size);
  const bad = !Number.isInteger(n) || n < 1 || n > Math.min(1000, accepted);
  const problem = start.error instanceof ApiError ? start.error.problem : null;
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={`Start a review of ${version.version_id}`}
      icon={<Icon name="eye" style={{ color: "var(--accent)" }} />}
      footer={
        <>
          <ModalClose asChild>
            <Button variant="ghost">Cancel</Button>
          </ModalClose>
          <Button variant="primary" disabled={bad || start.isPending} onClick={() => { start.mutate(); }}>
            {start.isPending ? (<><span className="spinner" />Drawing…</>) : (<><Icon name="eye" />Start review</>)}
          </Button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        <div className={bad ? "field error" : "field"}>
          <label htmlFor="rv-size">Sample size</label>
          <input id="rv-size" className="input mono" value={size} onChange={(e) => { setSize(e.target.value); }} aria-invalid={bad} />
          <small>
            {bad
              ? `A whole number from 1 to ${count(Math.min(1000, accepted))}.`
              : `Drawn at random from the ${count(accepted)} accepted rows. ${String(n)} gives an interval about ±${String(halfWidth(n))} points wide; ${String(n * 4)} about ±${String(halfWidth(n * 4))}.`}
          </small>
        </div>
        <div className="field">
          <label htmlFor="rv-seed">Seed, optional</label>
          <input id="rv-seed" className="input mono" value={seed} placeholder="random" onChange={(e) => { setSeed(e.target.value.replace(/\D/g, "")); }} />
          <small>Set it to draw the same sample again.</small>
        </div>
      </div>
      {problem ? (
        <p role="alert" style={{ color: "var(--danger)", marginTop: "var(--sp-3)" }}>
          <Badge tone="err">{problem.code}</Badge> {problem.detail}
        </p>
      ) : null}
    </Modal>
  );
}

/** Where SCR-15 opens from this version (SCREENS.md v4): always the raw version in the
 * lineage, with the transform's operations loaded when there is one. Tables only. */
function transformLink(version: Version, lineage: Lineage | undefined): { to: string; edit: boolean } | null {
  if (version.dtype !== "tabular") return null;
  const raw = version.stage === "ingest" ? version.version_id : lineage?.ancestors.find((a) => a.stage === "ingest")?.version_id;
  if (!raw) return null;
  const t = transformOf(version.version_id, version.stage, version.config, lineage);
  return t ? { to: `/versions/${raw}/transform?from=${t.versionId}`, edit: true } : { to: `/versions/${raw}/transform`, edit: false };
}

function Actions({ version, stats, lineage, onContinue, onReview, onTrain, compact }: {
  version: Version;
  stats: Stats | undefined;
  lineage?: Lineage | undefined;
  onContinue: () => void;
  onReview: () => void;
  onTrain: () => void;
  compact?: boolean;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const captioned = stats ? stats.clip_score !== null : false;
  const reportable = version.stage === "split" || version.stage === "filter";
  const report = useLatestReport(version.version_id, reportable);
  const build = useMutation({
    mutationFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/reports", {
          params: { path: { version_id: version.version_id } },
          body: { kind: "phase1_dataset" },
        }),
      ),
    onSuccess: (job) => {
      void client.invalidateQueries({ queryKey: ["versions", version.version_id, "report"] });
      void navigate(`/versions/${version.version_id}/report?job=${job.id}`); // SCR-06 follows the build (frame B)
    },
  });
  const allRejected = versionCount(version, "accepted") === 0;
  const why = "Needs captions: run the label stage first";
  const review = (
    <Button
      variant={captioned ? "primary" : "secondary"}
      aria-disabled={!captioned || undefined}
      className={captioned ? undefined : "is-disabled"}
      title={captioned ? undefined : why}
      aria-describedby={captioned ? undefined : "why-review"}
      onClick={() => { if (captioned) onReview(); }}
    >
      <Icon name="eye" />
      Start a review
    </Button>
  );
  const reportButton = reportable ? (
    report.data ? (
      <Button onClick={() => void navigate(`/versions/${version.version_id}/report`)}>
        <Icon name="report" />
        Open report
      </Button>
    ) : (
      <Button disabled={build.isPending || report.isPending} onClick={() => { build.mutate(); }}>
        <Icon name="report" />
        {build.isPending ? "Starting…" : "Build the eval report"}
      </Button>
    )
  ) : null;
  // Train a model… (SCREENS.md v3): on a version a model trains on, else disabled with why.
  const trainOk = trainable(version);
  const whyTrain = "Training needs a split version (a tokenize version for text).";
  const trainButton = (
    <Button
      aria-disabled={!trainOk || undefined}
      className={trainOk ? undefined : "is-disabled"}
      title={trainOk ? undefined : whyTrain}
      aria-describedby={trainOk ? undefined : "why-train"}
      onClick={() => { if (trainOk) onTrain(); }}
    >
      <Icon name="plus" />
      Train a model…
    </Button>
  );
  const tx = transformLink(version, lineage);
  const transformButton = tx ? (
    <Button onClick={() => void navigate(tx.to)}>
      <Icon name="sliders" />
      {tx.edit ? "Edit the transform…" : "Transform…"}
    </Button>
  ) : null;
  const continueButton = (
    <Button
      variant={captioned ? "secondary" : "primary"}
      aria-disabled={allRejected || undefined}
      className={allRejected ? "is-disabled" : undefined}
      title={allRejected ? "No row was accepted: there is nothing to continue from." : undefined}
      onClick={() => { if (!allRejected) onContinue(); }}
    >
      <Icon name="branch" />
      Continue the pipeline
    </Button>
  );
  return (
    <>
      <span className="wide-action">
        <Button size="sm" variant="ghost" onClick={() => void copy(version.version_id)}>
          <Icon name="copy" />
          Copy ID
        </Button>
      </span>
      {review}
      <span id="why-review" className="sr-only">{captioned ? "" : why}</span>
      <span id="why-train" className="sr-only">{trainOk ? "" : whyTrain}</span>
      <span className="wide-action">
        {transformButton}
        {trainButton}
        {reportButton}
        {version.created_by_job_id ? (
          <Button onClick={() => void navigate(`/jobs/${version.created_by_job_id ?? ""}`)}>
            <Icon name="list" />
            Open job
          </Button>
        ) : null}
      </span>
      {compact ? null : (
        <span className="narrow-action">
          <Menu.Root>
            <Menu.Trigger asChild>
              <Button size="sm">
                <Icon name="more" />
                More
              </Button>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu-pop" align="end" sideOffset={4}>
                <Menu.Item onSelect={() => void copy(version.version_id)}><Icon name="copy" />Copy ID</Menu.Item>
                {version.created_by_job_id ? (
                  <Menu.Item onSelect={() => void navigate(`/jobs/${version.created_by_job_id ?? ""}`)}><Icon name="list" />Open job</Menu.Item>
                ) : null}
                {tx ? (
                  <Menu.Item onSelect={() => void navigate(tx.to)}><Icon name="sliders" />{tx.edit ? "Edit the transform…" : "Transform…"}</Menu.Item>
                ) : null}
                <Menu.Item disabled={!trainOk} onSelect={onTrain}><Icon name="plus" />Train a model…</Menu.Item>
                {reportable ? (
                  <Menu.Item onSelect={() => { if (report.data) void navigate(`/versions/${version.version_id}/report`); else build.mutate(); }}>
                    <Icon name="report" />
                    {report.data ? "Open report" : "Build the eval report"}
                  </Menu.Item>
                ) : null}
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        </span>
      )}
      {continueButton}
    </>
  );
}

export function VersionScreen() {
  const { versionId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const version = useVersion(versionId);
  const lineage = useLineage(versionId);
  const stats = useStats(versionId);
  const [drawer, setDrawer] = useState(false);
  const [review, setReview] = useState(false);
  const [training, setTraining] = useState(false);
  const [selected, setSelected] = useState(0);
  const [loaded, setLoaded] = useState<Row[]>([]);

  const v = version.data;
  useCrumbs([
    { label: "Datasets", to: "/datasets" },
    ...(v ? [{ label: v.dataset, to: `/datasets/${v.dataset}` }] : []),
    { label: versionId, id: true },
  ]);

  // A job extending this version changes its lineage (the running child).
  const onJob = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["versions", versionId, "lineage"] });
  }, [client, versionId]);
  useFeedMessage("job", onJob);

  const accepted = v ? versionCount(v, "accepted") : 1;
  const modelsHere = v ? trainable(v) : false;
  const models = useVersionModels(versionId, modelsHere);
  const status = (params.get("status") ?? (v && accepted === 0 ? "rejected" : null)) as Status | null;
  const filters: RowFilters = useMemo(() => {
    const f: RowFilters = {};
    if (status) f.status = status;
    const reason = params.get("reason");
    if (reason) f.reason_code = reason;
    const split = params.get("split");
    if (split === "train" || split === "val" || split === "test") f.split = split;
    const q = params.get("q");
    if (q) f.q = q;
    if (params.get("semantic") === "1") f.semantic = true;
    return f;
  }, [params, status]);
  const tab = params.get("tab") ?? "rows";
  const view = params.get("view") === "grid" ? "grid" : "table";
  const openKey = params.get("row");

  const set = useCallback(
    (patch: Record<string, string | null>) => {
      setParams((current) => {
        const next = new URLSearchParams(current);
        for (const [k, value] of Object.entries(patch)) {
          if (value === null) next.delete(k);
          else next.set(k, value);
        }
        return next;
      }, { replace: true });
      if ("status" in patch || "reason" in patch || "split" in patch || "q" in patch || "semantic" in patch) setSelected(0);
    },
    [setParams],
  );

  // A deep-linked row that is not among the loaded rows is looked up by its key.
  const lookup = useRows(versionId, { q: openKey ?? "" }, Boolean(openKey) && !loaded.some((r) => r.row_key === openKey));
  const openRow =
    loaded.find((r) => r.row_key === openKey) ??
    lookup.data?.pages.flatMap((p) => p.items).find((r) => r.row_key === openKey) ??
    null;
  const threshold = (() => {
    const c = v ? configOf(lineage.data, v, "clean") : null;
    return c && typeof c.phash_max_distance === "number" ? c.phash_max_distance : null;
  })();

  if (version.isPending) {
    return (
      <div className="scr03" aria-busy="true">
        <Skeleton width={280} height={22} />
        <Skeleton width={420} />
        <span className="skeleton" style={{ height: 62, display: "block" }} />
        <div className="tiles">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="skeleton" style={{ height: 64, display: "block" }} />
          ))}
        </div>
      </div>
    );
  }
  if (version.isError || !v) {
    const notFound = version.error instanceof ApiError && version.error.problem.status === 404;
    return (
      <div className="scr03">
        {notFound ? (
          <div className="state err" role="alert">
            <Icon name="alert" />
            <b>Version {versionId} not found</b>
            <Badge tone="err">not_found</Badge>
            <p>
              No dataset version &apos;{versionId}&apos;.
              <br />
              <b style={{ color: "var(--text-primary)" }}>Do:</b> check the link, or find the version in its dataset.
            </p>
            <Button size="sm" onClick={() => void navigate("/datasets")}>
              <Icon name="db" />
              Go to datasets
            </Button>
          </div>
        ) : (
          <ErrorState error={version.error} title={`Version ${versionId} could not be loaded`} onRetry={() => void version.refetch()} />
        )}
      </div>
    );
  }

  const parent = lineage.data?.ancestors[0];
  // Models train on a split (and on a tokenize version of texts): SCR-03 v2 frame D.
  const jobState = v.created_by_job_state;
  const reasons = stats.data?.reasons.map((r) => r.reason_code) ?? [];
  const splits = stats.data?.by_split ? Object.keys(stats.data.by_split) : [];
  const labelled = stats.data ? stats.data.clip_score !== null : false;
  const topReason = stats.data?.reasons[0];
  const captioner = (configOf(lineage.data, v, "label") ?? {}).captioner;

  return (
    <div className="scr03">
      <div className="vhead">
        <div>
          <h1>
            {v.version_id} <span className="stage-tag">{v.stage}</span>
          </h1>
          <div className="sub">
            {v.dataset} · made {when(v.created_at)}
            {v.created_by_job_id ? (
              <>
                {" by "}
                <span className="mono">{v.created_by_job_id}</span>
                {jobState ? (
                  <Icon name={JOB_ICON[jobState] ?? "clock"} style={{ color: `var(--state-${jobState})` }} />
                ) : null}
                {jobState ? <span className="sr-only">{jobState}</span> : null}
              </>
            ) : (
              " from the CLI"
            )}
            {" · "}
            {parent ? (
              <>
                from <span className="mono">{parent.version_id}</span> ({parent.stage})
              </>
            ) : (
              "raw"
            )}
            {typeof captioner === "string" ? <> · captions by <span className="mono">{captioner}</span></> : null}
          </div>
        </div>
        <span className="spacer" />
        <Actions version={v} stats={stats.data} lineage={lineage.data} onContinue={() => { setDrawer(true); }} onReview={() => { setReview(true); }} onTrain={() => { setTraining(true); }} />
      </div>

      {lineage.data ? (
        <LineageStrip
          lineage={lineage.data}
          current={v}
          models={modelsHere ? (models.data?.items.length ?? null) : null}
          onModels={() => { set({ tab: "models" }); }}
        />
      ) : lineage.isError ? (
        <ErrorState error={lineage.error} title="Lineage could not be loaded" onRetry={() => void lineage.refetch()} />
      ) : (
        <span className="skeleton" style={{ height: 62, display: "block" }} />
      )}

      {accepted === 0 && topReason ? (
        <div className="notice" role="status">
          <Icon name="alert" />
          <div>
            <b>No row was accepted.</b>{" "}
            {topReason.rows === v.row_count ? `All ${count(v.row_count)} rows were` : `${count(topReason.rows)} of ${count(v.row_count)} rows were`}{" "}
            {topReason.status} as <span className="mono">{topReason.reason_code}</span>.{" "}
            {ADVICE[topReason.reason_code] ? (
              <>
                <b>Do:</b> {ADVICE[topReason.reason_code]}
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      <Tiles
        version={v}
        stats={stats.data}
        lineage={lineage.data}
        pressed={status}
        onPress={(s) => { set({ status: s, semantic: null, tab: null }); }}
        semantic={params.get("semantic") === "1"}
        onSemantic={(on) => { set({ semantic: on ? "1" : null, status: null, tab: null }); }}
      />

      <Tabs.Root className="panel" value={tab} onValueChange={(t) => { set({ tab: t === "rows" ? null : t }); }}>
        <Tabs.List className="tabs" aria-label="Version detail">
          <Tabs.Trigger className="tab" value="rows">
            Rows <span className="count">{count(v.row_count)}</span>
          </Tabs.Trigger>
          {v.dtype === "tabular" ? (
            <Tabs.Trigger className="tab" value="columns">
              Columns
            </Tabs.Trigger>
          ) : null}
          <Tabs.Trigger className="tab" value="reasons">
            Reasons <span className="count">{stats.data ? stats.data.reasons.length : "…"}</span>
          </Tabs.Trigger>
          <Tabs.Trigger className="tab" value="distributions">
            Distributions
          </Tabs.Trigger>
          <Tabs.Trigger className="tab" value="compare">
            Compare
          </Tabs.Trigger>
          {modelsHere ? (
            <Tabs.Trigger className="tab" value="models">
              Models <span className="count">{models.data ? count(models.data.items.length) : "…"}</span>
            </Tabs.Trigger>
          ) : null}
          <Tabs.Trigger className="tab" value="config">
            Config
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="rows" className="panel" tabIndex={-1}>
          <RowsPanel
            versionId={v.version_id}
            filters={filters}
            setFilter={(patch) => {
              set({ ...patch, ...("reason" in patch ? { reason: patch.reason ?? null } : {}) });
            }}
            reasons={reasons}
            splits={splits}
            labelled={labelled}
            view={view}
            selected={selected}
            onSelect={setSelected}
            onOpen={(i, rows) => {
              setSelected(i);
              setLoaded(rows);
              set({ row: rows[i]?.row_key ?? null });
            }}
          />
        </Tabs.Content>
        {v.dtype === "tabular" ? (
          <Tabs.Content value="columns" className="panel scroll">
            <ColumnsTab
              versionId={v.version_id}
              stage={v.stage}
              config={v.config}
              lineage={lineage.data}
              active={tab === "columns"}
              onShowRows={(reason) => { set({ reason: reason || null, status: reason ? null : "rejected", tab: null }); }}
              onEditTransform={(raw, from) => void navigate(`/versions/${raw}/transform?from=${from}`)}
            />
          </Tabs.Content>
        ) : null}
        <Tabs.Content value="reasons" className="panel">
          {stats.data ? (
            <Reasons stats={stats.data} onShow={(code) => { set({ reason: code, status: null, tab: null }); }} />
          ) : stats.isError ? (
            <ErrorState error={stats.error} title="Counts could not be loaded" onRetry={() => void stats.refetch()} />
          ) : (
            <Skeleton width={400} />
          )}
        </Tabs.Content>
        <Tabs.Content value="distributions" className="panel scroll">
          <DistributionsTab versionId={v.version_id} active={tab === "distributions"} onReason={(code) => { set({ reason: code, status: null, tab: null }); }} />
        </Tabs.Content>
        <Tabs.Content value="compare" className="panel scroll">
          {tab === "compare" ? (
            <CompareTab
              versionId={v.version_id}
              slug={v.dataset}
              stage={v.stage}
              createdAt={v.created_at}
              parentId={parent?.version_id ?? null}
              other={params.get("with")}
              onOther={(id, swap) => {
                if (swap && id) void navigate(`/versions/${id}?tab=compare&with=${v.version_id}`);
                else set({ with: id });
              }}
              onOpen={(key) => { set({ row: key }); }}
            />
          ) : null}
        </Tabs.Content>
        {modelsHere ? (
          <Tabs.Content value="models" className="panel scroll">
            <ModelsTab versionId={v.version_id} active={tab === "models"} onTrain={() => { setTraining(true); }} />
          </Tabs.Content>
        ) : null}
        <Tabs.Content value="config" className="panel">
          <Config version={v} />
        </Tabs.Content>
      </Tabs.Root>

      {openRow ? (
        <RowDrawer
          versionId={v.version_id}
          row={openRow}
          threshold={threshold}
          onClose={() => { set({ row: null }); }}
          onMove={(delta) => {
            const i = loaded.findIndex((r) => r.row_key === openRow.row_key);
            const next = loaded[i + delta];
            if (i >= 0 && next) {
              setSelected(i + delta);
              set({ row: next.row_key });
            }
          }}
          onOpenRow={(key) => { set({ row: key }); }}
        />
      ) : null}
      {drawer ? (
        <StartPipelineDrawer open from={{ mode: "version", versionId: v.version_id, dataset: v.dataset }} onOpenChange={(o) => { if (!o) setDrawer(false); }} />
      ) : null}
      {review ? <ReviewModal version={v} open onOpenChange={setReview} /> : null}
      {training ? <TrainDrawer open version={v.version_id} onOpenChange={setTraining} /> : null}
    </div>
  );
}
