// SCR-03 regions 8 and 9 (frame B): one row, every manifest field; for a duplicate, its
// group side by side with the kept row marked and the pHash differences highlighted.
// j / k move to the next row without closing; Esc closes.
import { useRows } from "../../api/queries";
import { copy } from "../../components/states";
import { Drawer } from "../../components/Drawer";
import { Button, Icon, Kbd, StatePill } from "../../components/ui";
import { bytes } from "../../lib/format";
import { dims, hexDiff, imageHash, phashDistance, type Row } from "./rows";
import { RowStatus } from "./RowsPanel";

function Hash({ hex, other }: { hex: string; other: string | null | undefined }) {
  return (
    <span className="hash">
      {hexDiff(hex, other).map((part, i) =>
        part.differs ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
      )}
    </span>
  );
}

function Photo({ row, kept }: { row: Row; kept?: boolean }) {
  const sha = imageHash(row);
  return (
    <div className={kept ? "photo kept" : "photo"}>
      {sha ? <img src={`/api/v1/blobs/${sha}?w=512`} alt={`Image of ${row.row_key}`} /> : null}
      {kept ? (
        <span className="tag">
          <StatePill state="completed" label="kept" />
        </span>
      ) : null}
    </div>
  );
}

function short(sha: string | null | undefined): string {
  return sha ? `${sha.slice(0, 8)}…${sha.slice(-8)}` : "–";
}

function Group({ versionId, row, threshold, onOpenRow }: {
  versionId: string;
  row: Row;
  threshold: number | null;
  onOpenRow: (key: string) => void;
}) {
  const group = useRows(versionId, { dup_group_id: row.dup_group_id ?? "" }, Boolean(row.dup_group_id));
  const members = group.data?.pages.flatMap((p) => p.items) ?? [];
  const kept = members.find((m) => m.dup_of === null) ?? null;
  const others = members.filter((m) => m !== kept);
  const shown = kept ? [kept, ...others.filter((m) => m.row_key === row.row_key), ...others.filter((m) => m.row_key !== row.row_key)] : members;
  const pair = shown.slice(0, 2);
  const distance = phashDistance(pair[0]?.phash, pair[1]?.phash);
  return (
    <>
      <div className="group-head">
        <b>{row.dup_kind === "exact" ? "Exact-duplicate group" : "Near-duplicate group"}</b>
        <span className="mono">{row.dup_group_id}</span>· {members.length || "…"} rows
        {distance !== null ? (
          <>
            {" · pHash distance "}
            <span className="dist">
              {distance}
              {threshold !== null ? ` / ${String(threshold)}` : ""}
            </span>
            {threshold !== null ? <span className="muted">(threshold)</span> : null}
          </>
        ) : null}
      </div>
      <div className="pair">
        {pair.map((m, i) => (
          <div key={m.row_key} className="card-img">
            <Photo row={m} kept={m === kept} />
            <span className="mono">{m.row_key}</span>
            <span>
              {m.status}
              {m.dup_kind && m !== kept ? ` · ${m.dup_kind}` : ""} · {dims(m.width, m.height) ?? "–"} · {bytes(m.size_bytes)}
            </span>
            {m.phash ? <Hash hex={m.phash} other={pair[1 - i]?.phash} /> : null}
          </div>
        ))}
      </div>
      {members.length > 2 ? (
        <p className="foot-note">
          {members.length - 2} more in this group:{" "}
          {shown.slice(2).map((m) => (
            <span key={m.row_key} className="mono">
              {m.row_key}{" "}
            </span>
          ))}
        </p>
      ) : null}
      {kept && kept.row_key !== row.row_key ? (
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          <Button size="sm" onClick={() => { onOpenRow(kept.row_key); }}>
            <Icon name="chevron" />
            Open kept row
          </Button>
        </div>
      ) : null}
    </>
  );
}

export function RowDrawer({ versionId, row, threshold, onClose, onMove, onOpenRow }: {
  versionId: string;
  row: Row;
  threshold: number | null;
  onClose: () => void;
  onMove: (delta: number) => void;
  onOpenRow: (key: string) => void;
}) {
  const reason = row.reason_code
    ? `${row.reason_code}${row.dup_of ? ` · ${row.dup_kind ?? ""} duplicate of ${row.dup_of}` : row.reason_detail ? ` · ${row.reason_detail}` : ""}`
    : null;
  return (
    <Drawer
      open
      className="row-drawer"
      title={`Row ${row.row_key}`}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      heading={
        <span style={{ display: "inline-flex", gap: "var(--sp-2)", alignItems: "center" }}>
          <b>{row.row_key}</b>
          <RowStatus status={row.status} />
        </span>
      }
      keys={
        <>
          <Kbd>j</Kbd>
          <Kbd>k</Kbd>
        </>
      }
      onKeyDown={(e) => {
        if (e.key === "j" || e.key === "k") {
          e.preventDefault();
          onMove(e.key === "j" ? 1 : -1);
        }
      }}
    >
      <span className="sr-only" aria-live="polite">
        {row.row_key}, {row.status}
      </span>
      {row.dup_group_id ? (
        <Group versionId={versionId} row={row} threshold={threshold} onOpenRow={onOpenRow} />
      ) : (
        <div className="card-img single">
          <Photo row={row} />
        </div>
      )}
      <div className="kv">
        <span>Status · decided at</span>
        <span>
          <RowStatus status={row.status} />
          {row.decided_at_stage ? ` · ${row.decided_at_stage}` : ""}
        </span>
        <span>Reason</span>
        <span>{reason ? <span className="mono">{reason}</span> : <span className="muted">–</span>}</span>
        <span>Original</span>
        <span>
          {row.format ?? "–"} · {dims(row.width, row.height) ?? "–"} · {bytes(row.size_bytes)}
        </span>
        <span>SHA-256</span>
        <span className="mono" title={row.sha256 ?? undefined}>{short(row.sha256)}</span>
        <span>Copy</span>
        <span>
          {row.derived_sha256 ? (
            <>
              <span className="mono" title={row.derived_sha256}>{short(row.derived_sha256)}</span> ·{" "}
              {dims(row.derived_width, row.derived_height)}
            </>
          ) : (
            <span className="muted">none: only accepted rows get one</span>
          )}
        </span>
        {row.phash ? (
          <>
            <span>pHash</span>
            <span className="mono">{row.phash}</span>
          </>
        ) : null}
        {row.caption ? (
          <>
            <span>Caption</span>
            <span>{row.caption}</span>
            <span>Caption model</span>
            <span className="mono">
              {row.caption_model} · {row.caption_model_revision?.slice(0, 7)}
            </span>
            <span>CLIP score</span>
            <span className="mono">
              {row.clip_score?.toFixed(3)} · {row.clip_model}
            </span>
          </>
        ) : null}
        {row.split ? (
          <>
            <span>Split · stratum</span>
            <span className="mono">
              {row.split} · {row.split_stratum}
            </span>
          </>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: "var(--sp-2)" }}>
        <Button size="sm" variant="ghost" onClick={() => void copy(row.row_key)}>
          <Icon name="copy" />
          Copy row key
        </Button>
      </div>
    </Drawer>
  );
}
