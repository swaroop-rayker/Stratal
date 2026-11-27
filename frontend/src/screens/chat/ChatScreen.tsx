// SCR-12 chat, the phase-3 slice (SCREENS.md v5; mockup SCR-12_v1, G2 approved 2026-11-27):
// manual mode with LLM, RAG and Images. Answers stream over SSE (ADR-0021 rule 8); a RAG answer
// shows its checked citations and the sources it was given (ADR-0022 rule 7); below the owner's
// threshold it is "I don't know" (rule 8); Images shows the nearest pictures (rule 12).
import { useQueryClient } from "@tanstack/react-query";
import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import {
  type AnswerDetails,
  type ChatEvent,
  type ChatMode,
  type ChatSources,
  type MessageOut,
  type RequestTimings,
  type SessionSummary,
  type Source,
  chatKeys,
  deleteSession,
  streamChat,
  useChatSession,
  useChatSessions,
  useChatSources,
} from "../../api/chat";
import { ApiError, type Problem } from "../../api/client";
import { Modal } from "../../components/Modal";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, type IconName, Kbd, Skeleton } from "../../components/ui";
import { useActiveJobs } from "../../shell/JobIndicator";
import { useCrumbs, useShortcuts } from "../../shell/hooks";

const MODES: { mode: ChatMode; label: string; icon: IconName }[] = [
  { mode: "llm", label: "LLM", icon: "chat" },
  { mode: "rag", label: "RAG", icon: "db" },
  { mode: "images", label: "Images", icon: "image" },
];
const PLACEHOLDER: Record<ChatMode, string> = {
  llm: "Message the model…",
  rag: "Ask about the documents…",
  images: "Describe the images to find…",
};
const MARKER = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
const LOADING_AFTER_MS = 1200;

type Timings = Partial<RequestTimings> & { display?: string; abstained?: boolean; labelled_version_id?: string };

/** One answer, live or stored. */
interface Answer {
  key: string;
  mode: ChatMode;
  question: string;
  text: string;
  timings: Timings;
  details: AnswerDetails;
  streaming: boolean;
  problem?: Problem;
  /** Live only: how many answers the conversation had stored when this one was sent. */
  before?: number;
}

const seconds = (ms: number | null | undefined) =>
  ms === null || ms === undefined ? "–" : `${(ms / 1000).toFixed(ms < 1000 ? 2 : 1)} s`;

function asMode(value: string | null | undefined): ChatMode {
  return value === "rag" || value === "images" ? value : "llm";
}

function storedAnswers(messages: MessageOut[]): Answer[] {
  const answers: Answer[] = [];
  let question = "";
  for (const m of messages) {
    if (m.role === "user") {
      question = m.content;
      continue;
    }
    const details = { ...(m.details ?? {}) } as AnswerDetails;
    if (m.citations) details.citations = m.citations as Source[];
    answers.push({
      key: `m${m.id}`,
      mode: asMode(m.mode),
      question,
      text: m.content,
      timings: { ...(m.request ?? {}), abstained: details.abstained },
      details,
      streaming: false,
      problem: details.error,
    });
  }
  return answers;
}

function shortModel(t: Timings): string {
  const name = (t.model ?? "").replace(/-q4km$/, "");
  return t.revision ? `${name} · ${t.revision.slice(0, 7)}` : name;
}

// ---------------------------------------------------------------------------- the screen

