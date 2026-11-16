// Empty and error states (DESIGN-SYSTEM c; principle 8). Errors show the problem's code,
// detail and suggested action from the API, with a retry (ADR-0011).
import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { Badge, Button, Icon, type IconName } from "./ui";

export function EmptyState({ icon = "db", title, children, action }: {
  icon?: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state">
      <Icon name={icon} />
      <b>{title}</b>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, title, onRetry }: {
  error: unknown;
  title: string;
  onRetry?: () => void;
}) {
  const problem = error instanceof ApiError ? error.problem : null;
  return (
    <div className="state err" role="alert">
      <Icon name="alert" />
      <b>{title}</b>
      {problem ? <Badge tone="err">{problem.code}</Badge> : null}
      <p>
        {problem?.detail ?? String(error)}
        {problem?.suggested_action ? (
          <>
            {" "}
            <b style={{ color: "var(--text-primary)" }}>Do:</b> {problem.suggested_action}
          </>
        ) : null}
      </p>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          <Icon name="refresh" />
          Retry
        </Button>
      ) : null}
      {problem?.request_id ? <span className="mono note">{problem.request_id}</span> : null}
    </div>
  );
}

/** Copy text to the clipboard; the button says so for a moment. */
export async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard access can be refused (permissions, insecure context); nothing to undo.
  }
}
