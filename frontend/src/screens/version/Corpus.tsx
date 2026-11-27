// SCR-03 for corpora (mockup SCR-03corpora_v1; SCREENS.md v5; DATA-5.3, LLM-2.1, LLM-2.2):
// an index version's facts, its actions, the chunk / document / pair drawers. The header,
// lineage, tabs and rows table are SCR-03's own; this file holds only what corpora add.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { ApiError, api, call, type Schemas } from "../../api/client";
import { useRows } from "../../api/queries";
import { copy } from "../../components/states";
import { Drawer } from "../../components/Drawer";
import { Button, Icon, Kbd } from "../../components/ui";
import { bytes, count } from "../../lib/format";
import { count as versionCount, type Row, type Version } from "./rows";
import { RowStatus } from "./RowsPanel";

type Lineage = Schemas["Lineage"];

/** What a corpus version's rows are: chunks (chunk, index), documents, or instruction pairs. */
export type CorpusKind = "chunks" | "documents" | "pairs";

export function corpusKind(v: Pick<Version, "dtype" | "stage">): CorpusKind | null {
  if (v.dtype === "instruction_pairs") return "pairs";
  if (v.dtype !== "text_corpus") return null;
  return v.stage === "chunk" || v.stage === "index" ? "chunks" : "documents";
}

function stageConfig(lineage: Lineage | undefined, version: Version, stage: string): Record<string, unknown> {
  if (version.stage === stage) return version.config;
  return lineage?.ancestors.find((a) => a.stage === stage)?.config ?? {};
}

/** The version that holds the corpus's documents: the last of clean_text and ingest. */
export function documentsVersion(lineage: Lineage | undefined, version: Version): string | null {
  if (corpusKind(version) === "documents") return version.version_id;
  const found = lineage?.ancestors.find((a) => a.stage === "clean_text") ?? lineage?.ancestors.find((a) => a.stage === "ingest");
  return found?.version_id ?? null;
}

function useIndexFacts(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["versions", id, "index"],
    queryFn: () => call(() => api.GET("/api/v1/versions/{version_id}/index", { params: { path: { version_id: id } } })),
    enabled,
    staleTime: Infinity,
  });
}

/** How many vectors the index stage took from the cache, from its job (none for the CLI). */
function useCached(version: Version) {
  const job = version.created_by_job_id;
  return useQuery({
    queryKey: ["jobs", job, "cached", version.version_id],
    queryFn: async () => {
      const found = await call(() => api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: job ?? "" } } }));
      const stage = found.stages.find((s) => s.output_version_id === version.version_id && s.name === "index");
      const cached = stage?.metrics?.cached;
      return typeof cached === "number" ? cached : null;
    },
    enabled: Boolean(job) && version.stage === "index",
    staleTime: Infinity,
  });
}

export function useRetrievalReport(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["versions", id, "report", "retrieval"],
    queryFn: async () => {
      try {
        return await call(() =>
          api.GET("/api/v1/versions/{version_id}/reports/latest", { params: { path: { version_id: id }, query: { kind: "retrieval" } } }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.problem.status === 404) return null;
        throw error;
      }
    },
    enabled,
  });
}

// ---------------------------------------------------------------------------- region 3, facts

function Fact({ k, v, d, small }: { k: string; v: ReactNode; d: ReactNode; small?: boolean }) {
  return (
    <div className="stile static">
      <span className="k">{k}</span>
      <span className={small ? "v sm" : "v"}>{v}</span>
      <span className="d">{d}</span>
    </div>
  );
}

