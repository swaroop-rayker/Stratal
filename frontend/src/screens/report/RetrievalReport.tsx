// SCR-06, the retrieval report (mockup SCR-06retrieval_v1; SCREENS.md v5; LLM-2.2, LLM-2.3;
// ADR-0022 rules 8–11): hit@k on the frozen question set, the other index versions of the
// corpus, top-1 relevance against the abstention threshold, and the missed questions.
// Setting the threshold is the owner's choice (M12 decision 6): this page only offers it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Job, type Schemas } from "../../api/client";
import { useVersion } from "../../api/queries";
import { type SettingsSection, useSaveSettings, useSettings } from "../../api/registry";
import { useFeedMessage } from "../../api/stream";
import { Modal } from "../../components/Modal";
import { ErrorState } from "../../components/states";
import { Badge, Button, Icon, Progress, Skeleton, StatePill } from "../../components/ui";
import { clock, count, errorDetail, stagePercent } from "../../lib/format";
import { useCrumbs } from "../../shell/hooks";

type Report = Schemas["Report"];

interface Hit {
  hits: number;
  share: number;
  questions: number;
}
interface Retrieved {
  row_key: string;
  section: string;
  document: string;
  relevance: number;
}
interface Missed {
  id: string;
  question: string;
  labels: { document: string; section: string }[];
  retrieved: Retrieved[];
}
/** The retrieval report's body (stratal.eval.retrieval.score). */
export interface RetrievalBody {
  ks: number[];
  hits: Record<string, Hit>;
  index: { version_id: string; model: string; revision: string; chunks: number; chunk_tokens: number | null; overlap_tokens: number | null };
  missed: Missed[];
  relevance: { bin_edges: number[]; answerable: number[]; unanswerable: number[]; answerable_bins: number[]; unanswerable_bins: number[] };
  thresholds: { threshold: number; answered: number; abstained: number }[];
  question_set: { name: string; sha256: string; answerable: number; unanswerable: number };
}
interface Summary {
  report_id: number;
  version_id: string;
  question_set_sha256: string;
  chunk_tokens: number | null;
  overlap_tokens: number | null;
  chunks: number;
  hits: Record<string, Hit>;
  created_at: string;
}
interface RetrievalValues {
  embedding_model: string;
  abstention_threshold: number | null;
  threshold_source: string | null;
  default_k: number;
}

const pct = (x: number) => `${Math.round(x * 100)}%`;
const MISSED_SHOWN = 6;

/** Answerable questions still answered, and unanswerable ones abstaining, at a threshold
 * (null: no threshold, nothing abstains). Same rule as the report's own table. */
export function outcome(body: RetrievalBody, threshold: number | null): { answered: number; abstained: number } {
  if (threshold === null) return { answered: body.relevance.answerable.length, abstained: 0 };
  return {
    answered: body.relevance.answerable.filter((r) => r >= threshold).length,
    abstained: body.relevance.unanswerable.filter((r) => r < threshold).length,
  };
}

