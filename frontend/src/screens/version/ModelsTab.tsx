// SCR-03 v2 Models (approved hi-fi SCR-03v2_v1, frame D; UI-2.1): the model versions trained
// on this version, grouped by lineage. The metric says which split it is on: test once
// scored, validation otherwise.
import { Fragment, useState } from "react";
import { Link } from "react-router";

import type { Schemas } from "../../api/client";
import { useVersionModels } from "../../api/queries";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";

type Trained = Schemas["TrainedModel"];
const SHOWN = 3;

const SHORT = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "–";

function kindText(m: Trained) {
  if (m.kind === "fine-tune") return <>fine-tune of <span className="mono">{m.parent_version_id}</span></>;
  if (m.kind === "study") return <>study's best · <Badge>computed once</Badge></>;
  if (m.kind === "classical") return "classical";
  return m.state === "partial" ? "run · partial" : "run";
}

function Lineage({ name, id, items }: { name: string; id: string; items: Trained[] }) {
  const [all, setAll] = useState(items.length <= SHOWN + 1);
  const shown = all ? items : items.slice(0, SHOWN);
  const rest = items.slice(SHOWN);
  const values = rest.map((m) => m.metric?.value).filter((v): v is number => typeof v === "number");
  return (
    <>
      <tr className="sec">
        <td colSpan={5}>
          {name} · <span className="mono lid">{id}</span>
          {items.length > 1 ? ` · ${String(items.length)} versions` : ""}
        </td>
      </tr>
      {shown.map((m) => (
        <tr key={m.id}>
          <td className="id"><Link to={`/models/${m.lineage_id}?version=${m.id}`}>{m.id}</Link></td>
          <td>{kindText(m)}</td>
          <td className="r">{m.metric ? `${m.metric.split} ${m.metric.value.toFixed(3)}` : "–"}</td>
          <td className={m.weights_status === "present" ? undefined : "gone"}>{m.weights_status.replace(/^weights_/, "")}</td>
          <td className="id">{SHORT(m.trained_at)}</td>
        </tr>
      ))}
      {!all ? (
        <tr>
          <td colSpan={5} className="lab">
            {count(rest.length)} more
            {values.length ? ` · ${rest[0]?.metric?.split ?? ""} ${Math.min(...values).toFixed(3)} to ${Math.max(...values).toFixed(3)}` : ""}
            {` · ${String(rest.filter((m) => m.weights_status !== "present").length)} with deleted weights`}{" "}
            <Button variant="ghost" size="sm" onClick={() => { setAll(true); }}>Show</Button>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function ModelsTab({ versionId, active, onTrain }: { versionId: string; active: boolean; onTrain: () => void }) {
  const models = useVersionModels(versionId, active);
  if (models.isError) return <ErrorState error={models.error} title="Models could not be loaded" onRetry={() => void models.refetch()} />;
  if (!models.data) return <Skeleton width={600} height={120} />;
  const items = models.data.items;
  const lineages = new Map<string, Trained[]>();
  for (const m of items) lineages.set(m.lineage_id, [...(lineages.get(m.lineage_id) ?? []), m]);
  return (
    <div className="v2stack">
      {items.length === 0 ? (
        <section className="card v2card">
          <EmptyState icon="grid" title="No model has been trained on this version" action={<Button size="sm" onClick={onTrain}><Icon name="branch" />Train a model…</Button>} />
        </section>
      ) : (
        <div className="table-wrap">
          <table className="t dense">
            <caption className="sr-only">Model versions trained on this version</caption>
            <thead><tr><th>Version</th><th>Kind</th><th className="r">Metric</th><th>Weights</th><th>Trained</th></tr></thead>
            <tbody>
              {[...lineages.entries()].map(([id, list]) => (
                <Fragment key={id}>
                  <Lineage name={list[0]?.lineage_name ?? id} id={id} items={list} />
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {items.length ? (
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          <Link className="btn sm" to={`/models/compare?split=${versionId}`}><Icon name="grid" />Compare on this test split</Link>
          <Button size="sm" onClick={onTrain}><Icon name="branch" />Train a model…</Button>
        </div>
      ) : null}
    </div>
  );
}
