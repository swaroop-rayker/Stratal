// SCR-11 region 7: the N in force, Preview cleanup (the dry run, REG-2.9), an override for
// this lineage (REG-2.4), and the last run. Lowering N goes through frame D: the versions
// and sizes it deletes, and the lineage's name typed to confirm (REG-2.10).
import { useState } from "react";
import { Link } from "react-router";

import { ApiError } from "../../api/client";
import {
  fetchPlan,
  type Keep,
  keepText,
  type LineageDetail,
  planOf,
  type RetentionPlan,
  useRetentionRun,
  useSaveSettings,
  useSettings,
} from "../../api/registry";
import { Modal, ModalClose } from "../../components/Modal";
import { ErrorState } from "../../components/states";
import { Button, Icon } from "../../components/ui";
import { bytes, clock } from "../../lib/format";
import { ConfirmDeletion, metric } from "./parts";

function KeptLine({ plan, lineageId }: { plan: RetentionPlan; lineageId: string }) {
  const item = plan.lineages.find((l) => l.lineage_id === lineageId);
  const pinned = item?.keep.filter((k) => k.reason.startsWith("pinned")).map((k) => k.version_id) ?? [];
  return (
    <>
      Kept: the base, the latest
      {pinned.length ? (
        <>
          , and the pinned <span className="mono">{pinned.join(", ")}</span>
        </>
      ) : null}
      .
    </>
  );
}