export function ChatScreen() {
  const { sessionId } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const sessions = useChatSessions();
  const [live, setLive] = useState<Answer | null>(null);
  // A stopped answer is stored when the API notices the closed stream, which can be after
  // the screen's first refetch: keep the live copy and poll until the stored one is there.
  const [awaitingStore, setAwaitingStore] = useState(false);
  const session = useChatSession(sessionId, awaitingStore);
  const sources = useChatSources();
  const active = useActiveJobs();
  const [mode, setMode] = useState<ChatMode>(search.get("index") ? "rag" : "llm");
  const [indexId, setIndexId] = useState<string | undefined>(search.get("index") ?? undefined);
  const [imagesId, setImagesId] = useState<string | undefined>(undefined);
  const [draft, setDraft] = useState("");
  const [loadingModel, setLoadingModel] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [deleting, setDeleting] = useState<SessionSummary | null>(null);
  const abort = useRef<AbortController | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useCrumbs([{ label: "Chat", to: "/chat" }, ...(session.data ? [{ label: session.data.title }] : [])]);

  const indexes = sources.data?.indexes ?? [];
  const images = sources.data?.images ?? [];
  const chosenIndex = indexId ?? indexes[0]?.version_id;
  const chosenImages = imagesId ?? images.find((i) => i.dataset.startsWith("coco"))?.version_id ?? images[0]?.version_id;

  const answers = useMemo(() => storedAnswers(session.data?.messages ?? []), [session.data]);

  // Opening a conversation picks up where it left off: its last mode and version.
  const [resumed, setResumed] = useState<string | undefined>(undefined);
  if (session.data && resumed !== session.data.id) {
    setResumed(session.data.id);
    const last = answers[answers.length - 1];
    if (last) {
      setMode(last.mode);
      if (last.mode === "rag" && last.timings.index_version_id) setIndexId(last.timings.index_version_id);
      if (last.mode === "images" && last.details.version_id) setImagesId(last.details.version_id);
    }
  }
  const gpuJob = (active.data?.items ?? []).find((job) =>
    job.stages.some((s) => s.queue === "gpu" && s.status === "running"),
  );

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [answers.length, live?.text]);

  const send = useCallback(
    async (text: string, as: ChatMode) => {
      const message = text.trim();
      if (!message || live?.streaming) return;
      const controller = new AbortController();
      abort.current = controller;
      const turn: Answer = { key: "live", mode: as, question: message, text: "", timings: {}, details: {}, streaming: true, before: answers.length };
      setLive(turn);
      setDraft("");
      const timer = window.setTimeout(() => { setLoadingModel(as !== "images"); }, LOADING_AFTER_MS);
      let target = sessionId;
      const update = (change: (a: Answer) => Answer) => { setLive((a) => (a ? change(a) : a)); };
      const onEvent = (event: ChatEvent) => {
        if (event.name !== "session") {
          window.clearTimeout(timer);
          setLoadingModel(false);
        }
        switch (event.name) {
          case "session":
            target = event.data.session_id;
            break;
          case "meta":
            update((a) => ({ ...a, timings: { ...a.timings, ...event.data } }));
            break;
          case "token":
            update((a) => ({ ...a, text: a.text + event.data.text }));
            break;
          case "citations":
          case "images":
            update((a) => ({ ...a, details: { ...a.details, ...event.data } }));
            break;
          case "done":
            update((a) => ({ ...a, timings: { ...a.timings, ...event.data } }));
            break;
          case "error":
            update((a) => ({ ...a, problem: event.data }));
            break;
        }
      };
      try {
        await streamChat(
          {
            mode: as,
            message,
            session_id: sessionId,
            index_version_id: as === "rag" ? chosenIndex : undefined,
            labelled_version_id: as === "images" ? chosenImages : undefined,
          },
          onEvent,
          controller.signal,
        );
        if (controller.signal.aborted) update((a) => ({ ...a, details: { ...a.details, stopped: true } }));
      } catch (error) {
        const problem = error instanceof ApiError ? error.problem : null;
        update((a) => ({ ...a, problem: problem ?? { status: 0, code: "internal_error", title: "Error", detail: String(error), suggested_action: null, request_id: null, errors: [] } }));
      } finally {
        window.clearTimeout(timer);
        setLoadingModel(false);
        update((a) => ({ ...a, streaming: false }));
        abort.current = null;
        await client.invalidateQueries({ queryKey: chatKeys.sessions });
        if (target && target !== sessionId) {
          void navigate(`/chat/${target}`, { replace: !sessionId });
        } else if (target) {
          await client.invalidateQueries({ queryKey: chatKeys.session(target) });
        }
      }
    },
    [answers.length, chosenImages, chosenIndex, client, live?.streaming, navigate, sessionId],
  );

  // Once the conversation has stored this answer, the live copy goes. A request refused
  // before it streamed (a status on its problem) is never stored, so it stays on screen.
  const stored = live !== null && !live.streaming && answers.length > (live.before ?? 0);
  const unstorable = live !== null && !live.streaming && Boolean(live.problem?.status);
  useEffect(() => {
    if (stored) setLive(null);
  }, [stored]);
  const waiting = live !== null && !live.streaming && !stored && !unstorable;
  useEffect(() => {
    setAwaitingStore(waiting);
    if (!waiting) return;
    // The API stores within a few seconds (a stop during a model load waits for the load);
    // after 30 s stop polling and leave the live copy on screen.
    const cap = window.setTimeout(() => { setAwaitingStore(false); }, 30_000);
    return () => { window.clearTimeout(cap); };
  }, [waiting]);

  const stop = useCallback(() => abort.current?.abort(), []);

  useShortcuts(
    useMemo(
      () => ({
        "/": () => box.current?.focus(),
      }),
      [],
    ),
  );
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && abort.current) {
        event.preventDefault();
        stop();
      }
      if (event.altKey && ["1", "2", "3"].includes(event.key)) {
        event.preventDefault();
        const picked = MODES[Number(event.key) - 1];
        if (picked) setMode(picked.mode);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  }, [stop]);

  const onBoxKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft, mode);
    }
  };

  const ragMissing = mode === "rag" && indexes.length === 0;
  const imagesMissing = mode === "images" && images.length === 0;
  const empty = !sessionId && !live;
  const streaming = live?.streaming ?? false;

  return (
    <div className="scr12">
      <aside className={railOpen ? "ch-sess open" : "ch-sess"} aria-label="Conversations">
        <Button size="sm" onClick={() => { setRailOpen(false); void navigate("/chat"); }} aria-label="New chat">
          <Icon name="plus" />
          <span className="lbl">New chat</span>
        </Button>
        <Button size="sm" variant="ghost" className="ch-rail-toggle" aria-label="Conversations" aria-expanded={railOpen} onClick={() => { setRailOpen((o) => !o); }}>
          <Icon name="list" />
        </Button>
        <SessionList
          sessions={sessions.data?.items ?? []}
          loading={sessions.isLoading}
          error={sessions.error}
          current={sessionId}
          onPick={() => { setRailOpen(false); }}
          onDelete={setDeleting}
        />
      </aside>
      <div className="ch-main">
        {empty ? (
          <div className="ch-empty">
            <EmptyState icon="chat" title="Ask the local model, ask your documents, or find images.">
              <b>LLM</b> answers from the model alone. <b>RAG</b> answers from an index of documents and cites them. <b>Images</b> finds
              pictures in a labelled version from a description.
            </EmptyState>
          </div>
        ) : (
          <div className="ch-conv" role="log" aria-live="polite" aria-relevant="additions">
            <div className="ch-col">
              {session.isLoading && sessionId ? (
                <>
                  <Skeleton width={320} height={30} />
                  <Skeleton width={760} height={72} />
                </>
              ) : session.error ? (
                <ErrorState error={session.error} title="This conversation could not be loaded" onRetry={() => void session.refetch()} />
              ) : null}
              {answers.map((a) => (
                <Turn key={a.key} answer={a} onRetry={(q, m) => void send(q, m)} />
              ))}
              {live ? <Turn answer={live} onRetry={(q, m) => void send(q, m)} /> : null}
              <div ref={bottom} />
            </div>
          </div>
        )}
        <div className="ch-comp">
          {gpuJob ? (
            <div className="ch-gpu" role="status">
              <Icon name="pause" />
              <span>
                The GPU is in use by <Link className="mono" to={`/jobs/${gpuJob.id}`}>{gpuJob.id}</Link> ({gpuJob.kind}). LLM and RAG answers
                wait for it; questions below the threshold and <i>Images</i> still work.
              </span>
            </div>
          ) : loadingModel ? (
            <div className="ch-gpu loading" role="status">
              <Icon name="clock" />
              <span>
                <b>Loading the LLM…</b> The first answer after a pause takes a few seconds more (cold start).
              </span>
            </div>
          ) : null}
          {ragMissing || imagesMissing ? (
            <div className="callout info">
              <Icon name="info" />
              <span>
                {ragMissing ? (
                  <>
                    <b>RAG</b> needs an index: ingest a folder as <i>Text corpus</i> and run it to <span className="mono">index</span> (
                    <Link className="ch-link" to="/datasets">Datasets</Link>).
                  </>
                ) : (
                  <>
                    <b>Images</b> needs a labelled version with CLIP vectors.
                  </>
                )}
              </span>
            </div>
          ) : null}
          <div className="row">
            <span className="group-btns ch-mode" role="group" aria-label="Mode">
              {MODES.map((m) => (
                <button key={m.mode} type="button" className="btn" aria-pressed={mode === m.mode} onClick={() => { setMode(m.mode); }}>
                  <Icon name={m.icon} />
                  {m.label}
                </button>
              ))}
            </span>
            <VersionPicker mode={mode} sources={sources.data} index={chosenIndex} images={chosenImages} onIndex={setIndexId} onImages={setImagesId} />
            <span className="hint">
              <Kbd>/</Kbd> to type · <Kbd>Alt</Kbd>
              <Kbd>1</Kbd>–<Kbd>3</Kbd> mode
            </span>
          </div>
          <div className="row">
            <textarea
              ref={box}
              className="ch-box"
              aria-label="Message"
              placeholder={PLACEHOLDER[mode]}
              value={draft}
              onChange={(e) => { setDraft(e.target.value); }}
              onKeyDown={onBoxKey}
              rows={2}
            />
            {streaming ? (
              <Button className="ch-send" onClick={stop}>
                <Icon name="stop" />
                Stop
              </Button>
            ) : (
              <Button
                variant="primary"
                className="ch-send"
                onClick={() => void send(draft, mode)}
                disabled={!draft.trim() || ragMissing || imagesMissing}
              >
                <Icon name="send" />
                Send
              </Button>
            )}
          </div>
        </div>
      </div>
      <DeleteSession session={deleting} onClose={() => { setDeleting(null); }} onDeleted={(id) => {
        setDeleting(null);
        void client.invalidateQueries({ queryKey: chatKeys.sessions });
        if (id === sessionId) void navigate("/chat");
      }} />
    </div>
  );
}

