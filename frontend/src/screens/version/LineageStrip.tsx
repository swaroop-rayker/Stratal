// SCR-03 region 3: ancestors back to raw, this version, and its descendants (DATA-3.3), as a
// list of links in order; forks become rows (frame E); a job about to add a child is a
// dashed node (frame A). Plain HTML rather than React Flow: the approved mockup is a strip.
import { Fragment, type ReactNode, useEffect, useRef } from "react";
import { Link } from "react-router";

import type { Schemas } from "../../api/client";
import { stagePercent } from "../../lib/format";
import { nodeFact, type Version } from "./rows";

type Lineage = Schemas["Lineage"];

function Node({ v, current }: { v: Version; current: boolean }) {
  const state = v.created_by_job_state ?? "completed";
  const cls = `node ${state === "completed" ? "done" : state}${current ? " selected" : ""}`;
  const label = `${v.stage}${current ? " · this version" : ""}`;
  const body = (
    <>
      <small>{label}</small>
      <b>{v.version_id}</b>
      <span>{nodeFact(v, current)}</span>
    </>
  );
  return current ? (
    <div className={cls} aria-current="true">
      {body}
    </div>
  ) : (
    <Link className={cls} to={`/versions/${v.version_id}`} aria-label={`${v.stage} ${v.version_id}, ${nodeFact(v)}`}>
      {body}
    </Link>
  );
}

const Edge = () => <span className="edge" aria-hidden="true" />;

export function LineageStrip({ lineage, current }: { lineage: Lineage; current: Version }) {
  const strip = useRef<HTMLElement>(null);
  // A long chain scrolls sideways: keep this version in view (frame I).
  useEffect(() => {
    const el = strip.current;
    const node = el?.querySelector<HTMLElement>("[aria-current]");
    if (!el || !node) return;
    const right = node.offsetLeft + node.offsetWidth + 48;
    if (right > el.clientWidth) el.scrollLeft = right - el.clientWidth;
  }, [current.version_id, lineage]);
  const children = new Map<string, Version[]>();
  for (const v of lineage.descendants) {
    const parent = v.parent_version_id ?? "";
    children.set(parent, [...(children.get(parent) ?? []), v]);
  }

  const pending = lineage.in_progress.map((job) => {
    const pct = stagePercent(job.step, job.total);
    return (
      <div key={job.job_id} className="node pending" aria-label={`${job.stage}, ${job.state}, will add a child version`}>
        <small>
          {job.stage} · {job.state}
        </small>
        <b>
          {job.job_id}
          {pct === null ? "" : ` · ${String(Math.round(pct))}%`}
        </b>
        <span>will add a child version</span>
      </div>
    );
  });

  /** A version and everything after it; several children fork into rows. */
  const branch = (v: Version, isCurrent: boolean): ReactNode => {
    const after: { key: string; node: ReactNode }[] = (children.get(v.version_id) ?? []).map((c) => ({
      key: c.version_id,
      node: branch(c, false),
    }));
    if (isCurrent) after.push(...pending.map((p, i) => ({ key: `p${String(i)}`, node: p })));
    return (
      <>
        <Node v={v} current={isCurrent} />
        {after.length === 1 ? (
          <>
            <Edge />
            {after[0]?.node}
          </>
        ) : after.length > 1 ? (
          <>
            <Edge />
            <div className="fork">
              {after.map((a) => (
                <div key={a.key} className="row">
                  {a.node}
                </div>
              ))}
            </div>
          </>
        ) : null}
      </>
    );
  };

  const ancestors = [...lineage.ancestors].reverse(); // raw first
  return (
    <nav className="lineage" aria-label="Lineage" ref={strip}>
      {ancestors.map((v) => (
        <Fragment key={v.version_id}>
          <Node v={v} current={false} />
          <Edge />
        </Fragment>
      ))}
      {branch(current, true)}
    </nav>
  );
}
