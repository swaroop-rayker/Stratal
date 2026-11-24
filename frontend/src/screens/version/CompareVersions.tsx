// SCR-03 v2 Compare (approved hi-fi SCR-03v2_v1, frame B; DATA-3.5): this version (B)
// against another of the dataset (A), rows matched by row_key. The config difference comes
// first, since it is usually the cause; wording-only rows are folded into one line.
import { useState } from "react";

import type { Schemas } from "../../api/client";
import { useVersionDiff, useVersions } from "../../api/queries";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";
import { STATUS_CLASS, type Status } from "./rows";

type Diff = Schemas["VersionDiff"];

const SHORT = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "";

function value(v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "string") return v.length > 20 ? `${v.slice(0, 12)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function Cell({ field, v }: { field: string; v: unknown }) {
  if (field === "status" && typeof v === "string") {
    return <td><span className={`rs ${STATUS_CLASS[v as Status]}`}><i />{v}</span></td>;
  }
  return <td className="id">{value(v)}</td>;
}

function ConfigCallout({ diff }: { diff: Diff }) {
  const changes = diff.config_diff.filter((c) => c.field !== null);
  const lone = diff.config_diff.filter((c) => c.field === null);
  if (!changes.length && !lone.length) {
    return (
      <div className="callout info"><Icon name="info" /><span>Their lineages have the same configs{diff.stages_differ ? `, up to A's stage (${diff.a.stage})` : ""}.</span></div>
    );
  }
  const stages = [...new Set(changes.map((c) => c.stage))];
  return (
    <div className="callout info">
      <Icon name="info" />
      <span>
        {stages.length ? (
          <>
            <b>Their lineages differ at {stages.join(", ")}:</b>{" "}
            {changes.map((c, i) => (
              <span key={`${c.stage}-${c.field ?? ""}`}>
                {i ? ", " : ""}
                <span className="mono">{c.field} {value(c.a)} → {value(c.b)}</span>
              </span>
            ))}
            .{" "}
          </>
        ) : null}
        {lone.map((c) => (
          <span key={c.stage}>{c.stage} is in {c.a === "present" ? "A" : "B"}'s lineage only. </span>
        ))}
        {diff.stages_differ ? <>A is a {diff.a.stage} version and B a {diff.b.stage} one: fields only one has are listed once.</> : null}
      </span>
    </div>
  );
}

function Rows({ diff, onOpen }: { diff: Diff; onOpen: (key: string) => void }) {
  const [wording, setWording] = useState(false);
  const shown = diff.changed_rows.length;
  return (
    <section className="card v2card">
      <h2>Changed rows <span className="sub2">A → B</span></h2>
      {shown === 0 && diff.wording_only === 0 ? (
        <p className="note2">No row changed.</p>
      ) : (
        <div className="table-wrap">
          <table className="t dense">
            <caption className="sr-only">Changed rows, A and B</caption>
            <thead><tr><th>Row</th><th>Field</th><th>A</th><th>B</th></tr></thead>
            <tbody>
              {diff.changed_rows.flatMap((row) =>
                row.fields.map((f, i) => (
                  <tr key={`${row.row_key}-${f.field}`}>
                    {i === 0 ? (
                      <td className="id rowkey" rowSpan={row.fields.length}>
                        <button type="button" className="linkish mono" onClick={() => { onOpen(row.row_key); }}>{row.row_key}</button>
                      </td>
                    ) : null}
                    <td>{f.field}</td>
                    <Cell field={f.field} v={f.a} />
                    <Cell field={f.field} v={f.b} />
                  </tr>
                )),
              )}
              {diff.wording_only ? (
                <tr>
                  <td colSpan={4} className="lab">
                    {count(diff.wording_only)} rows: reason wording only
                    {diff.wording_rows[0] ? ` ("${diff.wording_rows[0].a ?? ""}" → "${diff.wording_rows[0].b ?? ""}")` : ""}{" "}
                    <Button variant="ghost" size="sm" onClick={() => { setWording(!wording); }} aria-expanded={wording}>{wording ? "Hide" : "Show"}</Button>
                  </td>
                </tr>
              ) : null}
              {wording
                ? diff.wording_rows.map((w) => (
                    <tr key={`w-${w.row_key}`} className="wording">
                      <td className="id">{w.row_key}</td><td>reason_detail</td><td className="lab">{w.a ?? "–"}</td><td className="lab">{w.b ?? "–"}</td>
                    </tr>
                  ))
                : null}
            </tbody>
          </table>
        </div>
      )}
      <p className="note2">A row opens its drawer on this version (B). Up to {diff.shown_limit} changed rows are listed{diff.changed > shown ? `; ${count(diff.changed - shown)} more are counted` : ""}.</p>
    </section>
  );
}

