// SCR-06 eval report (SCREENS.md §3; mockup SCR-06_v1; EVAL-1.1): what each stage removed,
// the CLIP score distribution with the filter threshold, the filter rate, and the review's
// acceptance with its Wilson interval. Every chart has a table with the same numbers.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useCallback, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Job, type Schemas } from "../../api/client";
import { useLineage, useVersion } from "../../api/queries";
import { useFeedMessage } from "../../api/stream";
import { ErrorState } from "../../components/states";
import { Badge, Button, Icon, Progress, Skeleton, StatePill } from "../../components/ui";
import { clock, count, errorDetail, stagePercent } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";
import { RetrievalReport } from "./RetrievalReport";

type Report = Schemas["Report"];
type Body = Schemas["ReportBody"];
type Distribution = Schemas["Distribution"];

const PHASE1 = ["ingest", "clean", "preprocess", "label", "filter", "split"];
const DUPLICATE = new Set(["exact_duplicate", "near_duplicate", "semantic_duplicate"]);
const FILTERED = new Set(["clip_below_threshold"]);
type Kind = "rejected" | "duplicate" | "filtered";
const KIND_COLOR: Record<Kind, string> = { rejected: "var(--danger)", duplicate: "var(--text-muted)", filtered: "var(--warning)" };

function kindOf(reason: string): Kind {
  return DUPLICATE.has(reason) ? "duplicate" : FILTERED.has(reason) ? "filtered" : "rejected";
}

const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;

/** The report a page shows: a named one (?report=), else the version's newest. */
function useReport(versionId: string, reportId: string | null) {
  return useQuery({
    queryKey: ["versions", versionId, "report", reportId ?? "latest"],
    queryFn: async (): Promise<Report | null> => {
      try {
        return reportId
          ? await call(() => api.GET("/api/v1/reports/{report_id}", { params: { path: { report_id: Number(reportId) } } }))
          : await call(() =>
              api.GET("/api/v1/versions/{version_id}/reports/latest", {
                params: { path: { version_id: versionId }, query: { kind: "phase1_dataset" } },
              }),
            );
      } catch (error) {
        // "No report yet" is an empty state, not an error; an unknown version still is one.
        if (error instanceof ApiError && error.problem.status === 404 && /no .*report/i.test(error.problem.detail)) return null;
        throw error;
      }
    },
    retry: false,
  });
}

function useReviews(versionId: string) {
  return useQuery({
    queryKey: ["versions", versionId, "reviews"],
    queryFn: () => call(() => api.GET("/api/v1/versions/{version_id}/reviews", { params: { path: { version_id: versionId } } })),
  });
}

function useThreshold(versionId: string): number | null {
  const version = useVersion(versionId);
  const lineage = useLineage(versionId);
  const all = [...(version.data ? [version.data] : []), ...(lineage.data?.ancestors ?? [])];
  const value = all.find((v) => v.stage === "filter")?.config.clip_threshold;
  return typeof value === "number" ? value : null;
}

function Toggle({ table, onChange, label }: { table: boolean; onChange: (t: boolean) => void; label: string }) {
  return (
    <span className="group-btns" role="group" aria-label={`${label}: chart or table`}>
      <button type="button" className="btn sm" aria-pressed={!table} onClick={() => { onChange(false); }}>
        <Icon name="chart" />
        Chart
      </button>
      <button type="button" className="btn sm" aria-pressed={table} onClick={() => { onChange(true); }}>
        <Icon name="grid" />
        Table
      </button>
    </span>
  );
}

function Interval({ low, high, estimate }: { low: number; high: number; estimate: number }) {
  return (
    <>
      <div className="ci" aria-hidden="true">
        <span className="axis" />
        <span className="tick" style={{ left: 0 }} />
        <span className="tick" style={{ left: "50%" }} />
        <span className="tick" style={{ left: "calc(100% - 1px)" }} />
        <span className="range" style={{ left: pct(low), width: pct(high - low) }} />
        <span className="pt" style={{ left: pct(estimate) }} />
      </div>
      <div className="ci-scale" aria-hidden="true">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
    </>
  );
}

