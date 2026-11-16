// Design-system components (DESIGN-SYSTEM.md c) as thin React wrappers over the approved
// classes in styles/components.css. Tokens only; no one-off colours or sizes.
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";

import type { JobState } from "../api/client";

export type IconName =
  | "alert" | "back" | "ban" | "branch" | "chart" | "check" | "chevron" | "clock" | "copy"
  | "db" | "down" | "eye" | "folder" | "grid" | "heart" | "hourglass" | "info" | "keys"
  | "layers" | "list" | "more" | "note" | "pause" | "play" | "plus" | "refresh" | "report"
  | "search" | "sort" | "theme" | "x";

export function Icon({ name, className, style }: {
  name: IconName;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg className={className ? `i ${className}` : "i"} style={style} aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>;
}

const STATE_ICON: Record<Exclude<JobState, "running">, IconName> = {
  queued: "clock",
  paused: "pause",
  completed: "check",
  failed: "x",
  cancelled: "ban",
  stalled: "hourglass",
};

const STATE_WORD: Record<JobState, string> = {
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  stalled: "Stalled",
};

/** A job's state: icon, word and colour, never colour alone (principle 3). */
export function StatePill({ state, label }: { state: JobState; label?: string }) {
  return (
    <span className={`pill ${state}`}>
      {state === "running" ? <i className="pulse" /> : <Icon name={STATE_ICON[state]} />}
      {label ?? STATE_WORD[state]}
    </span>
  );
}

/** "Idle": the queued pill's shape in a neutral colour, a word that is not a job state.
 * text-secondary, not the mockup's text-muted, which measured 3.78:1 in light (owner's
 * decision at SCR-01's G3, 2026-11-16). */
export function IdlePill() {
  return (
    <span className="pill queued" style={{ "--c": "var(--text-secondary)" } as CSSProperties}>
      <Icon name="clock" />
      Idle
    </span>
  );
}

export function Progress({ value, state = "running", className }: {
  value: number | null;
  state?: JobState;
  className?: string;
}) {
  const style = {
    "--p": `${String(value ?? 0)}%`,
    "--c": `var(--state-${state})`,
  } as CSSProperties;
  return (
    <span
      className={`progress${value === null ? " indeterminate" : ""}${className ? ` ${className}` : ""}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : Math.round(value)}
    >
      <i style={style} />
    </span>
  );
}

export function Skeleton({ width, height }: { width: number; height?: number }) {
  return <span className="skeleton" style={{ width, height, display: "inline-block" }} />;
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({ variant = "secondary", size = "md", className, children, ...rest }: {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = ["btn"];
  if (variant !== "secondary") classes.push(variant);
  if (size !== "md") classes.push(size);
  if (className) classes.push(className);
  return (
    <button type="button" className={classes.join(" ")} {...rest}>
      {children}
    </button>
  );
}

export function Badge({ tone, children }: {
  tone?: "info" | "warn" | "err" | "ok" | "debug";
  children: ReactNode;
}) {
  return <span className={tone ? `badge ${tone}` : "badge"}>{children}</span>;
}

/** 40 × 12 sparkline of the last samples (status bar), in a resource colour. */
export function Sparkline({ values, max, color }: {
  values: number[];
  max: number;
  color: string;
}) {
  if (values.length < 2) return <svg className="sp" viewBox="0 0 40 12" aria-hidden="true" />;
  const step = 40 / (values.length - 1);
  const points = values
    .map((v, i) => {
      const y = 11 - (Math.min(Math.max(v, 0), max) / max) * 10;
      return `${(i * step).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="sp" viewBox="0 0 40 12" preserveAspectRatio="none" aria-hidden="true">
      <polyline fill="none" stroke={color} strokeWidth="1.2" points={points} />
    </svg>
  );
}
