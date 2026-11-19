// Pieces SCR-11 and SCR-14 share: version labels, the pin toggle, and the deletion dialog
// that asks for a phrase to be typed (DESIGN-SYSTEM d, "Deleting data"; SCR-11 frame D,
// SCR-14 frame C).
import { type ReactNode, useId, useRef, useState } from "react";

import type { ModelVersion, RetentionPlan } from "../../api/registry";
import { Modal } from "../../components/Modal";
import { Button, Icon, StatePill } from "../../components/ui";
import { bytes, DASH } from "../../lib/format";

/** Trained = the completed colour, partial = the paused colour, base = a neutral outlined
 * label (approved with SCR-11's mockup). Never colour alone: each has its word. */
export function VersionLabel({ state }: { state: ModelVersion["state"] }) {
  if (state === "base") return <span className="vbase">Base</span>;
  return state === "trained" ? (
    <StatePill state="completed" label="Trained" />
  ) : (
    <StatePill state="paused" label="Partial" />
  );
}

export function metric(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : `${(100 * value).toFixed(1)}%`;
}

/** A pin toggle named with its version, with ``aria-pressed`` (SCR-11 notes). */
export function PinButton({ version, onToggle, busy }: {
  version: Pick<ModelVersion, "id" | "pinned" | "state" | "weights_status">;
  onToggle: (pinned: boolean) => void;
  busy?: boolean;
}) {
  const pinnable = version.state === "trained" && version.weights_status === "present";
  const name = `${version.pinned ? "Unpin" : "Pin"} ${version.id}`;
  return (
    <button
      type="button"
      className={version.pinned ? "pinbtn on" : "pinbtn"}
      aria-label={name}
      aria-pressed={version.pinned}
      disabled={busy || (!pinnable && !version.pinned)}
      title={pinnable || version.pinned ? name : "Only trained versions with weights on disk can be pinned"}
      onClick={(event) => {
        event.stopPropagation();
        onToggle(!version.pinned);
      }}
    >
      <Icon name="pin" />
    </button>
  );
}

/** Whether a typed confirmation matches: exactly, as the mockups say. */
export function confirms(typed: string, phrase: string): boolean {
  return typed === phrase;
}

/** The deletion dialog: what goes, what stays, and a phrase to type before deleting. */
export function ConfirmDeletion({ open, onOpenChange, title, plan, phrase, kept, busy, error, onConfirm, showLineage }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  plan: RetentionPlan;
  phrase: string;
  kept: ReactNode;
  busy?: boolean;
  error?: ReactNode;
  onConfirm: () => void;
  showLineage?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const keep = useRef<HTMLButtonElement>(null);
  const inputId = useId();
  const whyId = useId();
  const rows = plan.lineages.flatMap((lineage) =>
    lineage.delete.map((d) => ({ lineage: lineage.name, ...d })),
  );
  const size = rows.reduce((sum, row) => sum + row.weights_bytes, 0);
  const lineages = new Set(rows.map((row) => row.lineage)).size;
  const ok = confirms(typed, phrase);
  const label = `Delete ${String(rows.length)} version${rows.length === 1 ? "'s" : "s'"} weights`;
  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        if (!next) setTyped("");
        onOpenChange(next);
      }}
      title={title}
      width={560}
      alert
      initialFocus={keep}
      icon={<Icon name="alert" />}
      footer={
        <>
          <Button ref={keep} onClick={() => { onOpenChange(false); }}>
            Keep them
          </Button>
          <Button
            variant="danger"
            disabled={!ok || busy}
            aria-describedby={ok ? undefined : whyId}
            onClick={onConfirm}
          >
            <Icon name="ban" />
            {label}
          </Button>
        </>
      }
    >
      <div className="scr11 in-dialog">
        This deletes the weights of{" "}
        <b className="em">
          {rows.length} version{rows.length === 1 ? "" : "s"}
          {showLineage && lineages > 1 ? ` in ${String(lineages)} lineages` : ""}, {bytes(size)}
        </b>
        . Their metadata stays, so each can be re-trained.
        <div className="table-wrap" style={{ marginTop: "var(--sp-2)" }}>
          <table className="t">
            <thead>
              <tr>
                {showLineage ? <th>Lineage</th> : null}
                <th>Version</th>
                <th className="r">val acc</th>
                <th className="r">Size</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.version_id}>
                  {showLineage ? <td className="id">{row.lineage}</td> : null}
                  <td className="id">{row.version_id}</td>
                  <td className="r">{metric(row.main_metric)}</td>
                  <td className="r">{bytes(row.weights_bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note2" style={{ marginTop: "var(--sp-2)" }}>{kept}</p>
        <div className="typeconfirm">
          <label htmlFor={inputId}>
            Type <b className="mono em">{phrase}</b> to confirm
          </label>
          <input
            id={inputId}
            className="input mono"
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => { setTyped(event.target.value); }}
          />
          <small id={whyId} className="note2">
            {ok ? " " : `The delete button stays disabled until “${phrase}” is typed exactly.`}
          </small>
        </div>
        {error}
      </div>
    </Modal>
  );
}
