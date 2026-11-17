// SCR-05 labeling review (SCREENS.md §3; mockup SCR-05_v1; LABEL-1.5, DATA-4.7): one sampled
// row at a time, accept or reject from the keyboard, decisions saved as made; then close and
// read the summary. Decisions change no data.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError, api, call, type Schemas } from "../../api/client";
import { useLineage, useVersion } from "../../api/queries";
import { Modal, ModalClose } from "../../components/Modal";
import { ErrorState } from "../../components/states";
import { Badge, Button, Icon, Kbd, Skeleton } from "../../components/ui";
import { bytes, clock, count } from "../../lib/format";
import { useCrumbs, useShortcuts } from "../../shell/hooks";

type Review = Schemas["Review"];
type Item = Schemas["ReviewItem"];
type Decision = "accept" | "reject";

function dims(w?: number | null, h?: number | null): string {
  return w && h ? `${String(w)} × ${String(h)}` : "–";
}

function useReview(id: string) {
  return useQuery({
    queryKey: ["reviews", id],
    queryFn: () => call(() => api.GET("/api/v1/reviews/{review_id}", { params: { path: { review_id: id } } })),
    retry: false,
  });
}

/** The filter threshold the version's rows were scored against, from its lineage. */
function useThreshold(versionId: string | undefined): number | null {
  const lineage = useLineage(versionId ?? "");
  const version = useVersion(versionId ?? "");
  const all = [...(version.data ? [version.data] : []), ...(lineage.data?.ancestors ?? [])];
  const filter = all.find((v) => v.stage === "filter");
  const value = filter?.config.clip_threshold;
  return typeof value === "number" ? value : null;
}

function ClipScale({ score, threshold }: { score: number | null | undefined; threshold: number | null }) {
  if (score === null || score === undefined) return <span className="mono">–</span>;
  const max = 0.5; // the report's score axis (EVAL-1.1)
  const pos = (v: number) => `${String(Math.max(0, Math.min(100, (v / max) * 100)))}%`;
  return (
    <span className="scale" aria-label={`CLIP score ${score.toFixed(3)}${threshold !== null ? `, threshold ${String(threshold)}` : ""}`}>
      <b className="mono">{score.toFixed(3)}</b>
      <span className="track" style={threshold !== null ? { background: `linear-gradient(90deg, var(--danger-soft) 0 ${pos(threshold)}, var(--surface-sunken) ${pos(threshold)})` } : undefined}>
        {threshold !== null ? <span className="thr" style={{ left: pos(threshold) }} /> : null}
        <span className="val" style={{ left: pos(score) }} />
      </span>
    </span>
  );
}