function shortDoc(document: string): string {
  return document.replace(/^decisions\//, "").replace(/-[a-z].*\.md$/, "").replace(/\.md$/, ".md");
}

function where(document: string, section: string): string {
  const tail = section.split(" › ").slice(1).join(" › ") || section;
  return `${shortDoc(document)} › ${tail}`;
}

function useRetrievalReport(versionId: string, reportId: string | null) {
  return useQuery({
    queryKey: ["versions", versionId, "report", "retrieval", reportId ?? "latest"],
    queryFn: async (): Promise<Report | null> => {
      try {
        return reportId
          ? await call(() => api.GET("/api/v1/reports/{report_id}", { params: { path: { report_id: Number(reportId) } } }))
          : await call(() =>
              api.GET("/api/v1/versions/{version_id}/reports/latest", { params: { path: { version_id: versionId }, query: { kind: "retrieval" } } }),
            );
      } catch (error) {
        if (error instanceof ApiError && error.problem.status === 404 && /no .*report/i.test(error.problem.detail)) return null;
        throw error;
      }
    },
    retry: false,
  });
}

function useSummaries(slug: string | undefined) {
  return useQuery({
    queryKey: ["datasets", slug, "reports", "retrieval"],
    queryFn: () => call(() => api.GET("/api/v1/datasets/{slug}/reports", { params: { path: { slug: slug ?? "" }, query: { kind: "retrieval" } } })),
    enabled: slug !== undefined,
  });
}

// ---------------------------------------------------------------------------- region 3

function OtherVersions({ body, current }: { body: RetrievalBody; current: string }) {
  const navigate = useNavigate();
  const version = useVersion(current);
  const summaries = useSummaries(version.data?.dataset);
  const items = ((summaries.data?.items ?? []) as Summary[]).filter((s) => s.question_set_sha256 === body.question_set.sha256);
  const best = Math.max(0, ...items.map((s) => s.hits["hit@5"]?.share ?? 0));
  return (
    <section className="panel" aria-label="Other index versions">
      <header>
        <h2>Other index versions</h2>
        <span className="sub">same corpus, same question set</span>
      </header>
      {summaries.isPending ? (
        <Skeleton width={480} />
      ) : summaries.isError ? (
        <ErrorState error={summaries.error} title="The other reports could not be loaded" onRetry={() => void summaries.refetch()} />
      ) : (
        <div className="table-wrap">
          <table className="t dense" aria-label="Retrieval reports of this corpus">
            <thead>
              <tr>
                <th>Index version</th>
                <th className="r">Chunk tokens</th>
                <th className="r">Overlap</th>
                <th className="r">Chunks</th>
                {body.ks.map((k) => (
                  <th key={k} className="r">hit@{k}</th>
                ))}
                <th>Built</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => {
                const here = s.version_id === current;
                return (
                  <tr
                    key={s.report_id}
                    className={here ? "here" : undefined}
                    tabIndex={0}
                    aria-current={here ? "true" : undefined}
                    onClick={() => { if (!here) void navigate(`/versions/${s.version_id}/report?kind=retrieval`); }}
                    onKeyDown={(e) => { if (e.key === "Enter" && !here) void navigate(`/versions/${s.version_id}/report?kind=retrieval`); }}
                  >
                    <td className="key">
                      {s.version_id}
                      {here ? " · this report" : ""}
                    </td>
                    <td className="r">{s.chunk_tokens ?? "–"}</td>
                    <td className="r">{s.overlap_tokens ?? "–"}</td>
                    <td className="r">{count(s.chunks)}</td>
                    {body.ks.map((k) => {
                      const share = s.hits[`hit@${String(k)}`]?.share;
                      const isBest = k === 5 && share !== undefined && share === best && items.length > 1;
                      return (
                        <td key={k} className={isBest ? "r best" : "r"}>
                          {share === undefined ? "–" : pct(share)}
                        </td>
                      );
                    })}
                    <td>{clock(s.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------- region 4

function RelevanceChart({ body, threshold }: { body: RetrievalBody; threshold: number | null }) {
  const { bin_edges: edges, answerable_bins: a, unanswerable_bins: u } = body.relevance;
  const lo = edges[0] ?? 0;
  const hi = edges[edges.length - 1] ?? 1;
  const max = Math.max(1, ...a, ...u);
  const ticks = edges.filter((_, i) => i % Math.max(1, Math.round((edges.length - 1) / 5)) === 0);
  const height = (n: number) => `${String(Math.round((n / max) * 100))}%`;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", minWidth: 0 }}>
      <div className="rr-key" aria-hidden="true">
        <span><i className="ka" />answerable · {body.relevance.answerable.length}</span>
        <span><i className="ku" />unanswerable · {body.relevance.unanswerable.length}</span>
        {threshold !== null ? <span><i className="kt" />threshold</span> : null}
      </div>
      <div
        className="rr-hist"
        role="img"
        aria-label="Top-1 relevance of answerable and unanswerable questions; the table of the bins follows"
        style={{ gridTemplateColumns: `repeat(${String(a.length)}, 1fr)` }}
      >
        {a.map((n, i) => (
          <div key={i} className="rr-bin" title={`${(edges[i] ?? 0).toFixed(2)}–${(edges[i + 1] ?? 0).toFixed(2)}: ${String(n)} answerable, ${String(u[i] ?? 0)} unanswerable`}>
            <i className={n ? "a" : "a z"} style={{ height: height(n) }} />
            <i className={u[i] ? "u" : "u z"} style={{ height: height(u[i] ?? 0) }} />
          </div>
        ))}
        {threshold !== null && threshold >= lo && threshold <= hi ? (
          <div className="rr-thr" style={{ left: `${String(((threshold - lo) / (hi - lo)) * 100)}%` }}>
            <span>{threshold.toFixed(2)}</span>
          </div>
        ) : null}
      </div>
      <div className="rr-ax" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t}>{t.toFixed(2)}</span>
        ))}
      </div>
      <table className="sr-only">
        <caption>Top-1 relevance bins</caption>
        <thead>
          <tr><th>From</th><th>To</th><th>Answerable</th><th>Unanswerable</th></tr>
        </thead>
        <tbody>
          {a.map((n, i) => (
            <tr key={i}>
              <td>{(edges[i] ?? 0).toFixed(2)}</td>
              <td>{(edges[i + 1] ?? 0).toFixed(2)}</td>
              <td>{n}</td>
              <td>{u[i] ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="ptext">
        Cosine of each question&apos;s best chunk.{" "}
        {threshold !== null ? (
          <>
            Below <b>{threshold.toFixed(2)}</b>, RAG says &quot;I don&apos;t know&quot; without asking the model.
          </>
        ) : (
          <>With no threshold set, RAG never says &quot;I don&apos;t know&quot;.</>
        )}
      </p>
    </div>
  );
}

function ThresholdPanel({ body, versionId, settings }: { body: RetrievalBody; versionId: string; settings: SettingsSection | undefined }) {
  const values = settings?.values as RetrievalValues | undefined;
  const sameModel = values ? values.embedding_model === body.index.model : true;
  const current = sameModel ? (values?.abstention_threshold ?? null) : null;
  const [asked, setAsked] = useState<number | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const save = useSaveSettings();
  const now = outcome(body, current);
  const close = () => {
    setAsked(null);
    save.reset();
    window.setTimeout(() => opener.current?.focus(), 0);
  };
  const diff = (n: number, word: [string, string]) => (n === 0 ? "the same as now" : `${String(Math.abs(n))} ${n < 0 ? word[0] : word[1]} than now`);
  const next = asked === null ? null : outcome(body, asked);
  const source = values?.threshold_source;
  return (
    <section className="panel" aria-label="Top-1 relevance, and the abstention threshold">
      <header>
        <h2>Top-1 relevance, and the abstention threshold</h2>
        <span className="sub">
          {current !== null ? (
            <>
              current threshold {current.toFixed(2)}
              {source ? (source === versionId ? ", set from this report" : <>, set from <Link className="mono" to={`/versions/${source}/report?kind=retrieval`}>{source}</Link>&apos;s report</>) : ""}
            </>
          ) : (
            "no threshold set"
          )}
        </span>
      </header>
      {current === null && settings ? (
        <div className="callout info" role="status">
          <Icon name="info" />
          <span>
            No abstention threshold is set: RAG never says &quot;I don&apos;t know&quot;. Pick one from the table.
          </span>
        </div>
      ) : null}
      <div className="rr-two">
        <RelevanceChart body={body} threshold={current} />
        <div className="table-wrap rr-thrtable">
          <table className="t dense" aria-label="Thresholds">
            <thead>
              <tr>
                <th className="r">Threshold</th>
                <th className="r">Still answered</th>
                <th className="r">Abstained</th>
                <th><span className="sr-only">Action</span></th>
              </tr>
            </thead>
            <tbody>
              {body.thresholds.map((t) => {
                const here = current !== null && Math.abs(t.threshold - current) < 1e-9;
                return (
                  <tr key={t.threshold} className={here ? "here" : undefined}>
                    <td className="r">{t.threshold.toFixed(2)}</td>
                    <td className="r">
                      {t.answered} of {body.question_set.answerable}
                    </td>
                    <td className="r">
                      {t.abstained} of {body.question_set.unanswerable}
                    </td>
                    <td>
                      {here ? (
                        <span className="rr-cur">current</span>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!settings}
                          aria-label={`Use ${t.threshold.toFixed(2)}`}
                          onClick={(e) => {
                            opener.current = e.currentTarget;
                            setAsked(t.threshold);
                          }}
                        >
                          Use
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <Modal
        open={asked !== null}
        onOpenChange={(open) => { if (!open) close(); }}
        title={`Set the abstention threshold to ${asked?.toFixed(2) ?? ""}?`}
        width={460}
        icon={<Icon name="sliders" style={{ color: "var(--accent)" }} />}
        footer={
          <>
            <Button onClick={close}>Cancel</Button>
            <Button
              variant="primary"
              disabled={save.isPending || !settings || asked === null}
              onClick={() => {
                if (!settings || !values || asked === null) return;
                save.mutate(
                  {
                    section: "retrieval",
                    values: { ...values, abstention_threshold: asked, threshold_source: versionId },
                    version: settings.version,
                  },
                  { onSuccess: close },
                );
              }}
            >
              {save.isPending ? "Saving…" : `Set to ${asked?.toFixed(2) ?? ""}`}
            </Button>
          </>
        }
      >
        {asked !== null && next ? (
          <p style={{ margin: 0 }}>
            Below <b>{asked.toFixed(2)}</b>, RAG answers &quot;I don&apos;t know&quot; without asking the model. On this report&apos;s questions,{" "}
            <b>
              {next.answered} of {body.question_set.answerable}
            </b>{" "}
            answerable questions would still be answered ({diff(next.answered - now.answered, ["fewer", "more"])}) and{" "}
            <b>
              {next.abstained} of {body.question_set.unanswerable}
            </b>{" "}
            unanswerable ones would abstain ({diff(next.abstained - now.abstained, ["fewer", "more"])}). It applies to every index built with{" "}
            <span className="mono">{body.index.model}</span>; change it on <Link to="/settings#retrieval">Settings</Link> at any time.
          </p>
        ) : null}
        {save.error ? (
          <p role="alert" style={{ color: "var(--danger)", marginBottom: 0 }}>
            <Badge tone="err">{save.error instanceof ApiError ? save.error.problem.code : "error"}</Badge>{" "}
            {save.error instanceof ApiError ? save.error.problem.detail : String(save.error)}
          </p>
        ) : null}
      </Modal>
    </section>
  );
}

// ---------------------------------------------------------------------------- region 5

function MissedPanel({ body, versionId }: { body: RetrievalBody; versionId: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? body.missed : body.missed.slice(0, MISSED_SHOWN);
  const k = body.ks.includes(5) ? 5 : (body.ks[body.ks.length - 1] ?? 5);
  return (
    <section className="panel" aria-label="Missed questions">
      <header>
        <h2>Missed questions</h2>
        <span className="sub">
          {body.missed.length} answerable {body.missed.length === 1 ? "question" : "questions"} with no hit in the top {k}
        </span>
      </header>
      {body.missed.length === 0 ? (
        <p className="ptext">Every answerable question has its labelled section in the top {k}.</p>
      ) : (
        <div className="table-wrap">
          <table className="t rr-miss" aria-label="Missed questions">
            <thead>
              <tr>
                <th>Question</th>
                <th>Labelled section</th>
                <th>Retrieved instead (top 3)</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((m) => (
                <tr key={m.id}>
                  <td>{m.question}</td>
                  <td className="key">
                    {m.labels.map((l) => (
                      <span key={`${l.document}-${l.section}`} style={{ display: "block" }}>
                        {shortDoc(l.document)} › {l.section || "(whole document)"}
                      </span>
                    ))}
                  </td>
                  <td>
                    <span className="got">
                      {m.retrieved.slice(0, 3).map((r) => (
                        <Link key={r.row_key} to={`/versions/${versionId}?row=${encodeURIComponent(r.row_key)}`}>
                          <b>{r.relevance.toFixed(2)}</b> {where(r.document, r.section)}
                        </Link>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
              {body.missed.length > MISSED_SHOWN ? (
                <tr>
                  <td colSpan={3}>
                    <Button size="sm" variant="ghost" onClick={() => { setAll((a) => !a); }}>
                      {all ? "Show fewer" : `${String(body.missed.length - MISSED_SHOWN)} more`}
                    </Button>
                    <span className="muted"> · each retrieved chunk opens on SCR-03</span>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------- the page

export function RetrievalReport() {
  const { versionId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const version = useVersion(versionId);
  const report = useRetrievalReport(versionId, params.get("report"));
  const settings = useSettings();
  const section = settings.data?.sections.find((s) => s.name === "retrieval");
  const buildingId = params.get("job");

  useCrumbs([
    { label: "Datasets", to: "/datasets" },
    ...(version.data ? [{ label: version.data.dataset, to: `/datasets/${version.data.dataset}` }] : []),
    { label: versionId, to: `/versions/${versionId}`, id: true },
    { label: "Retrieval report" },
  ]);

  const building = useQuery({
    queryKey: ["jobs", buildingId ?? ""],
    queryFn: () => call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: buildingId ?? "" } } })),
    enabled: buildingId !== null,
  });
  const onJob = useCallback(
    (job: Job) => {
      if (job.id !== buildingId) return;
      client.setQueryData(["jobs", job.id], job);
      if (job.state === "completed") {
        void client.invalidateQueries({ queryKey: ["versions", versionId, "report"] });
        void client.invalidateQueries({ queryKey: ["datasets"] });
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
    mutationFn: () =>
      call(() => api.POST("/api/v1/versions/{version_id}/reports", { params: { path: { version_id: versionId } }, body: { kind: "retrieval" } })),
    onSuccess: (job) => {
      setParams((p) => {
        const next = new URLSearchParams(p);
        next.set("job", job.id);
        return next;
      }, { replace: true });
    },
  });

  const body = useMemo(() => (report.data ? (report.data.report as unknown as RetrievalBody) : null), [report.data]);
  const v = version.data;
  const job = building.data;

  // Frame C: retrieval reports are built on an index version.
  if (v && v.stage !== "index") {
    return (
      <div className="scr06">
        <div className="callout info" role="status">
          <Icon name="info" />
          <span>
            Retrieval reports are built on an index version. <span className="mono">{v.version_id}</span> is a {v.stage} version: open its{" "}
            <Link to={`/versions/${v.version_id}`}>lineage</Link> to find its index.
          </span>
        </div>
      </div>
    );
  }

  if (job && job.state !== "completed") {
    const stage = job.stages[0];
    const failed = job.state === "failed" || job.state === "cancelled";
    return (
      <div className="scr06">
        {failed ? (
          <div className="state err" role="alert">
            <Icon name="x" />
            <b>Building the retrieval report failed</b>
            <Badge tone="err">{job.error?.code ?? job.state}</Badge>
            <p>{job.error?.reason ?? errorDetail(job.error?.detail) ?? "The retrieval_eval stage did not finish."}</p>
            <div style={{ display: "flex", gap: "var(--sp-2)" }}>
              <Button size="sm" onClick={() => { build.mutate(); }}>
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
              <h1>Retrieval report</h1>
              <span role="status">
                <StatePill state="running" label="Building" />
              </span>
            </div>
            <div className="panel">
              <div style={{ display: "flex", gap: "var(--sp-2)", alignItems: "center" }}>
                <b className="mono">{job.id}</b>
                <span className="ptext">
                  {stage?.step !== null && stage?.step !== undefined && stage.total ? `${String(stage.step)} of ${String(stage.total)} questions` : (stage?.activity ?? "Waiting for a cpu worker…")}
                </span>
              </div>
              <Progress value={stagePercent(stage?.step, stage?.total)} state="running" />
              <Link className="btn sm ghost" style={{ alignSelf: "flex-start" }} to={`/jobs/${job.id}`}>
                Open the job
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
        <span className="skeleton" style={{ height: 160, display: "block" }} />
      </div>
    );
  }
  if (report.isError) {
    return (
      <div className="scr06">
        <ErrorState error={report.error} title="The report could not be loaded" onRetry={() => void report.refetch()} />
      </div>
    );
  }
  const r = report.data;
  if (!r || !body) {
    return (
      <div className="scr06">
        <div className="state" style={{ minHeight: 258 }}>
          <Icon name="report" />
          <b>No retrieval report for this index yet</b>
          <p>It scores retrieval on the frozen question set: no model is asked, so it takes seconds and needs no GPU.</p>
          <Button variant="primary" style={{ marginTop: "var(--sp-2)" }} disabled={build.isPending} onClick={() => { build.mutate(); }}>
            <Icon name="report" />
            {build.isPending ? "Starting…" : "Build report"}
          </Button>
          {build.error instanceof ApiError ? <Badge tone="err">{build.error.problem.code}</Badge> : null}
        </div>
      </div>
    );
  }

  const answerable = body.question_set.answerable;
  return (
    <div className="scr06">
      <div className="rhead">
        <h1>
          Retrieval report <span className="mono">{r.version_id}</span>
        </h1>
        <Badge>{r.kind}</Badge>
        <span className="sub">
          index{body.index.chunk_tokens ? ` · ${String(body.index.chunk_tokens)} tokens` : ""} · built {clock(r.created_at)}
          {r.job_id ? (
            <>
              {" by "}
              <Link className="mono" to={`/jobs/${r.job_id}`}>
                {r.job_id}
              </Link>
              <Icon name="check" />
            </>
          ) : null}
        </span>
        <span className="spacer" />
        <Button onClick={() => void navigate(`/versions/${r.version_id}`)}>
          <Icon name="db" />
          Open the index
        </Button>
        <Button disabled={build.isPending} onClick={() => { build.mutate(); }}>
          <Icon name="refresh" />
          Rebuild
        </Button>
      </div>
      <div className="rr-meta">
        <span>
          Question set <span className="mono">{body.question_set.name}</span>
        </span>
        <span>
          sha256 <span className="mono">{body.question_set.sha256.slice(0, 4)}…{body.question_set.sha256.slice(-4)}</span>
        </span>
        <span>
          {answerable} answerable · {body.question_set.unanswerable} unanswerable
        </span>
        <span>k = {body.ks.join(", ")}</span>
        <span>no model asked</span>
      </div>
      <div className="heads">
        {body.ks.map((k) => {
          const h = body.hits[`hit@${String(k)}`];
          if (!h) return null;
          return (
            <div key={k} className="head">
              <span className="k">hit@{k}</span>
              <span className="v">{pct(h.share)}</span>
              <span className="d">
                {h.hits} of {h.questions}
                {k === 1 ? " answerable questions" : ""}
                {k === 5 && body.missed.length ? ` · ${String(body.missed.length)} missed, listed below` : ""}
              </span>
            </div>
          );
        })}
      </div>
      <OtherVersions body={body} current={r.version_id} />
      {settings.isError ? <ErrorState error={settings.error} title="The settings could not be loaded" onRetry={() => void settings.refetch()} /> : null}
      <ThresholdPanel body={body} versionId={r.version_id} settings={section} />
      <MissedPanel body={body} versionId={r.version_id} />
    </div>
  );
}