function Headlines({ body, threshold, versionId }: { body: Body; threshold: number | null; versionId: string }) {
  const navigate = useNavigate();
  const stages = PHASE1.slice(0, PHASE1.indexOf(body.stage) + 1).filter((s) => s !== "split" || body.stage === "split");
  const filtered = Object.values(body.rejections.by_stage.filter ?? {}).reduce((a, b) => a + b, 0);
  const captioned = body.clip_scores?.labelled.count ?? null;
  const acc = body.review?.acceptance?.overall;
  const interval = acc?.interval;
  return (
    <div className="heads">
      <div className="head" style={{ "--c": "var(--success)" } as CSSProperties}>
        <span className="k">Rows kept</span>
        <span className="v">{count(body.rows.accepted)}</span>
        <span className="d">
          of {count(body.rows.total)} raw · {pct(body.rows.accepted / Math.max(1, body.rows.total))} · after {stages.length} stages
        </span>
      </div>
      <div className="head" style={{ "--c": "var(--warning)" } as CSSProperties}>
        <span className="k">Filter rate</span>
        {body.filter_rate === null ? (
          <span className="v">—</span>
        ) : (
          <span className="v">
            {(body.filter_rate * 100).toFixed(2)}
            <small>%</small>
          </span>
        )}
        <span className="d">
          {count(filtered)} of {count(captioned)} captioned rows
          {threshold !== null ? (
            <>
              {" "}below CLIP <span className="mono">{threshold.toFixed(2)}</span>
            </>
          ) : null}
        </span>
      </div>
      {acc && interval && interval.low !== null && interval.high !== null && interval.estimate !== null ? (
        <div className="head">
          <span className="k">Review acceptance</span>
          <span className="v">
            {(interval.estimate * 100).toFixed(1)}
            <small>%</small>
          </span>
          <span className="d long">
            {acc.accepted} of {acc.decided} · {Math.round(interval.confidence * 100)}% Wilson interval{" "}
            <span className="mono">
              {pct(interval.low)} – {pct(interval.high)}
            </span>
          </span>
          <span className="d short mono">
            {pct(interval.low)} – {pct(interval.high)}
          </span>
          <Interval low={interval.low} high={interval.high} estimate={interval.estimate} />
          {body.review?.acceptance?.by_split ? (
            <span className="splits d long" aria-label="Acceptance per split">
              {Object.entries(body.review.acceptance.by_split).map(([split, r]) => (
                <span key={split}>
                  <b>{split}</b> {r.accepted}/{r.decided}
                  {r.interval.low !== null && r.interval.high !== null ? ` · ${pct(r.interval.low, 0)}–${pct(r.interval.high, 0)}` : ""}
                </span>
              ))}
            </span>
          ) : null}
        </div>
      ) : (
        <div className="head none">
          <span className="k">Review acceptance</span>
          <span className="v">— not measured</span>
          <span className="d">No closed review existed when this report was built.</span>
          <span className="d">
            <b style={{ color: "var(--text-primary)" }}>Do</b> start a review of this version, close it, then rebuild.
          </span>
          <Button size="sm" style={{ alignSelf: "flex-start", marginTop: "var(--sp-2)" }} onClick={() => void navigate(`/versions/${versionId}`)}>
            <Icon name="eye" />
            Start a review
          </Button>
        </div>
      )}
    </div>
  );
}

