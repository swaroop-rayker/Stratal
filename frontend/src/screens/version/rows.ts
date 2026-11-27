// Manifest-row helpers for SCR-03 (DATA_MODEL.md §5).
import type { Schemas } from "../../api/client";
import { bytes } from "../../lib/format";

export type Row = Schemas["Row"];
export type Version = Schemas["Version"];
export type Status = Row["status"];

export const STATUS_CLASS: Record<Status, string> = {
  accepted: "acc",
  rejected: "rej",
  duplicate: "dup",
  filtered: "fil",
};

export const STATUS_COLOR: Record<Status, string> = {
  accepted: "var(--success)",
  rejected: "var(--danger)",
  duplicate: "var(--text-muted)",
  filtered: "var(--warning)",
};

/** The image to show: the preprocessed copy when there is one, else the original. */
export function imageHash(row: Row): string | null {
  return row.derived_sha256 ?? row.sha256 ?? null;
}

export function thumbUrl(sha: string, width: number): string {
  return `/api/v1/blobs/${sha}?w=${String(width)}`;
}

export function dims(w: number | null | undefined, h: number | null | undefined): string | null {
  return w && h ? `${String(w)} × ${String(h)}` : null;
}

/** Hamming distance between two pHash hex strings, or null when either is missing. */
export function phashDistance(a: string | null | undefined, b: string | null | undefined): number | null {
  if (!a || !b || a.length !== b.length) return null;
  let bits = 0;
  for (let i = 0; i < a.length; i++) {
    let x = parseInt(a.charAt(i), 16) ^ parseInt(b.charAt(i), 16);
    while (x) {
      bits += x & 1;
      x >>= 1;
    }
  }
  return bits;
}

/** Split a hex string into runs that are the same as, or differ from, another one. */
export function hexDiff(hex: string, other: string | null | undefined): { text: string; differs: boolean }[] {
  const parts: { text: string; differs: boolean }[] = [];
  for (let i = 0; i < hex.length; i++) {
    const differs = other ? other.charAt(i) !== hex.charAt(i) : false;
    const last = parts[parts.length - 1];
    if (last && last.differs === differs) last.text += hex.charAt(i);
    else parts.push({ text: hex.charAt(i), differs });
  }
  return parts;
}

export function count(version: Version, key: string): number {
  const value = version.counts[key];
  return typeof value === "number" ? value : 0;
}

/** The one fact a lineage node shows (DESIGN-SYSTEM c, lineage node). */
export function nodeFact(v: Version, current = false): string {
  if (current && v.stage === "preprocess") {
    const added = v.new_blob_bytes > 0 ? ` · +${bytes(v.new_blob_bytes)}` : "";
    return `${count(v, "accepted").toLocaleString("en-US")} copies${added}`;
  }
  const n = (k: string) => count(v, k).toLocaleString("en-US");
  const config = v.config as Record<string, unknown>;
  if (v.dtype === "text_corpus" || v.dtype === "instruction_pairs") {
    // SCR-03 for corpora: one corpus fact per node (mockup SCR-03corpora_v1, region 2).
    const what = v.dtype === "text_corpus" ? "documents" : "pairs";
    switch (v.stage) {
      case "ingest":
        return count(v, "rejected") ? `${n("accepted")} ${what} · ${n("rejected")} rejected` : `${n("accepted")} ${what}`;
      case "clean_text":
        return `${n("accepted")} kept · ${n("duplicate")} duplicates`;
      case "chunk":
        return `${n("accepted")} chunks${typeof config.chunk_tokens === "number" ? ` · ${String(config.chunk_tokens)} tokens` : ""}`;
      case "index": {
        const model = typeof config.embedding_model === "string" ? config.embedding_model.replace(/-en-v[\d.]+$/, "") : "";
        return `${n("accepted")} vectors${model ? ` · ${model}` : ""}`;
      }
    }
  }
  switch (v.stage) {
    case "ingest":
      return `${n("accepted")} accepted`;
    case "clean":
      return `${n("duplicate")} duplicates`;
    case "preprocess":
      return typeof config.max_side === "number" ? `max ${String(config.max_side)}` : `${n("accepted")} copies`;
    case "label":
      return `${n("accepted")} captioned`;
    case "filter":
      return `${n("filtered")} filtered`;
    case "split": {
      const parts = ["train", "val", "test"].map((k) => config[k]).filter((p): p is number => typeof p === "number");
      return parts.length === 3
        ? parts.map((p) => String(Math.round(p * 100))).join(" / ")
        : `${n("accepted")} accepted`;
    }
    default:
      return `${n("accepted")} accepted`;
  }
}
