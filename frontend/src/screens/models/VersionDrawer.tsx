// SCR-11 frame C: one version and everything that made it (REG-1.1); a version whose
// weights retention deleted keeps its metadata and offers Re-train (REG-2.7).
import { useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router";

import { usePin, useRetrain, useVersionDetail } from "../../api/registry";
import { Drawer } from "../../components/Drawer";
import { ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { clock, DASH } from "../../lib/format";
import { VersionLabel } from "./parts";
import { TaskMetrics } from "./TaskMetrics";

function short(hash: string): string {
  return hash.length > 10 ? `${hash.slice(0, 4)}…${hash.slice(-4)}` : hash;
}

export function VersionDrawer({ versionId, onClose, onTry }: {
  versionId: string | null;
  onClose: () => void;
  onTry: (id: string) => void;
}) {
  const detail = useVersionDetail(versionId);
  // The version last shown, for returning focus to its row once versionId is null again.
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (versionId !== null) last.current = versionId;
  }, [versionId]);
  const pin = usePin();
  const retrain = useRetrain();
  const navigate = useNavigate();
  const v = detail.data;
  // The parent: the lineage's base, or since M11 a trained version, maybe in another lineage
  // (RUN-1.10; SCR-11 v2 frame E).
  const parent = useVersionDetail(v?.parent_version_id ?? null).data;
  return (
    <Drawer
      open={versionId !== null}
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={versionId ?? "Version"}
      heading={
        <h2 className="mono" style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)" }}>
          {versionId}
          {v ? <VersionLabel state={v.state} /> : null}
        </h2>
      }
      width={560}
      returnFocus={() => document.querySelector<HTMLElement>(`tr[data-version="${last.current ?? ""}"]`)}
    >
      <div className="scr11 in-dialog" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        {detail.isPending ? (
          <Skeleton width={360} height={120} />
        ) : detail.isError || !v ? (
          <ErrorState error={detail.error} title="The version could not be loaded" onRetry={() => void detail.refetch()} />
        ) : (
          <>
            {v.weights_status === "weights_deleted" ? (
              <div className="callout">
                <span style={{ flex: 1 }}>
                  Weights deleted by retention
                  {v.weights_deleted_at ? ` at ${clock(v.weights_deleted_at).slice(0, 5)}` : ""}
                  {v.weights_deleted_by_job_id ? (
                    <>
                      {" "}(<Link className="mono" to={`/jobs/${v.weights_deleted_by_job_id}`}>{v.weights_deleted_by_job_id}</Link>)
                    </>
                  ) : null}
                  . Metadata kept.
                </span>
                {v.state === "trained" ? (
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={retrain.isPending}
                    onClick={() => {
                      retrain.mutate(v.id, {
                        onSuccess: (job) => void navigate(`/jobs/${job.id}`),
                      });
                    }}
                  >
                    <Icon name="refresh" />
                    Re-train
                  </Button>
                ) : null}
              </div>
            ) : null}
            {retrain.isError ? <ErrorState error={retrain.error} title="Re-train was refused" /> : null}
            {v.note ? <p className="note2">{v.note}</p> : null}
            <div className="kv">
              <span>Dataset version</span>
              <span className="mono">
                {v.dataset_version_id ? <Link to={`/versions/${v.dataset_version_id}`}>{v.dataset_version_id}</Link> : DASH}
              </span>
              <span>Job</span>
              <span className="mono">{v.job_id ? <Link to={`/jobs/${v.job_id}`}>{v.job_id}</Link> : DASH}</span>
              <span>Created</span>
              <span className="mono">{clock(v.trained_at ?? v.created_at).slice(0, 5)}</span>
              <span>Parent</span>
              <span className="mono">
                {v.parent_version_id ? (
                  parent && parent.state !== "base" ? (
                    <>
                      <Link to={`/models/${parent.lineage_id}?version=${parent.id}`}>{parent.id}</Link>
                      {parent.lineage_id !== v.lineage_id ? <> in <b>{parent.lineage_name}</b> <Badge>another lineage</Badge></> : null}
                    </>
                  ) : `${v.parent_version_id} (base)`
                ) : v.state === "base" ? "none: this is the base" : "none"}
              </span>
              <span>Config hash</span>
              <span className="mono" title={v.config_hash}>{short(v.config_hash)}</span>
              {v.study_id ? (
                <>
                  <span>Study</span>
                  <span className="mono"><Link to={`/studies/${v.study_id}`}>{v.study_id}</Link> · its best trial</span>
                </>
              ) : null}
              <span>Retention</span>
              <span>{v.retention_reason}</span>
            </div>
            <TaskMetrics
              version={v}
              onRetrain={v.weights_status === "weights_deleted" || v.state !== "trained" ? null : () => {
                retrain.mutate(v.id, { onSuccess: (job) => void navigate(`/jobs/${job.id}`) });
              }}
            />
            <pre className="code">{JSON.stringify(v.config, null, 2)}</pre>
            <div style={{ display: "flex", gap: "var(--sp-2)" }}>
              {v.state === "trained" ? (
                <Button
                  size="sm"
                  aria-pressed={v.pinned}
                  disabled={pin.isPending || (v.weights_status !== "present" && !v.pinned)}
                  onClick={() => { pin.mutate({ id: v.id, pinned: !v.pinned }); }}
                >
                  <Icon name="pin" />
                  {v.pinned ? "Unpin" : "Pin"}
                </Button>
              ) : null}
              <Button
                size="sm"
                disabled={v.weights_status !== "present" || v.state === "partial"}
                onClick={() => { onTry(v.id); }}
              >
                Try it
              </Button>
              {v.state === "trained" && v.weights_status === "present" && (v.config as { model?: { kind?: string } } | null)?.model && (v.config as { model: { kind?: string } }).model.kind !== "sklearn" ? (
                // RUN-1.10: a trained network starts a run from its weights (SCR-09 v2, decision 1).
                <Button size="sm" onClick={() => { void navigate(`/runs/new?init_from=${v.id}`); }}>
                  <Icon name="branch" />
                  Fine-tune…
                </Button>
              ) : null}
            </div>
          </>
        )}
      </div>
    </Drawer>
  );
}
