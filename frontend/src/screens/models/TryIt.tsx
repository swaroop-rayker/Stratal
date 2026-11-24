// SCR-11 region 8 and frame E: a sample input for one version, its prediction, and the
// device that served it (REG-1.4, ADR-0016). The result is announced politely; a refusal
// is an alert (SCR-11 notes).
import { useRef, useState } from "react";

import { ApiError, api, call } from "../../api/client";
import { useVersion } from "../../api/queries";
import { type Prediction, usePredict } from "../../api/registry";
import { Badge, Button, Icon } from "../../components/ui";
import { RecordTry, TextTry } from "./TryValues";

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      resolve(text.slice(text.indexOf(",") + 1));
    };
    reader.onerror = () => { reject(reader.error ?? new Error("unreadable file")); };
    reader.readAsDataURL(file);
  });
}

/** "Served on the CPU: job_0031 holds the GPU · 38 ms". */
export function servedText(result: Prediction): string {
  const device = result.device === "gpu" ? "GPU" : "CPU";
  const why = result.fallback_reason ? `: ${result.fallback_reason}` : "";
  // A cold start says so, and times over a second read in seconds (SCR-11 v2, frame C).
  const cold = result.load_ms ? ` · cold start ${(result.load_ms / 1000).toFixed(1)} s` : "";
  const total = result.total_ms >= 1000 ? `${(result.total_ms / 1000).toFixed(1)} s` : `${String(Math.round(result.total_ms))} ms`;
  return `Served on the ${device}${why}${cold} · ${total}`;
}

export function TryIt({ versionId, datasetVersion }: { versionId: string | null; datasetVersion: string | null }) {
  const predict = usePredict(versionId ?? "");
  const [preview, setPreview] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  function send(body: { image_b64?: string; blob?: string }, shown: string) {
    setPreview(shown);
    predict.mutate(body);
  }

  async function fromFile(chosen: File | undefined) {
    if (!chosen) return;
    send({ image_b64: await toBase64(chosen) }, URL.createObjectURL(chosen));
  }

  async function fromDataset() {
    if (!datasetVersion) return;
    const page = await call(() =>
      api.GET("/api/v1/versions/{version_id}/rows", {
        params: { path: { version_id: datasetVersion }, query: { status: "accepted", split: "test", limit: 100 } },
      }),
    );
    const rows = page.items.filter((row) => row.derived_sha256 ?? row.sha256);
    const row = rows[Math.floor(Math.random() * rows.length)];
    const sha = row?.derived_sha256 ?? row?.sha256;
    if (!sha) return;
    send({ blob: sha }, `/api/v1/blobs/${sha}?w=128`);
  }

  const result = predict.data;
  const refused = predict.error instanceof ApiError ? predict.error.problem : null;
  // M11 (SCR-11 v2, frames C and D): a table takes a record, a text a text box.
  const dtype = useVersion(datasetVersion ?? "", datasetVersion !== null).data?.dtype;
  if (versionId !== null && dtype === "tabular") return <RecordTry versionId={versionId} datasetVersion={datasetVersion} />;
  if (versionId !== null && dtype === "labelled_text") return <TextTry versionId={versionId} datasetVersion={datasetVersion} />;
  return (
    <section className="card" aria-label="Try it">
      <h2>
        Try it
        <span className="spacer" />
        <span className="mono" style={{ fontWeight: 400, fontSize: "var(--fs-11)", color: "var(--text-secondary)" }}>
          {versionId ?? "no version with weights"}
        </span>
      </h2>
      {versionId === null ? (
        <p className="note2">Every version of this lineage has had its weights deleted; re-train one first.</p>
      ) : (
        <>
          <div
            className="drop"
            style={dragging ? { borderColor: "var(--accent)" } : undefined}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => { setDragging(false); }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              void fromFile(event.dataTransfer.files[0]);
            }}
          >
            <span>
              Drop an image,{" "}
              <button type="button" className="btn sm ghost" onClick={() => file.current?.click()}>
                choose one
              </button>{" "}
              or{" "}
              <button type="button" className="btn sm ghost" disabled={!datasetVersion} onClick={() => void fromDataset()}>
                pick one from the dataset
              </button>
            </span>
            <input
              ref={file}
              type="file"
              accept="image/*"
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(event) => void fromFile(event.target.files?.[0])}
            />
          </div>
          <div aria-live="polite">
            {predict.isPending ? <p className="note2">Predicting…</p> : null}
            {result ? (
              <>
                <div style={{ display: "flex", gap: "var(--sp-3)", alignItems: "center" }}>
                  {preview ? <img className="thumb" src={preview} alt="the image sent" /> : null}
                  <div className="pred" style={{ flex: 1 }}>
                    {(result.top ?? []).map((t, i) =>
                      i === 0 ? (
                        <span key={t.class} style={{ display: "contents" }}>
                          <b>{t.class}</b>
                          <b>{t.probability === null ? "–" : t.probability.toFixed(2)}</b>
                        </span>
                      ) : (
                        <span key={t.class} style={{ display: "contents" }}>
                          <span>{t.class}</span>
                          <span>{t.probability === null ? "–" : t.probability.toFixed(2)}</span>
                        </span>
                      ),
                    )}
                  </div>
                </div>
                <p className="note2">{servedText(result)}</p>
              </>
            ) : null}
          </div>
          {refused ? (
            <div role="alert" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", fontSize: "var(--fs-12)", color: "var(--text-secondary)" }}>
              <span>
                <Icon name="alert" style={{ color: "var(--danger)", verticalAlign: "-2px" }} /> <Badge tone="err">{refused.code}</Badge>
              </span>
              <span>{refused.detail}</span>
              {refused.suggested_action ? <span>{refused.suggested_action}</span> : null}
            </div>
          ) : null}
          {result || refused ? (
            <Button size="sm" variant="ghost" onClick={() => { predict.reset(); setPreview(null); }}>
              Clear
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}