function Summary({ review, version }: { review: Review; version: Schemas["Version"] | undefined }) {
  const navigate = useNavigate();
  const build = useMutation({
    mutationFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/reports", {
          params: { path: { version_id: review.version_id } },
          body: { kind: "phase1_dataset", review_id: review.id },
        }),
      ),
    onSuccess: (job) => {
      void navigate(`/jobs/${(job as { id: string }).id}`);
    },
  });
  const p = review.progress;
  const rejected = review.items.filter((i) => i.decision === "reject");
  const noted = rejected.filter((i) => i.note).length;
  return (
    <>
      <div className="sbar">
        <h1>{review.id}</h1>
        <span className="pill closed">
          <Icon name="check" />
          Closed
        </span>
        <span className="sub">
          closed {clock(review.closed_at)} · {p.decided} of {p.sampled} decided
        </span>
        <span className="spacer" />
        <Button onClick={() => void navigate(`/versions/${review.version_id}`)}>
          <Icon name="db" />
          Open version
        </Button>
        <Button variant="primary" disabled={build.isPending} onClick={() => { build.mutate(); }}>
          <Icon name="report" />
          {build.isPending ? "Starting…" : "Build the eval report"}
        </Button>
      </div>
      <div className="sumgrid">
        <div className="sumcard" style={{ "--c": "var(--success)" } as CSSProperties}>
          <span className="k">Accepted</span>
          <div className="bignum">{p.accept}</div>
          <span className="d">
            of {p.decided} reviewed · {p.decided ? ((p.accept / p.decided) * 100).toFixed(1) : "—"}%
          </span>
        </div>
        <div className="sumcard" style={{ "--c": "var(--danger)" } as CSSProperties}>
          <span className="k">Rejected</span>
          <div className="bignum">{p.reject}</div>
          <span className="d">{noted} with a note</span>
        </div>
        <div className="sumcard" style={{ "--c": "var(--accent)" } as CSSProperties}>
          <span className="k">Next</span>
          <p style={{ margin: "var(--sp-1) 0 0", fontSize: "var(--fs-13)", color: "var(--text-secondary)" }}>
            The eval report turns this into an acceptance rate with its 95% interval (EVAL-1.1)
            {version ? <>, for <span className="mono">{version.version_id}</span></> : null}.
          </p>
        </div>
      </div>
      {build.error instanceof ApiError ? (
        <p role="alert" className="readonly">
          <Badge tone="err">{build.error.problem.code}</Badge> {build.error.problem.detail}
        </p>
      ) : null}
      <h3 className="sec">Rejected items</h3>
      {rejected.length ? (
        <div className="rejrow">
          {rejected.map((i) => (
            <div key={i.position} className="rejitem">
              {i.row?.derived_sha256 ? <img className="ph" src={`/api/v1/blobs/${i.row.derived_sha256}?w=336`} alt={`Item ${String(i.position)}, ${i.row_key}`} /> : <span className="ph" />}
              <span className="mono">
                #{i.position} · {i.row_key}
              </span>
              <span>&ldquo;{i.row?.caption ?? "–"}&rdquo;</span>
              {i.note ? <span className="note">{i.note}</span> : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="readonly">No item was rejected.</p>
      )}
    </>
  );
}

export function ReviewScreen() {
  const { reviewId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const review = useReview(reviewId);
  const versionId = review.data?.version_id;
  const version = useVersion(versionId ?? "");
  const threshold = useThreshold(versionId);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [unsaved, setUnsaved] = useState<Record<number, Decision>>({});
  const [failure, setFailure] = useState<{ position: number; error: ApiError } | null>(null);
  const [closing, setClosing] = useState(false);
  const [details, setDetails] = useState(false);
  const note = useRef<HTMLTextAreaElement>(null);

  useCrumbs([
    { label: "Datasets", to: "/datasets" },
    ...(version.data ? [{ label: version.data.dataset, to: `/datasets/${version.data.dataset}` }] : []),
    ...(versionId ? [{ label: versionId, to: `/versions/${versionId}` }] : []),
    { label: reviewId, id: true },
  ]);

  const items = useMemo(() => review.data?.items ?? [], [review.data]);
  const firstOpen = items.find((i) => i.decision === null)?.position ?? 1;
  const position = Number(params.get("item") ?? firstOpen) || 1;
  const item: Item | undefined = items.find((i) => i.position === position);
  const go = useCallback(
    (p: number) => {
      if (p < 1 || p > items.length) return;
      setParams((q) => {
        const next = new URLSearchParams(q);
        next.set("item", String(p));
        return next;
      }, { replace: true });
    },
    [items.length, setParams],
  );

  const save = useMutation({
    mutationFn: (v: { position: number; decision: Decision; note: string | null }) =>
      call(() =>
        api.PUT("/api/v1/reviews/{review_id}/items/{position}", {
          params: { path: { review_id: reviewId, position: v.position } },
          body: { decision: v.decision, note: v.note },
        }),
      ),
    onMutate: (v) => {
      setUnsaved((u) => ({ ...u, [v.position]: v.decision }));
      setFailure(null);
    },
    onSuccess: (saved, v) => {
      setUnsaved((u) => Object.fromEntries(Object.entries(u).filter(([k]) => Number(k) !== v.position)));
      client.setQueryData<Review>(["reviews", reviewId], (old) => {
        if (!old) return old;
        const next = old.items.map((i) => (i.position === saved.position ? { ...i, ...saved, row: i.row } : i));
        const accept = next.filter((i) => i.decision === "accept").length;
        const reject = next.filter((i) => i.decision === "reject").length;
        return { ...old, items: next, progress: { ...old.progress, accept, reject, decided: accept + reject, undecided: next.length - accept - reject } };
      });
    },
    onError: (error, v) => {
      if (error instanceof ApiError) setFailure({ position: v.position, error });
    },
  });

  const open = review.data?.status === "open";
  const decide = useCallback(
    (decision: Decision) => {
      if (!item || !open) return;
      const text = notes[item.position] ?? item.note ?? "";
      save.mutate({ position: item.position, decision, note: text.trim() || null });
      // Then the next item (frame A in light: "Saving… then item 25").
      if (item.position < items.length) go(item.position + 1);
    },
    [item, open, notes, save, items.length, go],
  );

  const bindings = useMemo(
    () => ({
      a: () => { decide("accept"); },
      r: () => { decide("reject"); },
      n: () => note.current?.focus(),
      j: () => { go(position + 1); },
      k: () => { go(position - 1); },
    }),
    [decide, go, position],
  );
  useShortcuts(bindings);

  const close = useMutation({
    mutationFn: () => call(() => api.POST("/api/v1/reviews/{review_id}/close", { params: { path: { review_id: reviewId } } })),
    onSuccess: () => {
      setClosing(false);
      void client.invalidateQueries({ queryKey: ["reviews", reviewId] });
    },
  });

  if (review.isPending) {
    return (
      <div className="scr05" aria-busy="true">
        <Skeleton width={220} height={22} />
        <div className="work">
          <span className="skeleton" style={{ height: "100%", display: "block" }} />
          <div className="panel">
            <Skeleton width={200} />
            <Skeleton width={160} />
            <span className="skeleton" style={{ height: 48, display: "block" }} />
          </div>
        </div>
      </div>
    );
  }
  if (review.isError) {
    const notFound = review.error instanceof ApiError && review.error.problem.status === 404;
    return (
      <div className="scr05">
        {notFound ? (
          <div className="state err" role="alert">
            <Icon name="alert" />
            <b>Review {reviewId} not found</b>
            <Badge tone="err">not_found</Badge>
            <p>
              No review &apos;{reviewId}&apos;. <b style={{ color: "var(--text-primary)" }}>Do</b> open the version and start or pick a
              review there.
            </p>
            <Button size="sm" onClick={() => void navigate("/datasets")}>
              <Icon name="db" />
              Go to datasets
            </Button>
          </div>
        ) : (
          <ErrorState error={review.error} title={`Review ${reviewId} could not be loaded`} onRetry={() => void review.refetch()} />
        )}
      </div>
    );
  }

  const r = review.data;
  if (r.status === "closed") {
    return (
      <div className="scr05">
        <Summary review={r} version={version.data} />
      </div>
    );
  }

  const p = r.progress;
  const accepted = version.data ? Number(version.data.counts.accepted ?? 0) : null;
  const decision = item ? (unsaved[item.position] ?? item.decision) : null;
  const saving = item ? item.position in unsaved && !failure : false;
  const row = item?.row;

  return (
    <div className="scr05">
      <div className="sbar">
        <h1>{r.id}</h1>
        <span className="pill open">
          <Icon name="eye" />
          Open
        </span>
        <span className="sub sub-long">
          of <span className="mono">{r.version_id}</span>
          {version.data ? ` (${version.data.stage})` : ""} · {r.sample_size} of {accepted === null ? "…" : count(accepted)} accepted rows · seed{" "}
          <span className="mono">{r.seed}</span> · started {clock(r.created_at)}
        </span>
        <span className="spacer" />
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            // The shell's shortcut help opens on "?".
            window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" }));
          }}
        >
          <Icon name="keys" />
          Keys
          <Kbd>?</Kbd>
        </Button>
        <Button
          disabled={close.isPending}
          onClick={() => {
            if (p.undecided > 0) setClosing(true);
            else close.mutate();
          }}
        >
          Close session
        </Button>
      </div>

      <div className="prog">
        <span className="progress" role="progressbar" aria-valuenow={p.decided} aria-valuemax={p.sampled} aria-label="Decided">
          <i className="acc" style={{ width: `${String((p.accept / p.sampled) * 100)}%` }} />
          <i className="rej" style={{ width: `${String((p.reject / p.sampled) * 100)}%` }} />
        </span>
        <span>
          <b>{p.decided}</b> / {p.sampled} decided · <span className="k-acc">{p.accept} accepted</span> · <span className="k-rej">{p.reject} rejected</span>
        </span>
      </div>

      {item ? (
        <div className="work">
          <div className="photo" role="img" aria-label={`Item ${String(item.position)} of ${String(p.sampled)}: ${item.row_key}`}>
            {row?.derived_sha256 ? <img src={`/api/v1/blobs/${row.derived_sha256}?w=1024`} alt="" /> : null}
            <span className="tag">
              <b style={{ color: "var(--text-primary)" }}>
                {item.position} / {p.sampled}
              </b>
              {item.row_key}
            </span>
            <span className="dims">copy {dims(row?.derived_width, row?.derived_height)}</span>
            <span className="arrows">
              <Button size="sm" aria-label="Previous item" disabled={position <= 1} onClick={() => { go(position - 1); }}>
                <Icon name="back" />
                <Kbd>k</Kbd>
              </Button>
              <Button size="sm" aria-label="Next item" disabled={position >= p.sampled} onClick={() => { go(position + 1); }}>
                <Kbd>j</Kbd>
                <Icon name="chevron" />
              </Button>
            </span>
          </div>
          <div className="panel">
            <div className="capcard" aria-live="polite">
              <span className="k">Caption</span>
              <p>{row?.caption ?? "–"}</p>
            </div>
            <div className={details ? "kv detail open" : "kv detail"}>
              <span>Model</span>
              <span className="mono">
                {row?.caption_model ?? "–"}
                {row?.caption_model_revision ? ` · ${row.caption_model_revision.slice(0, 7)}` : ""}
              </span>
              <span>CLIP score</span>
              <ClipScale score={row?.clip_score} threshold={threshold} />
              {threshold !== null ? (
                <>
                  <span />
                  <span className="scale-key">
                    <span>
                      <i style={{ "--c": "var(--warning)" } as CSSProperties} />
                      threshold {threshold.toFixed(2)}
                    </span>
                    <span>
                      <i style={{ "--c": "var(--danger-soft)" } as CSSProperties} />
                      would be filtered
                    </span>
                  </span>
                </>
              ) : null}
              <span>Row</span>
              <span className="mono">{item.row_key}</span>
              <span>Original</span>
              <span className="mono">
                {dims(row?.width, row?.height)} · {bytes(row?.size_bytes)}
              </span>
              {item.split ? (
                <>
                  <span>Split</span>
                  <span className="mono">
                    {item.split} · {row?.split_stratum ?? "–"}
                  </span>
                </>
              ) : null}
            </div>
            <button type="button" className="btn sm ghost details-toggle" style={{ alignSelf: "flex-start" }} onClick={() => { setDetails((d) => !d); }} aria-expanded={details}>
              Details
              <Icon name={details ? "down" : "chevron"} />
            </button>
            <div className="decide">
              <button type="button" className="btn dbtn acc" aria-pressed={decision === "accept"} onClick={() => { decide("accept"); }}>
                {saving && decision === "accept" ? <span className="spinner" style={{ color: "var(--success)" }} /> : <Icon name="check" />}
                {decision === "accept" ? "Accepted" : "Accept"}
                <Kbd>a</Kbd>
              </button>
              <button type="button" className="btn dbtn rej" aria-pressed={decision === "reject"} onClick={() => { decide("reject"); }}>
                {saving && decision === "reject" ? <span className="spinner" style={{ color: "var(--danger)" }} /> : <Icon name="x" />}
                {decision === "reject" ? "Rejected" : "Reject"}
                <Kbd>r</Kbd>
              </button>
            </div>
            <div className={details ? "field detail open" : "field detail"}>
              <label htmlFor="rv-note">
                <Icon name="note" style={{ width: 12, height: 12 }} />
                Note, optional
                <Kbd>n</Kbd>
              </label>
              <textarea
                id="rv-note"
                ref={note}
                className="input"
                placeholder="e.g. two dogs, but it is dusk, not sunset"
                value={notes[item.position] ?? item.note ?? ""}
                onChange={(e) => { setNotes((n) => ({ ...n, [item.position]: e.target.value })); }}
                onKeyDown={(e) => { if (e.key === "Escape") e.currentTarget.blur(); }}
                onBlur={(e) => {
                  // A changed note on a decided item is saved with its decision.
                  const text = e.target.value.trim() || null;
                  if (item.decision && text !== (item.note ?? null)) save.mutate({ position: item.position, decision: item.decision, note: text });
                }}
              />
            </div>
            <p className="hint">
              {saving ? `Saving… then item ${String(Math.min(item.position + 1, p.sampled))}.` : "Saved as you decide. Go back to an item to change its decision."}
            </p>
            {failure ? (
              <div className="toast" role="alert" style={{ "--c": "var(--danger)" } as CSSProperties}>
                <Icon name="x" />
                <div>
                  <b>Decision for item {failure.position} not saved</b>
                  <span>
                    <span className="mono">{failure.error.problem.code}</span> · {failure.error.problem.detail} Item {failure.position} stays open until it is saved.
                  </span>
                  <div style={{ marginTop: "var(--sp-2)" }}>
                    <Button
                      size="sm"
                      onClick={() => {
                        const d = unsaved[failure.position];
                        if (d) save.mutate({ position: failure.position, decision: d, note: (notes[failure.position] ?? "").trim() || null });
                      }}
                    >
                      <Icon name="refresh" />
                      Retry
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="strip" role="listbox" aria-label="Sampled items">
        {items.map((i) => {
          const d = unsaved[i.position] ?? i.decision;
          const isUnsaved = i.position in unsaved && failure?.position === i.position;
          const cls = ["cell", d === "accept" ? "a" : d === "reject" ? "r" : "", i.position === position ? "cur" : "", isUnsaved ? "unsaved" : ""].filter(Boolean).join(" ");
          return (
            <button
              key={i.position}
              type="button"
              role="option"
              aria-selected={i.position === position}
              aria-label={`Item ${String(i.position)}, ${d === "accept" ? "accepted" : d === "reject" ? "rejected" : "undecided"}${isUnsaved ? ", not saved" : ""}`}
              className={cls}
              tabIndex={i.position === position ? 0 : -1}
              onClick={() => { go(i.position); }}
            >
              {d === "accept" ? <Icon name="check" /> : d === "reject" ? <Icon name="x" /> : i.position}
            </button>
          );
        })}
      </div>

      <Modal
        open={closing}
        onOpenChange={setClosing}
        title={`Close ${r.id} with ${String(p.undecided)} undecided?`}
        icon={<Icon name="alert" style={{ color: "var(--warning)" }} />}
        footer={
          <>
            <ModalClose asChild>
              <Button>Keep reviewing</Button>
            </ModalClose>
            <Button variant="primary" disabled={close.isPending || p.decided === 0} onClick={() => { close.mutate(); }}>
              Close with {p.decided} decided
            </Button>
          </>
        }
      >
        Only the <b>{p.decided} decided</b> items count towards the acceptance rate. The other {p.undecided} are not reviewed and
        cannot be decided after closing. A smaller sample gives a wider interval in the report.
        {p.decided === 0 ? <p style={{ margin: "var(--sp-2) 0 0" }}>Decide at least one item before closing: an empty review measures nothing.</p> : null}
      </Modal>
      <p className="sr-only" aria-live="assertive">
        {failure ? `Decision for item ${String(failure.position)} not saved.` : ""}
      </p>
      <Link to={`/versions/${r.version_id}`} className="sr-only">
        Back to the version
      </Link>
    </div>
  );
}
