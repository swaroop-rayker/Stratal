// Number and time formats for live values (DESIGN-SYSTEM d: tabular mono, no reflow).

export const DASH = "—";

type Maybe = number | null | undefined;

export function percent(value: Maybe, digits = 0): string {
  return value === null || value === undefined ? DASH : `${value.toFixed(digits)}%`;
}

/** Megabytes as gigabytes with one decimal: 7372 → "7.2". */
export function gb(mb: Maybe): string {
  return mb === null || mb === undefined ? DASH : (mb / 1024).toFixed(1);
}

export function bytes(n: Maybe): string {
  if (n === null || n === undefined) return DASH;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Three significant digits at most: "867 MB", "1.1 GB", "64.2 KB".
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit] ?? "B"}`;
}

export function count(n: Maybe): string {
  return n === null || n === undefined ? DASH : n.toLocaleString("en-US");
}

/** "14m 32s", "2h 05m", "48s"; a dash when unknown. */
export function duration(seconds: Maybe): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(rest).padStart(2, "0")}s`;
  return `${rest}s`;
}

/** Local wall-clock time, "14:03:12". */
export function clock(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
}

/** "2h ago", "just now", for past times. */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return DASH;
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** A stage's progress as a percentage, or null when its total is unknown. */
export function stagePercent(step: Maybe, total: Maybe): number | null {
  if (step === null || step === undefined || !total) return null;
  return Math.min(100, (step / total) * 100);
}