export function RetentionCard({ lineage, onQueued }: {
  lineage: LineageDetail;
  onQueued: (jobId: string) => void;
}) {
  const settings = useSettings();
  const save = useSaveSettings();
  const run = useRetentionRun();
  const [preview, setPreview] = useState<RetentionPlan | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [override, setOverride] = useState(false);
  const [mode, setMode] = useState<"number" | "all">("number");
  const [n, setN] = useState("0");
  const [confirm, setConfirm] = useState<{ plan: RetentionPlan; keep: Keep | null; run?: boolean } | null>(null);
  const section = settings.data?.sections.find((s) => s.name === "retention");
  const info = lineage.retention;

  function openOverride() {
    const current = info.keep_intermediate;
    setMode(current === "all" ? "all" : "number");
    setN(current === "all" ? "0" : String(current));
    save.reset();
    setOverride(true);
  }

  /** Save this lineage's override (``null`` removes it); a deletion asks first. */
  function saveOverride(keep: Keep | null, token?: string) {
    if (!section) return;
    const values = section.values as { keep_intermediate: Keep; overrides: Record<string, Keep> };
    const overrides = Object.fromEntries(
      Object.entries(values.overrides).filter(([id]) => id !== lineage.id),
    );
    if (keep !== null) overrides[lineage.id] = keep;
    save.mutate(
      { section: "retention", values: { ...values, overrides }, version: section.version, confirm: token ?? null },
      {
        onSuccess: (saved) => {
          setOverride(false);
          setConfirm(null);
          if (saved.job_id) onQueued(saved.job_id);
        },
        onError: (error) => {
          const plan = planOf(error);
          if (plan) {
            setOverride(false);
            setConfirm({ plan, keep });
          }
        },
      },
    );
  }

  async function openPreview() {
    setPreviewError(null);
    try {
      setPreview(await fetchPlan(lineage.id));
    } catch (error) {
      setPreviewError(error);
    }
  }

  const last = info.last_run;
  const planItem = preview?.lineages.find((l) => l.lineage_id === lineage.id);
  const conflict = save.error instanceof ApiError && save.error.problem.code === "conflict";
  return (
    <section className="card" aria-label="Retention">
      <h2>Retention</h2>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--text-secondary)" }}>
        Keep <b className="em">{keepText(info.keep_intermediate)}</b> intermediate versions ·{" "}
        {info.source === "lineage" ? "this lineage's override" : "global default"}
      </div>
      <div style={{ display: "flex", gap: "var(--sp-2)" }}>
        <Button size="sm" onClick={() => void openPreview()}>
          <Icon name="eye" />
          Preview cleanup
        </Button>
        <Button size="sm" variant="ghost" disabled={!section} onClick={openOverride}>
          Override for this lineage…
        </Button>
      </div>
      {previewError ? <ErrorState error={previewError} title="The preview could not be made" /> : null}
      <p className="note2">
        {last ? (
          <>
            Last run {clock(last.finished_at).slice(0, 5)}{" "}
            {last.versions_deleted
              ? `freed ${bytes(last.bytes_freed)} from ${String(last.versions_deleted)} version${last.versions_deleted === 1 ? "" : "s"}`
              : "deleted nothing"}{" "}
            (<Link className="mono" to={`/jobs/${last.job_id}`}>{last.job_id}</Link>).
          </>
        ) : (
          "No retention run has covered this lineage yet."
        )}
      </p>

      <Modal
        open={preview !== null}
        onOpenChange={(open) => { if (!open) setPreview(null); }}
        title={`Preview cleanup of ${lineage.name}`}
        width={640}
        icon={<Icon name="eye" />}
        footer={
          <>
            <ModalClose asChild>
              <Button>Close</Button>
            </ModalClose>
            <Button
              variant="danger"
              disabled={!planItem || (planItem.delete.length === 0 && planItem.checkpoints.length === 0) || run.isPending}
              onClick={() => {
                if (!preview) return;
                if (planItem && planItem.delete.length > 0) {
                  setConfirm({ plan: preview, keep: null, run: true });
                  setPreview(null);
                } else {
                  run.mutate(lineage.id, { onSuccess: (job) => { setPreview(null); onQueued(job.id); } });
                }
              }}
            >
              Run cleanup now
            </Button>
          </>
        }
      >
        {planItem ? (
          <div className="scr11 in-dialog" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
            <span>
              With keep_intermediate = <b className="em">{keepText(planItem.keep_intermediate)}</b> ({planItem.source}): this changes nothing until you run it.
            </span>
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr><th>Version</th><th>Would</th><th className="r">val acc</th><th className="r">Size</th></tr>
                </thead>
                <tbody>
                  {[
                    ...planItem.delete.map((d) => ({ ...d, would: "be deleted" })),
                    ...planItem.blocked.map((d) => ({ ...d, would: "be kept: refused" })),
                    ...planItem.keep.map((d) => ({ ...d, would: "be kept" })),
                  ].map((d) => (
                    <tr key={d.version_id}>
                      <td className="id">{d.version_id}</td>
                      <td style={{ whiteSpace: "normal" }}>{d.would} <span className="note2">({d.reason})</span></td>
                      <td className="r">{metric(d.main_metric)}</td>
                      <td className="r">{bytes(d.weights_bytes)}</td>
                    </tr>
                  ))}
                  {planItem.checkpoints.map((c) => (
                    <tr key={c.job_id}>
                      <td className="id">checkpoint of {c.job_id}</td>
                      <td style={{ whiteSpace: "normal" }}>be deleted <span className="note2">({c.reason})</span></td>
                      <td className="r">—</td>
                      <td className="r">{bytes(c.bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <span>
              Frees <b className="em">{bytes(planItem.bytes_to_free)}</b>.
            </span>
            {run.isError ? <ErrorState error={run.error} title="Cleanup was not started" /> : null}
          </div>
        ) : null}
      </Modal>

      <Modal
        open={override}
        onOpenChange={setOverride}
        title={`keep_intermediate for ${lineage.name}`}
        width={460}
        footer={
          <>
            {info.source === "lineage" ? (
              <Button variant="ghost" disabled={save.isPending} onClick={() => { saveOverride(null); }}>
                Remove override
              </Button>
            ) : null}
            <span className="spacer" />
            <ModalClose asChild>
              <Button>Cancel</Button>
            </ModalClose>
            <Button
              variant="primary"
              disabled={save.isPending || (mode === "number" && !/^\d{1,3}$/.test(n)) || (mode === "number" && Number(n) > 100)}
              onClick={() => { saveOverride(mode === "all" ? "all" : Number(n)); }}
            >
              Save
            </Button>
          </>
        }
      >
        <div className="scr11 in-dialog" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
          <span>Intermediate versions this lineage keeps besides base and latest. 0–100, or all.</span>
          <span style={{ display: "flex", gap: "var(--sp-2)" }}>
            <span className="group-btns" role="group" aria-label="Keep">
              <button type="button" className="btn sm" aria-pressed={mode === "number"} onClick={() => { setMode("number"); }}>Number</button>
              <button type="button" className="btn sm" aria-pressed={mode === "all"} onClick={() => { setMode("all"); }}>All</button>
            </span>
            {mode === "number" ? (
              <input
                className="input mono"
                aria-label="keep_intermediate"
                inputMode="numeric"
                style={{ width: 72, height: "var(--h-sm)" }}
                value={n}
                onChange={(event) => { setN(event.target.value); }}
              />
            ) : null}
          </span>
          <small className="note2">Lowering it lists what it would delete before anything is saved (REG-2.10).</small>
          {conflict ? (
            <p role="alert" className="note2">The settings changed elsewhere; close this and try again.</p>
          ) : save.isError && !planOf(save.error) ? (
            <ErrorState error={save.error} title="The override was not saved" />
          ) : null}
        </div>
      </Modal>

      {confirm ? (
        <ConfirmDeletion
          open
          onOpenChange={(open) => { if (!open) setConfirm(null); }}
          title={
            confirm.run
              ? `Delete these weights of ${lineage.name}?`
              : `Keep ${keepText(confirm.keep ?? 0)} intermediate versions of ${lineage.name}?`
          }
          plan={confirm.plan}
          phrase={lineage.name}
          kept={<KeptLine plan={confirm.plan} lineageId={lineage.id} />}
          busy={save.isPending || run.isPending}
          error={save.isError && !planOf(save.error) ? <ErrorState error={save.error} title="Not saved" /> : null}
          onConfirm={() => {
            if (confirm.run) {
              run.mutate(lineage.id, { onSuccess: (job) => { setConfirm(null); onQueued(job.id); } });
            } else {
              saveOverride(confirm.keep, confirm.plan.token);
            }
          }}
        />
      ) : null}
    </section>
  );
}