/** An index version's facts: not filters, so the tile's shape without its edge or press. */
export function IndexFacts({ version, lineage }: { version: Version; lineage: Lineage | undefined }) {
  const facts = useIndexFacts(version.version_id, true);
  const cached = useCached(version);
  const chunk = stageConfig(lineage, version, "chunk");
  const f = facts.data;
  const wait = <span className="skeleton" style={{ display: "inline-block", width: 48, height: 18 }} />;
  return (
    <div className="tiles co-six" aria-busy={facts.isPending || undefined}>
      <Fact
        k="Chunks"
        v={count(versionCount(version, "accepted"))}
        d={typeof chunk.chunk_tokens === "number" ? `${String(chunk.chunk_tokens)} tokens, ${typeof chunk.overlap_tokens === "number" ? String(chunk.overlap_tokens) : "0"} overlap` : "–"}
      />
      <Fact k="Documents" v={f ? count(f.documents) : wait} d={f ? `${count(f.characters)} characters` : ""} />
      <Fact
        k="Vectors"
        v={f ? count(f.vectors) : wait}
        d={f ? `${String(f.dimension)} dimensions${typeof cached.data === "number" ? ` · ${count(cached.data)} from cache` : ""}` : ""}
      />
      <Fact k="Index on disk" v={f ? (f.size_bytes === null ? "gone" : bytes(f.size_bytes)) : wait} d="exact search (IndexFlatIP)" />
      <Fact
        k="Embedding model"
        small
        v={f ? f.model : wait}
        d={f ? `revision ${f.revision.slice(0, 7)} · ${f.query_prefix ? "query prefix set" : "no query prefix"}` : ""}
      />
      {facts.isError ? (
        <p className="foot-note" role="alert" style={{ gridColumn: "1 / -1" }}>
          The index's facts could not be loaded: {facts.error instanceof ApiError ? facts.error.problem.detail : String(facts.error)}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- region 1, actions

/** The header's actions on a corpus version: no review, train or transform (SCREENS.md v5). */
export function CorpusActions({ version, onContinue }: { version: Version; onContinue: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const index = version.stage === "index";
  const report = useRetrievalReport(version.version_id, index);
  const build = useMutation({
    mutationFn: () =>
      call(() =>
        api.POST("/api/v1/versions/{version_id}/reports", {
          params: { path: { version_id: version.version_id } },
          body: { kind: "retrieval" },
        }),
      ),
    onSuccess: (job) => {
      void client.invalidateQueries({ queryKey: ["versions", version.version_id, "report"] });
      void navigate(`/versions/${version.version_id}/report?kind=retrieval&job=${job.id}`);
    },
  });
  const allRejected = versionCount(version, "accepted") === 0;
  const reportLabel = report.data ? "Open the retrieval report" : build.isPending ? "Starting…" : "Build the retrieval report";
  return (
    <span className="co-acts">
      <span className="wide-action">
        <Button size="sm" variant="ghost" onClick={() => void copy(version.version_id)}>
          <Icon name="copy" />
          Copy ID
        </Button>
      </span>
      {index ? (
        <>
          <Button variant="primary" onClick={() => void navigate(`/chat?index=${version.version_id}`)}>
            <Icon name="chat" />
            Ask this index
          </Button>
          <Button
            title={reportLabel}
            disabled={build.isPending || report.isPending}
            onClick={() => {
              if (report.data) void navigate(`/versions/${version.version_id}/report?kind=retrieval`);
              else build.mutate();
            }}
          >
            <Icon name="report" />
            <span className="wide-label">{reportLabel}</span>
            <span className="narrow-label">Retrieval report</span>
          </Button>
        </>
      ) : null}
      <Button
        variant={index ? "secondary" : "primary"}
        aria-disabled={allRejected || undefined}
        className={allRejected ? "is-disabled" : undefined}
        title={allRejected ? "No row was accepted: there is nothing to continue from." : undefined}
        onClick={() => { if (!allRejected) onContinue(); }}
      >
        <Icon name="branch" />
        Continue the pipeline
      </Button>
    </span>
  );
}

// ---------------------------------------------------------------------------- region 4, cells

/** The section path without the document's title: "3. Identifiers › 3.1 …". */
export function sectionTail(path: string | null | undefined): string {
  if (!path) return "–";
  const parts = path.split(" › ");
  return parts.length > 1 ? parts.slice(1).join(" › ") : path;
}

function firstLine(text: string | null | undefined): string {
  const line = (text ?? "").split("\n").find((l) => l.trim()) ?? "";
  return line.trim().replace(/^#+\s*/, "");
}

function pair(row: Row): { instruction: string; input: string; output: string } | null {
  if (!row.record) return null;
  try {
    const value = JSON.parse(row.record) as Record<string, unknown>;
    const text = (k: string) => (typeof value[k] === "string" ? value[k] : "");
    return { instruction: text("instruction"), input: text("input"), output: text("output") };
  } catch {
    return null;
  }
}

export function corpusColumns(kind: CorpusKind, withDocument: boolean): { name: string; right?: boolean }[] {
  switch (kind) {
    case "chunks":
      return [{ name: "Chunk" }, ...(withDocument ? [{ name: "Document" }] : []), { name: "Section" }, { name: "Tokens", right: true }, { name: "Text" }];
    case "documents":
      return [{ name: "Path" }, { name: "Title" }, { name: "Characters", right: true }, { name: "Status" }];
    case "pairs":
      return [{ name: "Row" }, { name: "Instruction" }, { name: "Output" }, { name: "Status" }];
  }
}

const dash = <span className="muted">–</span>;

function Status({ row }: { row: Row }) {
  return (
    <td>
      <RowStatus status={row.status} />
      {row.reason_code ? <span className="reason"> {row.reason_code}</span> : null}
    </td>
  );
}

export function CorpusCells({ kind, row, withDocument }: { kind: CorpusKind; row: Row; withDocument: boolean }) {
  switch (kind) {
    case "chunks":
      return (
        <>
          <td className="key">{row.row_key}</td>
          {withDocument ? <td className="sec">{row.document ?? dash}</td> : null}
          <td className="sec" title={row.section ?? undefined}>{sectionTail(row.section)}</td>
          <td className="r">{row.token_count ?? dash}</td>
          <td className="first">{row.status === "accepted" ? firstLine(row.text) : <RowStatus status={row.status} />}</td>
        </>
      );
    case "documents":
      return (
        <>
          <td className="key">{row.row_key}</td>
          <td className="sec">{row.title ?? dash}</td>
          <td className="r">{row.text ? count(row.text.length) : dash}</td>
          <Status row={row} />
        </>
      );
    case "pairs": {
      const p = pair(row);
      return (
        <>
          <td className="key">{row.row_key}</td>
          <td className="first" style={{ color: "var(--text-primary)" }}>{p?.instruction || dash}</td>
          <td className="first">{p?.output || dash}</td>
          <Status row={row} />
        </>
      );
    }
  }
}

// ---------------------------------------------------------------------------- regions 5 and 8, drawers

/** The chunk keys around this one: the key's number after "#", one less or one more. */
export function neighbour(key: string, delta: number): string | null {
  const match = /^(.*#)(\d+)$/.exec(key);
  if (!match) return null;
  const [, head = "", digits = ""] = match;
  const n = Number(digits) + delta;
  return n < 0 ? null : `${head}${String(n).padStart(digits.length, "0")}`;
}

function useRowByKey(versionId: string, key: string | null) {
  const found = useRows(versionId, { q: key ?? "" }, key !== null);
  return found.data?.pages.flatMap((p) => p.items).find((r) => r.row_key === key) ?? null;
}

function Cited({ n, chat }: { n: string; chat: string | null }) {
  return (
    <div className="callout info co-cited">
      <Icon name="info" />
      <span>
        Cited as <span className="co-cite">{n}</span> in a chat answer
        {chat ? (
          <>
            {" · "}
            <Link className="mono" style={{ color: "var(--accent)" }} to={`/chat/${chat}`}>
              Back to the conversation
            </Link>
          </>
        ) : null}
      </span>
    </div>
  );
}

function ChunkBody({ versionId, row, lineage, version, onOpenRow }: {
  versionId: string;
  row: Row;
  lineage: Lineage | undefined;
  version: Version;
  onOpenRow: (key: string) => void;
}) {
  const navigate = useNavigate();
  const prevKey = neighbour(row.row_key, -1);
  const nextKey = neighbour(row.row_key, 1);
  const prev = useRowByKey(versionId, prevKey);
  const next = useRowByKey(versionId, nextKey);
  const docs = documentsVersion(lineage, version);
  const doc = useRowByKey(docs ?? versionId, docs && row.document ? row.document : null);
  const overlapTokens = stageConfig(lineage, version, "chunk").overlap_tokens;
  const text = row.text ?? "";
  const shared =
    prev && prev.document === row.document && prev.char_end !== null && prev.char_end !== undefined && row.char_start !== null && row.char_start !== undefined
      ? Math.max(0, Math.min(text.length, prev.char_end - row.char_start))
      : 0;
  const length = doc?.text?.length;
  return (
    <>
      <div className="kv">
        <span>Document</span>
        <span className="mono">{row.document ?? "–"}</span>
        <span>Section</span>
        <span>{row.section ?? "–"}</span>
        <span>Offsets</span>
        <span className="mono">
          {row.char_start !== null && row.char_start !== undefined && row.char_end !== null && row.char_end !== undefined
            ? `${count(row.char_start)}–${count(row.char_end)}${length ? ` of ${count(length)} characters` : ""}`
            : "–"}
        </span>
        <span>Tokens</span>
        <span className="mono">
          {row.token_count ?? "–"}
          {shared > 0 && typeof overlapTokens === "number" && overlapTokens > 0 ? ` · ${String(overlapTokens)} shared with the previous chunk` : ""}
        </span>
      </div>
      {row.status === "accepted" ? (
        <div className="co-text" tabIndex={0} aria-label="Chunk text">
          {shared > 0 ? (
            <>
              <span className="ovtag">overlap</span>
              <span className="ov">{text.slice(0, shared)}</span>
            </>
          ) : null}
          {text.slice(shared)}
        </div>
      ) : (
        <p className="foot-note">
          <RowStatus status={row.status} /> {row.reason_code ? <span className="mono">{row.reason_code}</span> : null}: a rejected document has no
          chunks.
        </p>
      )}
      <div className="co-nav">
        <Button size="sm" disabled={!prev} onClick={() => { if (prev) onOpenRow(prev.row_key); }}>
          <Icon name="back" />
          Previous chunk
        </Button>
        <Button size="sm" disabled={!next} onClick={() => { if (next) onOpenRow(next.row_key); }}>
          Next chunk
          <Icon name="chevron" />
        </Button>
        <span className="spacer" />
        {docs && row.document ? (
          <Button size="sm" variant="ghost" onClick={() => void navigate(`/versions/${docs}?row=${encodeURIComponent(row.document ?? "")}`)}>
            Open the document&apos;s row
          </Button>
        ) : null}
      </div>
    </>
  );
}

function DocumentBody({ row }: { row: Row }) {
  return (
    <>
      <div className="kv">
        <span>Path</span>
        <span className="mono">{row.row_key}</span>
        <span>Title</span>
        <span>{row.title ?? "–"}</span>
        <span>Characters</span>
        <span className="mono">{row.text ? count(row.text.length) : "–"}</span>
        <span>Status · decided at</span>
        <span>
          <RowStatus status={row.status} />
          {row.decided_at_stage ? ` · ${row.decided_at_stage}` : ""}
          {row.reason_code ? <span className="mono"> · {row.reason_code}</span> : null}
        </span>
      </div>
      {row.text ? (
        <div className="co-text" tabIndex={0} aria-label="Document text">
          {row.text.length > 4000 ? `${row.text.slice(0, 4000)} …` : row.text}
        </div>
      ) : null}
    </>
  );
}

const TEMPLATE = /(<\|im_start\|>\w+\n|<\|im_end\|>\n?)/;

/** The pair as the model will see it, the template's tokens set apart from the pair's text. */
function ChatFormat({ text }: { text: string }) {
  return (
    <pre className="co-chat" aria-label="Chat format">
      {text.split(TEMPLATE).map((part, i) =>
        TEMPLATE.test(part) ? (
          <span key={i} className="tok">{part}</span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </pre>
  );
}

function PairBody({ row }: { row: Row }) {
  const p = pair(row);
  return (
    <>
      <div className="co-pair">
        <span className="lbl">Instruction</span>
        <span className="val">{p?.instruction || "–"}</span>
        <span className="lbl">Input</span>
        <span className="val">{p?.input ? p.input : <span className="muted">none</span>}</span>
        <span className="lbl">Output</span>
        <span className="val">{p?.output.trim() ? p.output : <span className="muted">empty</span>}</span>
      </div>
      {row.status === "rejected" ? (
        <p className="foot-note">
          Rejected as <span className="mono">{row.reason_code}</span>
          {row.reason_detail ? ` · ${row.reason_detail}` : ""}: it is not given to the model.
        </p>
      ) : row.chat_format ? (
        <>
          <h3 className="sec" style={{ margin: 0 }}>
            As the model will see it <span className="muted">· the pairs&apos; adapter, Qwen3&apos;s chat template</span>
          </h3>
          <ChatFormat text={row.chat_format} />
        </>
      ) : null}
    </>
  );
}

export function CorpusDrawer({ kind, version, lineage, row, onClose, onMove, onOpenRow }: {
  kind: CorpusKind;
  version: Version;
  lineage: Lineage | undefined;
  row: Row;
  onClose: () => void;
  onMove: (delta: number) => void;
  onOpenRow: (key: string) => void;
}) {
  const [params] = useSearchParams();
  const cited = kind === "chunks" ? params.get("cited") : null;
  const chat = params.get("chat");
  const what = kind === "chunks" ? "Chunk" : kind === "pairs" ? "Pair" : "Document";
  const move = (delta: number) => {
    if (kind === "chunks") {
      const key = neighbour(row.row_key, delta);
      if (key) onOpenRow(key);
    } else {
      onMove(delta);
    }
  };
  return (
    <Drawer
      open
      className="row-drawer co-drawer"
      title={`${what} ${row.row_key}${cited ? `, cited as ${cited}` : ""}`}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      heading={
        <span style={{ display: "inline-flex", gap: "var(--sp-2)", alignItems: "center" }}>
          <Icon name={kind === "pairs" ? "chat" : "note"} />
          {/* The heading names the dialog: opened from a citation, it says so (a11y notes). */}
          <span className="sr-only">{what} </span>
          <b>{row.row_key}</b>
          {cited ? <span className="sr-only">, cited as {cited}</span> : null}
          {kind === "chunks" ? null : <RowStatus status={row.status} />}
        </span>
      }
      keys={
        kind === "chunks" ? (
          <>
            <Kbd>[</Kbd>
            <Kbd>]</Kbd>
          </>
        ) : (
          <>
            <Kbd>j</Kbd>
            <Kbd>k</Kbd>
          </>
        )
      }
      onKeyDown={(e) => {
        const keys: Record<string, number> = kind === "chunks" ? { "[": -1, "]": 1 } : { k: -1, j: 1 };
        const delta = keys[e.key];
        if (delta !== undefined && !(e.target instanceof HTMLInputElement)) {
          e.preventDefault();
          move(delta);
        }
      }}
    >
      <span className="sr-only" aria-live="polite">
        {row.row_key}
        {cited ? `, cited as ${cited}` : ""}
      </span>
      {cited ? <Cited n={cited} chat={chat} /> : null}
      {kind === "chunks" ? (
        <ChunkBody versionId={version.version_id} row={row} lineage={lineage} version={version} onOpenRow={onOpenRow} />
      ) : kind === "pairs" ? (
        <PairBody row={row} />
      ) : (
        <DocumentBody row={row} />
      )}
    </Drawer>
  );
}

/** SCR-03 frame D: a citation to a chunk of an index version that is gone. */
export function GoneCitation({ row, chat }: { row: string | null; chat: string | null }) {
  return (
    <div className="state err" role="alert">
      <Icon name="alert" />
      <b>This chunk is not here any more.</b>
      <p>
        {row ? <span className="mono">{row}</span> : "The chunk"} was cited from an index version since deleted. The conversation keeps the source as it
        was given.
      </p>
      {chat ? (
        <Link className="btn sm" to={`/chat/${chat}`}>
          <Icon name="back" />
          Back to the conversation
        </Link>
      ) : null}
    </div>
  );
}