export function CompareTab({ versionId, slug, stage, createdAt, parentId, other, onOther, onOpen }: {
  versionId: string;
  slug: string;
  stage: string;
  createdAt: string | null | undefined;
  parentId: string | null;
  other: string | null;
  onOther: (id: string | null, swap?: boolean) => void;
  onOpen: (key: string) => void;
}) {
  const versions = useVersions(slug);
  const chosen = other ?? parentId;
  const diff = useVersionDiff(versionId, chosen);
  const options = (versions.data?.items ?? []).filter((v) => v.version_id !== versionId);
  const d = diff.data;
  return (
    <div className="v2stack">
      <section className="card v2card">
        <h2>Compare with</h2>
        <div className="cmp-pick">
          <label>
            A{" "}
            <select className="input mono" value={chosen ?? ""} onChange={(e) => { onOther(e.target.value || null); }} aria-label="Version A">
              <option value="">Pick a version…</option>
              {options.map((v) => (
                <option key={v.version_id} value={v.version_id}>{v.version_id} · {v.stage} · {SHORT(v.created_at)}</option>
              ))}
            </select>
          </label>
          <Button variant="ghost" size="sm" disabled={!chosen} onClick={() => { onOther(chosen, true); }}>Swap</Button>
          <span>B, this version <span className="mono">{versionId}</span> · {stage} · {SHORT(createdAt)}</span>
        </div>
        {d ? <ConfigCallout diff={d} /> : null}
      </section>
      {!chosen ? (
        <section className="card v2card">
          <EmptyState icon="db" title="Pick a version to compare with">{slug} has {count(options.length + 1)} versions.</EmptyState>
        </section>
      ) : diff.isError ? (
        <ErrorState error={diff.error} title="The two versions could not be compared" onRetry={() => void diff.refetch()} />
      ) : !d ? (
        <div className="cmp-grid" aria-busy="true"><section className="card v2card"><Skeleton width={200} height={90} /></section><section className="card v2card"><Skeleton width={500} height={140} /></section></div>
      ) : d.changed === 0 && d.added === 0 && d.removed === 0 && d.wording_only === 0 ? (
        <section className="card v2card">
          <EmptyState icon="check" title="These two versions have the same rows">No row was added, removed or changed.</EmptyState>
        </section>
      ) : (
        <div className="cmp-grid">
          <section className="card v2card">
            <h2>Rows <span className="sub2">by row_key</span></h2>
            <div className="counts">
              <span>added in B</span><span>{count(d.added)}</span>
              <span>removed in B</span><span>{count(d.removed)}</span>
              <span><b>changed</b></span><span><b>{count(d.changed)}</b></span>
              <span>reason wording only</span><span>{count(d.wording_only)}</span>
              <span>the same</span><span>{count(d.same)}</span>
            </div>
            {Object.keys(d.by_field).length ? (
              <>
                <h2 style={{ marginTop: "var(--sp-2)" }}>Changed, by field</h2>
                <div className="counts">
                  {Object.entries(d.by_field).flatMap(([field, n]) => [
                    <span key={`${field}-k`}>{field}</span>,
                    <span key={`${field}-v`}>{count(n)}</span>,
                  ])}
                </div>
              </>
            ) : null}
            {d.only_in_a.length || d.only_in_b.length ? (
              <p className="note2">
                {d.only_in_b.length ? <>Only in B: <span className="mono">{d.only_in_b.join(", ")}</span>. </> : null}
                {d.only_in_a.length ? <>Only in A: <span className="mono">{d.only_in_a.join(", ")}</span>.</> : null}
              </p>
            ) : null}
          </section>
          <Rows diff={d} onOpen={onOpen} />
        </div>
      )}
    </div>
  );
}