// ---------------------------------------------------------------------------- parts

function SessionList({ sessions, loading, error, current, onPick, onDelete }: {
  sessions: SessionSummary[];
  loading: boolean;
  error: unknown;
  current: string | undefined;
  onPick: () => void;
  onDelete: (s: SessionSummary) => void;
}) {
  if (loading) return <Skeleton width={180} height={12} />;
  if (error) return <ErrorState error={error} title="Conversations could not be loaded" />;
  const today = new Date().toDateString();
  const groups: [string, SessionSummary[]][] = [
    ["Today", sessions.filter((s) => new Date(s.updated_at).toDateString() === today)],
    ["Earlier", sessions.filter((s) => new Date(s.updated_at).toDateString() !== today)],
  ];
  return (
    <>
      {groups.map(([name, items]) =>
        items.length ? (
          <div key={name} style={{ display: "contents" }}>
            <div className="grp">{name}</div>
            {items.map((s) => (
              <div key={s.id} className="ch-it" aria-current={s.id === current ? "true" : undefined}>
                <Link to={`/chat/${s.id}`} onClick={onPick}>
                  <b>{s.title}</b>
                  <span>
                    {s.mode ? MODES.find((m) => m.mode === s.mode)?.label : ""} · {relative(s.updated_at)}
                  </span>
                </Link>
                <Button size="sm" variant="ghost" className="ch-del" aria-label={`Delete “${s.title}”`} onClick={() => { onDelete(s); }}>
                  <Icon name="x" />
                </Button>
              </div>
            ))}
          </div>
        ) : null,
      )}
    </>
  );
}