function Funnel({ body }: { body: Body }) {
  const [table, setTable] = useState(false);
  const last = PHASE1.indexOf(body.stage);
  const stages = PHASE1.slice(0, last + 1);
  const total = body.rows.total || 1;
  const rows: { stage: string; kept: number; removed: number; byKind: Partial<Record<Kind, number>>; main: Kind }[] = [];
  let kept = body.rows.total;
  for (const stage of stages) {
    const reasons = body.rejections.by_stage[stage] ?? {};
    const byKind: Partial<Record<Kind, number>> = {};
    for (const [reason, n] of Object.entries(reasons)) byKind[kindOf(reason)] = (byKind[kindOf(reason)] ?? 0) + n;
    const removed = Object.values(reasons).reduce((a, b) => a + b, 0);
    kept -= removed;
    const main = (Object.entries(byKind) as [Kind, number][]).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "rejected";
    rows.push({ stage, kept, removed, byKind, main });
  }
  const maxRemoved = Math.max(1, ...rows.map((r) => r.removed));
  const reasonRows = Object.entries(body.rejections.by_stage)
    .sort((a, b) => PHASE1.indexOf(a[0]) - PHASE1.indexOf(b[0]))
    .flatMap(([stage, reasons]) => Object.entries(reasons).map(([reason, n]) => ({ stage, reason, n })));
  return (
    <section className="panel" aria-label="Rows by stage">
      <header>
        <h2>Rows by stage</h2>
        <span className="sub">rejections per stage</span>
        <span className="spacer" />
        <Toggle table={table} onChange={setTable} label="Rows by stage" />
      </header>
      {table ? null : (
        <>
          <div className="funnel" role="img" aria-label={rows.map((r) => `${r.stage}: ${count(r.kept)} kept, ${count(r.removed)} removed`).join("; ")}>
            <div className="fhead">
              <span>Stage</span>
              <span>Kept rows</span>
              <span style={{ textAlign: "right" }}>Kept</span>
              <span>Removed</span>
              <span />
            </div>
            <div className="frow">
              <span className="st">raw</span>
              <span className="kept raw" style={{ width: "100%" }} />
              <span className="num">{count(body.rows.total)}</span>
              <span />
              <span />
            </div>
            {rows.map((r) => (
              <div key={r.stage} className="frow">
                <span className="st">{r.stage}</span>
                <span className="kept" style={{ width: pct(r.kept / total, 2) }} />
                <span className="num">{count(r.kept)}</span>
                {r.removed ? <span className="rm" style={{ "--c": KIND_COLOR[r.main], width: pct(r.removed / maxRemoved, 1) } as CSSProperties} /> : <span />}
                <span className={r.removed ? "rmtxt" : "rmtxt zero"}>
                  {r.removed
                    ? (Object.entries(r.byKind) as [Kind, number][]).map(([k, n]) => `−${count(n)} ${k}`).join(" · ")
                    : "−0"}
                </span>
              </div>
            ))}
          </div>
          <div className="fkey" aria-hidden="true">
            <span><i style={{ "--c": "var(--success)" } as CSSProperties} />kept</span>
            <span><i style={{ "--c": "var(--danger)" } as CSSProperties} />rejected</span>
            <span><i style={{ "--c": "var(--text-muted)" } as CSSProperties} />duplicate</span>
            <span><i style={{ "--c": "var(--warning)" } as CSSProperties} />filtered</span>
            <span>removed: own scale, max {count(maxRemoved)}</span>
          </div>
        </>
      )}
      <div className="table-wrap">
        <table className="t" aria-label="Rows removed per stage and reason">
          <thead>
            <tr>
              <th>Removed at</th>
              <th>Reason</th>
              <th className="r">Rows</th>
              <th className="r">Share</th>
            </tr>
          </thead>
          <tbody>
            {reasonRows.length ? (
              reasonRows.map((r) => (
                <tr key={`${r.stage}-${r.reason}`}>
                  <td>{r.stage}</td>
                  <td className="code">{r.reason}</td>
                  <td className="r">{count(r.n)}</td>
                  <td className="r">{pct(r.n / total, 2)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={4}>No row was removed.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Histogram({ dist, threshold }: { dist: Distribution; threshold: number | null }) {
  const edges = dist.edges;
  const lo = edges[0] ?? 0;
  const hi = edges[edges.length - 1] ?? 0.5;
  const x = (v: number) => ((v - lo) / (hi - lo)) * 200;
  const max = Math.max(1, ...dist.histogram);
  const ticks = [0, 0.1, 0.2, 0.3, 0.4, 0.5].filter((t) => t >= lo && t <= hi);
  const below = threshold !== null ? dist.histogram.reduce((a, n, i) => ((edges[i + 1] ?? 0) <= threshold + 1e-9 ? a + n : a), 0) : 0;
  return (
    <>
      <div
        className="hist"
        role="img"
        aria-label={`Histogram of CLIP scores of ${count(dist.count)} rows${threshold !== null ? `; ${count(below)} below the threshold ${String(threshold)}` : ""}; mean ${dist.mean?.toFixed(3) ?? "—"}`}
      >
        <svg viewBox="0 0 200 100" preserveAspectRatio="none">
          {threshold !== null ? <rect x="0" y="0" width={x(threshold)} height="100" fill="var(--danger-soft)" /> : null}
          <line x1="0" y1="50" x2="200" y2="50" stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {dist.histogram.map((n, i) => {
            const a = edges[i] ?? 0;
            const b = edges[i + 1] ?? a;
            const h = (n / max) * 100;
            const filtered = threshold !== null && b <= threshold + 1e-9;
            return n ? (
              <rect key={i} className="bin" x={x(a) + 0.5} y={100 - h} width={Math.max(0.5, x(b) - x(a) - 1)} height={h} fill={filtered ? "var(--danger)" : "var(--seq-5)"}>
                <title>{`${a.toFixed(3)}–${b.toFixed(3)}: ${count(n)} rows`}</title>
              </rect>
            ) : null;
          })}
          {threshold !== null ? (
            <line x1={x(threshold)} y1="0" x2={x(threshold)} y2="100" stroke="var(--warning)" strokeWidth="2" strokeDasharray="5 3" vectorEffect="non-scaling-stroke" />
          ) : null}
          {dist.mean !== null ? (
            <line x1={x(dist.mean)} y1="0" x2={x(dist.mean)} y2="100" stroke="var(--text-secondary)" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
          ) : null}
        </svg>
      </div>
      <div className="hx" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t} style={threshold !== null && Math.abs(t - threshold) < 1e-9 ? { color: "var(--warning)" } : undefined}>
            {t.toFixed(2)}
            {threshold !== null && Math.abs(t - threshold) < 1e-9 ? " threshold" : ""}
          </span>
        ))}
      </div>
      <div className="fkey" aria-hidden="true">
        <span><i style={{ "--c": "var(--seq-5)" } as CSSProperties} />kept</span>
        {threshold !== null ? <span><i style={{ "--c": "var(--danger)" } as CSSProperties} />filtered (below {threshold.toFixed(2)})</span> : null}
        <span>
          dotted line: mean {dist.mean?.toFixed(3) ?? "—"} · median {dist.quantiles.p50?.toFixed(3) ?? "—"}
        </span>
      </div>
    </>
  );
}

function ClipPanel({ body, threshold, reviewId }: { body: Body; threshold: number | null; reviewId: string | null }) {
  const [table, setTable] = useState(false);
  const dist = body.clip_scores?.labelled ?? null;
  const filtered = body.rejections.by_stage.filter?.clip_below_threshold ?? 0;
  const review = body.review;
  return (
    <section className="panel" aria-label="CLIP score distribution">
      <header>
        <h2>CLIP score distribution</h2>
        <span className="sub">{dist ? `${count(dist.count)} captioned rows` : "no captions"}</span>
        <span className="spacer" />
        {dist ? <Toggle table={table} onChange={setTable} label="CLIP score distribution" /> : null}
      </header>
      {!dist ? (
        <p className="ptext">This version has no CLIP scores: it was not labelled.</p>
      ) : table ? (
        <div className="table-wrap">
          <table className="t" aria-label="CLIP score bins">
            <thead>
              <tr>
                <th>From</th>
                <th>To</th>
                <th className="r">Rows</th>
                <th className="r">Share</th>
                <th>Filtered</th>
              </tr>
            </thead>
            <tbody>
              {dist.histogram.map((n, i) =>
                n ? (
                  <tr key={i}>
                    <td className="code">{dist.edges[i]?.toFixed(3)}</td>
                    <td className="code">{dist.edges[i + 1]?.toFixed(3)}</td>
                    <td className="r">{count(n)}</td>
                    <td className="r">{pct(n / Math.max(1, dist.count), 2)}</td>
                    <td>{threshold !== null && (dist.edges[i + 1] ?? 0) <= threshold + 1e-9 ? "yes" : "no"}</td>
                  </tr>
                ) : null,
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <Histogram dist={dist} threshold={threshold} />
      )}
      {dist && threshold !== null ? (
        <p className="ptext">
          <b>
            {count(filtered)} rows ({body.filter_rate === null ? "—" : pct(body.filter_rate, 2)})
          </b>{" "}
          fell below the threshold of {threshold.toFixed(2)} and were filtered (LABEL-1.4). The red band is what the filter removed.
        </p>
      ) : null}
      {review?.acceptance ? (
        <div className="callout">
          <Icon name="eye" />
          <span>
            The review drew {review.sample_size} rows from the {count(body.rows.accepted)} kept: <b>{review.acceptance.overall.accepted} accepted</b>,{" "}
            {review.acceptance.overall.decided - review.acceptance.overall.accepted} rejected.
          </span>
          <span className="spacer" />
          {reviewId ? (
            <Link className="btn sm ghost" to={`/reviews/${reviewId}`}>
              Open {reviewId}
              <Icon name="chevron" />
            </Link>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** SCR-06: the phase-1 eval report, or with ``?kind=retrieval`` an index's retrieval report. */
export function ReportScreen() {
  const [params] = useSearchParams();
  return params.get("kind") === "retrieval" ? <RetrievalReport /> : <EvalReport />;
}

function EvalReport() {
  const { versionId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const report = useReport(versionId, params.get("report"));
  const version = useVersion(versionId);
  const reviews = useReviews(versionId);
  const threshold = useThreshold(versionId);
  const [dismissed, setDismissed] = useState(false);
  const buildingId = params.get("job");

  useCrumbs([
    { label: "Datasets", to: "/datasets" },
    ...(version.data ? [{ label: version.data.dataset, to: `/datasets/${version.data.dataset}` }] : []),
    { label: versionId, to: `/versions/${versionId}`, id: true },
    { label: "report" },
  ]);

  const building = useQuery({
    queryKey: ["jobs", buildingId ?? ""],
    queryFn: () => call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: buildingId ?? "" } } })),
    enabled: buildingId !== null,
  });
  // The build is followed on the global stream; its completion brings the new report.
  const onJob = useCallback(
    (job: Job) => {
      if (job.id !== buildingId) return;
      client.setQueryData(["jobs", job.id], job);
      if (job.state === "completed") {
        void client.invalidateQueries({ queryKey: ["versions", versionId, "report"] });
        setParams((p) => {
          const next = new URLSearchParams(p);
          next.delete("job");
          next.delete("report");
          return next;
        }, { replace: true });
      }
    },
    [buildingId, client, versionId, setParams],
  );
  useFeedMessage("job", onJob);

  const build = useMutation({
    mutationFn: (reviewId: string | null) =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/reports", {
          params: { path: { version_id: versionId } },
          body: { kind: "phase1_dataset", ...(reviewId ? { review_id: reviewId } : {}) },
        }),
      ),
    onSuccess: (job) => {
      setParams((p) => {
        const next = new URLSearchParams(p);
        next.set("job", job.id);
        return next;
      }, { replace: true });
    },
  });

  const closed = (reviews.data?.items ?? []).filter((r) => r.status === "closed");
  const newestClosed = closed[0];
  const job = building.data;

  if (job && job.state !== "completed") {
    const stage = job.stages[0];
    const failed = job.state === "failed" || job.state === "cancelled";
    return (
      <div className="scr06">
        {failed ? (
          <div className="state err" role="alert">
            <Icon name="x" />
            <b>Building the report failed</b>
            <Badge tone="err">{job.error?.code ?? job.state}</Badge>
            <p>
              {job.error?.reason ?? errorDetail(job.error?.detail) ?? "The evaluate stage did not finish."}{" "}
              {job.error?.suggested_action ? (
                <>
                  <b style={{ color: "var(--text-primary)" }}>Do</b> {job.error.suggested_action}
                </>
              ) : null}
            </p>
            <div style={{ display: "flex", gap: "var(--sp-2)" }}>
              <Button size="sm" onClick={() => { build.mutate(report.data?.review_session_id ?? null); }}>
                <Icon name="refresh" />
                Retry
              </Button>
              <Link className="btn sm ghost" to={`/jobs/${job.id}`}>
                Open {job.id}
                <Icon name="chevron" />
              </Link>
            </div>
          </div>
        ) : (
          <div className="build">
            <div className="rhead">
              <h1>Eval report</h1>
              <span role="status">
                <StatePill state="running" label="Building" />
              </span>
            </div>
            <div className="panel">
              <div style={{ display: "flex", gap: "var(--sp-2)", alignItems: "center" }}>
                <b className="mono">{job.id}</b>
                <span className="ptext">{stage?.activity ?? (job.state === "queued" ? "Waiting for a cpu worker…" : "Building…")}</span>
              </div>
              <Progress value={stagePercent(stage?.step, stage?.total)} state="running" />
              <Link className="btn sm ghost" style={{ alignSelf: "flex-start" }} to={`/jobs/${job.id}`}>
                Watch the job
                <Icon name="chevron" />
              </Link>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (report.isPending) {
    return (
      <div className="scr06" aria-busy="true">
        <Skeleton width={240} height={22} />
        <span className="skeleton" style={{ height: 84, display: "block" }} />
        <span className="skeleton" style={{ height: 84, display: "block" }} />
      </div>
    );
  }
  if (report.isError) {
    return (
      <div className="scr06">
        <ErrorState error={report.error} title="The report could not be loaded" onRetry={() => void report.refetch()} />
        <Button size="sm" style={{ alignSelf: "flex-start" }} onClick={() => void navigate("/datasets")}>
          <Icon name="db" />
          Go to datasets
        </Button>
      </div>
    );
  }

  const r = report.data;
  if (!r) {
    return (
      <div className="scr06">
        <div className="state" style={{ minHeight: 258 }}>
          <Icon name="report" />
          <b>No report for {versionId} yet</b>
          <p>
            The report summarises what each stage removed, the CLIP scores, the filter rate and the review&apos;s acceptance rate.{" "}
            {newestClosed ? (
              <>
                It will use the closed review <span className="mono">{newestClosed.id}</span> ({newestClosed.sample_size} items).
              </>
            ) : (
              "No review of this version is closed, so it will be built without the acceptance part."
            )}
          </p>
          <Button variant="primary" style={{ marginTop: "var(--sp-2)" }} disabled={build.isPending} onClick={() => { build.mutate(null); }}>
            <Icon name="report" />
            {build.isPending ? "Starting…" : "Build report"}
          </Button>
          {build.error instanceof ApiError ? <Badge tone="err">{build.error.problem.code}</Badge> : null}
        </div>
      </div>
    );
  }

  const body = r.report as Body;  // the phase-1 report; a retrieval report renders apart
  const used = r.review_session_id;
  // Reviews come newest first: a newest closed review that is not the one used is newer.
  const newer = newestClosed && newestClosed.id !== used ? newestClosed : null;

  return (
    <div className="scr06">
      <div className="rhead">
        <h1>
          Eval report <span className="mono">{r.version_id}</span>
        </h1>
        <Badge>{r.kind}</Badge>
        <span className="sub">
          #{r.id} · built {clock(r.created_at)}
          {r.job_id ? (
            <>
              {" by "}
              <Link className="mono" to={`/jobs/${r.job_id}`}>
                {r.job_id}
              </Link>
              <Icon name="check" />
            </>
          ) : null}
          {used ? (
            <>
              {" · review "}
              <Link className="mono" to={`/reviews/${used}`}>
                {used}
              </Link>
            </>
          ) : (
            " · no review"
          )}
        </span>
        <span className="spacer" />
        <Button onClick={() => void navigate(`/versions/${r.version_id}`)}>
          <Icon name="db" />
          Open version
        </Button>
        <Button disabled={build.isPending} onClick={() => { build.mutate(null); }}>
          <Icon name="refresh" />
          Rebuild
        </Button>
      </div>

      {newer && !dismissed ? (
        <div className="callout warn" role="status">
          <Icon name="info" />
          <span>
            <b>A newer review exists.</b>{" "}
            {used ? (
              <>
                This report used <span className="mono">{used}</span>;{" "}
              </>
            ) : (
              "This report was built without a review; "
            )}
            <span className="mono">{newer.id}</span> ({newer.sample_size} items) closed at {clock(newer.closed_at)}.
          </span>
          <span className="spacer" />
          <Button size="sm" variant="primary" onClick={() => { build.mutate(newer.id); }}>
            <Icon name="refresh" />
            Rebuild with {newer.id}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { setDismissed(true); }}>
            Keep this report
          </Button>
        </div>
      ) : null}

      <Headlines body={body} threshold={threshold} versionId={r.version_id} />
      <div className="grid2">
        <Funnel body={body} />
        <ClipPanel body={body} threshold={threshold} reviewId={used} />
      </div>
    </div>
  );
}
