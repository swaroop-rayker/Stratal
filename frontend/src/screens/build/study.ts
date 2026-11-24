// SCR-09's study part (HPO-1.1): a tuned field's spec, its marker for POST /jobs, and the
// checks the form makes as you type (the server repeats them; a 422 lands on the same row).
import type { RJSFSchema } from "@rjsf/utils";

import type { Marker } from "../../api/studies";

export type Scale = "linear" | "log" | "choices";

/** A tuned field: a range on a scale, or choices. */
export interface TuneSpec {
  scale: Scale;
  low?: number;
  high?: number;
  step?: number;
  choices?: unknown[];
}

/** The training fields a study may tune (stratal.hpo.space.NETWORK_FIELDS). */
export const NETWORK_TUNABLE = new Set(["epochs", "batch_size", "optimizer", "learning_rate", "momentum", "weight_decay", "schedule"]);

/** The numeric part of a field's schema: an anyOf of a number and null counts as the number. */
export function numeric(schema: RJSFSchema): RJSFSchema | null {
  if (schema.type === "integer" || schema.type === "number") return schema;
  const options = (schema.anyOf ?? []) as RJSFSchema[];
  return options.find((o) => o.type === "integer" || o.type === "number") ?? null;
}

export function choicesOf(schema: RJSFSchema): unknown[] | null {
  if (schema.enum) return [...schema.enum];
  if (schema.type === "boolean") return [true, false];
  return null;
}

export function isTunable(schema: RJSFSchema): boolean {
  return Boolean(numeric(schema) ?? choicesOf(schema)) && !schema.readOnly;
}

/** The field's own range, as the helper under a tuned field says it. */
export function ownRange(schema: RJSFSchema): { low: number | null; high: number | null; above: boolean } {
  const n = numeric(schema);
  if (!n) return { low: null, high: null, above: false };
  const low = n.minimum ?? n.exclusiveMinimum ?? null;
  return { low: typeof low === "number" ? low : null, high: typeof (n.maximum ?? n.exclusiveMaximum) === "number" ? Number(n.maximum ?? n.exclusiveMaximum) : null, above: n.exclusiveMinimum !== undefined };
}

/** A new spec for a field just turned on: its current value spread around, inside its range. */
export function startSpec(schema: RJSFSchema, value: unknown): TuneSpec {
  const choices = choicesOf(schema);
  if (choices) return { scale: "choices", choices };
  const n = numeric(schema) ?? {};
  const own = ownRange(schema);
  const v = typeof value === "number" ? value : typeof n.default === "number" ? n.default : own.low ?? 1;
  if (n.type === "integer") {
    const low = Math.max(own.low ?? 1, Math.round(v / 2) || 1);
    const high = Math.min(own.high ?? v * 4, Math.max(low + 1, v * 2));
    return { scale: "linear", low, high };
  }
  if (v > 0) {
    const low = Math.max(v / 10, own.above ? Number.MIN_VALUE : own.low ?? 0);
    return { scale: "log", low: Number(low.toPrecision(2)), high: Number(Math.min(v * 10, own.high ?? v * 10).toPrecision(2)) };
  }
  return { scale: "linear", low: own.low ?? 0, high: own.high ?? 1 };
}

/** What is wrong with a spec, by the rules the server applies to a marker (or null). */
export function specProblem(schema: RJSFSchema, spec: TuneSpec): string | null {
  const own = ownRange(schema);
  if (spec.scale === "choices") {
    const c = spec.choices ?? [];
    if (c.length < 2) return "Choose at least two values";
    if (new Set(c.map((v) => JSON.stringify(v))).size !== c.length) return "A choice is given twice";
    for (const v of c) {
      if (typeof v !== "number") continue;
      if ((own.low !== null && (own.above ? v <= own.low : v < own.low)) || (own.high !== null && v > own.high)) {
        return `Each must be ${String(own.low ?? "")}–${String(own.high ?? "")}`;
      }
    }
    return null;
  }
  const { low, high } = spec;
  if (low === undefined || high === undefined || Number.isNaN(low) || Number.isNaN(high)) return "Give a low and a high";
  if (spec.scale === "log" && low <= 0) return "A log scale needs a low above 0";
  if (low >= high) return "The low must be below the high";
  if (numeric(schema)?.type === "integer" && !(Number.isInteger(low) && Number.isInteger(high))) return "Whole numbers only";
  if ((own.low !== null && (own.above ? low <= own.low : low < own.low)) || (own.high !== null && high > own.high)) {
    return `Outside the field's range, ${String(own.low ?? "")} to ${String(own.high ?? "")}`;
  }
  return null;
}

export function toMarker(path: string, schema: RJSFSchema, spec: TuneSpec): Marker {
  if (spec.scale === "choices") return { path, type: "categorical", choices: spec.choices ?? [] };
  const integer = numeric(schema)?.type === "integer";
  const type = spec.scale === "log" && !integer ? "log_float" : integer ? "int" : "float";
  return { path, type, low: spec.low, high: spec.high, ...(spec.step && type !== "log_float" ? { step: spec.step } : {}) };
}

export function fromMarker(marker: Marker): TuneSpec {
  if (marker.type === "categorical") return { scale: "choices", choices: marker.choices ?? [] };
  return { scale: marker.type === "log_float" ? "log" : "linear", low: marker.low, high: marker.high, step: marker.step ?? undefined };
}

/** A spec in words, for the search space table: "1e-4 to 1e-1 · log", "64, 128, 256". */
export function describe(spec: TuneSpec): string {
  if (spec.scale === "choices") return (spec.choices ?? []).map((c) => (typeof c === "string" ? c : JSON.stringify(c))).join(", ");
  const show = (v: number | undefined) => (v === undefined ? "?" : Math.abs(v) < 1e-3 && v !== 0 ? v.toExponential(0) : String(v));
  return `${show(spec.low)} to ${show(spec.high)}${spec.scale === "log" ? " · log" : spec.step ? ` step ${String(spec.step)}` : ""}`;
}

export function typeWord(schema: RJSFSchema, spec: TuneSpec): string {
  if (spec.scale === "choices") return "choice";
  if (spec.scale === "log") return "log float";
  return numeric(schema)?.type === "integer" ? "int" : "float";
}

/** The largest value a spec can draw, for the worst case of the VRAM check. */
export function largest(spec: TuneSpec): number | null {
  if (spec.scale === "choices") {
    const nums = (spec.choices ?? []).filter((c): c is number => typeof c === "number");
    return nums.length ? Math.max(...nums) : null;
  }
  return spec.high ?? null;
}

/** The marker path of a training field, a classical parameter or a layer field. */
export const paths = {
  train: (field: string) => `/config/${field}`,
  param: (field: string) => `/classical/model/params/${field}`,
  layer: (node: string, field: string) => `/config/model/graph/nodes/${node}/layer/${field}`,
};

/** Map a 422's errors to search-space rows: index in `space` → message. */
export function spaceErrors(errors: { loc: (string | number)[]; msg: string }[]): Map<number, string> {
  const out = new Map<number, string>();
  for (const e of errors) {
    if (e.loc[0] === "space" && typeof e.loc[1] === "number") out.set(e.loc[1], e.msg);
  }
  return out;
}

/** About how long a study of networks takes: trials × (training + S-09's start of ~19 s). */
export const TRIAL_START_S = 19;