function relative(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function VersionPicker({ mode, sources, index, images, onIndex, onImages }: {
  mode: ChatMode;
  sources: ChatSources | undefined;
  index: string | undefined;
  images: string | undefined;
  onIndex: (id: string) => void;
  onImages: (id: string) => void;
}) {
  if (mode === "rag" && sources?.indexes.length) {
    return (
      <select aria-label="Index version" value={index} onChange={(e) => { onIndex(e.target.value); }}>
        {sources.indexes.map((i) => (
          <option key={i.version_id} value={i.version_id}>
            {i.dataset} · {i.version_id} · index · {i.chunks.toLocaleString()} chunks
            {i.chunk_tokens ? ` · ${i.chunk_tokens} tokens` : ""}
          </option>
        ))}
      </select>
    );
  }
  if (mode === "images" && sources?.images.length) {
    return (
      <select aria-label="Labelled version" value={images} onChange={(e) => { onImages(e.target.value); }}>
        {sources.images.map((i) => (
          <option key={i.version_id} value={i.version_id}>
            {i.dataset} · {i.version_id} · {i.stage} · {i.images.toLocaleString()} images
          </option>
        ))}
      </select>
    );
  }
  return null;
}

function Turn({ answer, onRetry }: { answer: Answer; onRetry: (question: string, mode: ChatMode) => void }) {
  return (
    <>
      <div className="ch-q">{answer.question}</div>
      {answer.problem ? (
        <Refusal problem={answer.problem} onRetry={() => { onRetry(answer.question, answer.mode); }} />
      ) : answer.details.abstained ? (
        <Abstention answer={answer} />
      ) : (
        <AnswerBubble answer={answer} />
      )}
    </>
  );
}

function Refusal({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  const job = /job_\d+/.exec(problem.detail)?.[0];
  return (
    <div className="ch-a err" role="alert">
      <div className="lead">
        <Icon name="alert" />
        <b className="code">{problem.code}</b>
        <span style={{ fontWeight: "var(--fw-regular)" }}>{problem.detail}</span>
      </div>
      {problem.suggested_action ? <p className="sub">{problem.suggested_action}</p> : null}
      <div className="row">
        <Button size="sm" onClick={onRetry}>
          <Icon name="refresh" />
          Retry
        </Button>
        {job ? (
          <Link className="btn sm ghost" to={`/jobs/${job}`}>
            Open {job}
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function Abstention({ answer }: { answer: Answer }) {
  const nearest = answer.details.nearest;
  const index = answer.timings.index_version_id;
  return (
    <div className="ch-a abst">
      <div className="lead">
        <Icon name="info" />
        I don&apos;t know.
      </div>
      <p className="sub">
        The documents don&apos;t seem to cover this.
        {nearest ? (
          <>
            {" "}
            Nearest source:{" "}
            <Link className="ch-link mono" to={rowLink(index, nearest.row_key)}>
              {nearest.document} › {lastSections(nearest.section)}
            </Link>
            , relevance <span className="mono">{nearest.relevance.toFixed(2)}</span>
            {answer.details.threshold !== null && answer.details.threshold !== undefined ? (
              <>
                , below the threshold <span className="mono">{answer.details.threshold.toFixed(2)}</span>
              </>
            ) : null}
            .
          </>
        ) : null}{" "}
        The model was not asked.
      </p>
      <Footer answer={answer} />
    </div>
  );
}

function rowLink(version: string | null | undefined, row: string): string {
  return version ? `/versions/${version}?row=${encodeURIComponent(row)}` : "#";
}

function lastSections(path: string): string {
  return path.split(" › ").slice(1).join(" › ") || path;
}

function AnswerBubble({ answer }: { answer: Answer }) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<number | null>(null);
  const given = answer.details.sources ?? answer.details.citations ?? [];
  const cited = given.filter((s) => s.cited ?? true);
  const uncited = given.filter((s) => s.cited === false);
  const index = answer.timings.index_version_id;
  const sourceId = (n: number) => `${answer.key}-src-${n}`;
  const show = (n: number) => {
    setFocus(n);
    if (uncited.some((s) => s.n === n)) setOpen(true);
    window.setTimeout(() => document.getElementById(sourceId(n))?.focus(), 0);
  };
  return (
    <div className="ch-a">
      {answer.mode === "images" && answer.details.images ? (
        <>
          <p>
            {answer.details.images.length} images in <span className="mono">{answer.details.version_id}</span> nearest to “{answer.question}”
          </p>
          <div className="ch-grid">
            {answer.details.images.map((img) => (
              <Link key={img.row_key} className="ch-img" to={rowLink(answer.details.version_id, img.row_key)}>
                <img src={`/api/v1/blobs/${img.derived_sha256}?w=224`} alt={`${img.row_key}, similarity ${img.similarity.toFixed(2)}`} loading="lazy" />
                <span>
                  <b>{img.similarity.toFixed(2)}</b> · {img.row_key.replace(/\.[a-z]+$/i, "")}
                </span>
              </Link>
            ))}
          </div>
        </>
      ) : (
        <p>
          <CitedText text={answer.text} onCite={show} />
          {answer.streaming ? <span className="caret" aria-hidden="true" /> : null}
        </p>
      )}
      {answer.details.stopped ? (
        <span className="ch-stopped">
          <Icon name="stop" />
          {answer.text ? "Stopped" : "Stopped before the first token"}
        </span>
      ) : null}
      {answer.mode === "rag" && !answer.streaming && given.length ? (
        <div className="ch-src">
          <h3>
            Sources
            <span>
              {cited.length} cited of {given.length} given
            </span>
          </h3>
          {cited.map((s) => (
            <SourceRow key={s.n} id={sourceId(s.n)} source={s} index={index} hot={focus === s.n} />
          ))}
          {uncited.length ? (
            open ? (
              uncited.map((s) => <SourceRow key={s.n} id={sourceId(s.n)} source={s} index={index} hot={focus === s.n} uncited />)
            ) : (
              <button type="button" className="ch-more" onClick={() => { setOpen(true); }}>
                <span className="no">{span(uncited)}</span>
                <Icon name="chevron" style={{ width: 12, height: 12 }} />
                {uncited.length} more {uncited.length === 1 ? "source" : "sources"} given, not cited
              </button>
            )
          ) : null}
        </div>
      ) : null}
      {answer.mode === "rag" && !answer.streaming && answer.details.uncited ? <p className="sub">No source cited.</p> : null}
      <Footer answer={answer} />
    </div>
  );
}

/** "4", "3–5" or "1, 2, 5": the numbers of the folded, uncited sources. */
function span(sources: Source[]): string {
  const numbers = sources.map((s) => s.n);
  const first = numbers[0];
  const last = numbers[numbers.length - 1];
  if (first === undefined || last === undefined) return "";
  if (first === last) return String(first);
  return last - first + 1 === numbers.length ? `${String(first)}–${String(last)}` : numbers.join(", ");
}

function CitedText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    parts.push(text.slice(last, match.index));
    for (const n of (match[1] ?? "").split(/\s*,\s*/).map(Number)) {
      parts.push(
        <a
          key={`${match.index}-${n}`}
          href={`#src-${n}`}
          className="ch-cite"
          aria-label={`Source ${n}`}
          onClick={(e) => {
            e.preventDefault();
            onCite(n);
          }}
        >
          {n}
        </a>,
      );
    }
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}

function SourceRow({ id, source, index, hot, uncited }: { id: string; source: Source; index: string | null | undefined; hot: boolean; uncited?: boolean }) {
  const { sessionId } = useParams();
  const percent = `${Math.round(Math.max(0, Math.min(1, source.relevance)) * 100)}%`;
  // SCR-03 opens the chunk "cited as [n]" with the way back here (SCR-03 for corpora, region 5).
  const back = `${uncited ? "" : `&cited=${String(source.n)}`}${sessionId ? `&chat=${sessionId}` : ""}`;
  return (
    <Link id={id} className={`ch-s${uncited ? " unc" : ""}${hot ? " is-hover" : ""}`} to={`${rowLink(index, source.row_key)}${index ? back : ""}`}>
      <span className="no">{source.n}</span>
      <span>
        <span className="where">
          {source.document} › {lastSections(source.section)}
        </span>
        <span className="snip">{source.snippet}</span>
      </span>
      <span className="rel">
        {source.relevance.toFixed(2)}
        <i style={{ ["--p" as string]: percent }} />
      </span>
    </Link>
  );
}

function Footer({ answer }: { answer: Answer }) {
  const t = answer.timings;
  if (!t.route && !answer.streaming) return null;
  const route = answer.details.abstained ? "RAG · abstained" : (MODES.find((m) => m.mode === answer.mode)?.label ?? t.route);
  return (
    <div className="ch-foot">
      <span className="route">{route}</span>
      {t.model && !answer.details.abstained ? <span>{shortModel(t)}</span> : null}
      {t.device ? <span>{t.device.toUpperCase()}</span> : null}
      {t.cold_start && t.load_ms ? <span className="cold">cold start {seconds(t.load_ms)}</span> : null}
      {answer.mode !== "images" && !answer.details.abstained ? (
        <span>
          first token <b>{seconds(t.ttft_ms)}</b>
        </span>
      ) : null}
      {answer.streaming ? (
        <span>generating…</span>
      ) : (
        <span>
          {answer.details.stopped ? "stopped at" : "total"} <b>{seconds(t.total_ms)}</b>
        </span>
      )}
      {t.tokens_per_s ? (
        <span>
          <b>{Math.round(t.tokens_per_s)}</b> tok/s
        </span>
      ) : null}
      {answer.mode === "images" && answer.details.searched ? (
        <span>
          <b>{answer.details.searched.toLocaleString()}</b> images searched
        </span>
      ) : null}
      {answer.mode === "rag" && t.index_version_id ? <span>index {t.index_version_id}</span> : null}
    </div>
  );
}

function DeleteSession({ session, onClose, onDeleted }: {
  session: SessionSummary | null;
  onClose: () => void;
  onDeleted: (id: string) => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  const [problem, setProblem] = useState<unknown>(null);
  return (
    <Modal
      open={session !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Delete this conversation?"
      icon={<Icon name="alert" />}
      alert
      initialFocus={cancel}
      footer={
        <>
          <Button ref={cancel} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (!session) return;
              deleteSession(session.id).then(() => { onDeleted(session.id); }, setProblem);
            }}
          >
            Delete
          </Button>
        </>
      }
    >
      “<b>{session?.title}</b>” and its messages are removed. The requests&apos; timings stay, for NFR-6. This cannot be undone.
      {problem ? <ErrorState error={problem} title="It could not be deleted" /> : null}
    </Modal>
  );
}
