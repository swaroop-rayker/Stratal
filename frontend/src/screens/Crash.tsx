// A screen that throws while rendering shows the design system's error state, not React
// Router's developer page; the shell around it keeps working.
import { useNavigate, useRouteError } from "react-router";

import { Button, Icon } from "../components/ui";

export function Crash() {
  const error = useRouteError();
  const navigate = useNavigate();
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div style={{ padding: "var(--sp-6)" }}>
      <div className="state err" role="alert">
        <Icon name="alert" />
        <b>This screen stopped with an error</b>
        <p>
          <span className="mono">{message}</span>
          <br />
          <b style={{ color: "var(--text-primary)" }}>Do:</b> reload the page; if it happens again, note what you did and
          the message above.
        </p>
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          <Button size="sm" onClick={() => { window.location.reload(); }}>
            <Icon name="refresh" />
            Reload
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void navigate("/datasets")}>
            <Icon name="db" />
            Go to datasets
          </Button>
        </div>
      </div>
    </div>
  );
}
