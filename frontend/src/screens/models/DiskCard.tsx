// SCR-11 region 6: disk use per lineage, measured on disk, in five parts (REG-2.11). The bar
// is an image named with every part; the figures beneath are its table view (SCR-11 notes).
import { type CSSProperties, Fragment } from "react";

import type { DiskReport } from "../../api/registry";
import { Icon, Skeleton } from "../../components/ui";
import { bytes } from "../../lib/format";

const PARTS: { key: keyof DiskReport["parts"]; label: string; color: string }[] = [
  { key: "base", label: "base", color: "var(--cat-1)" },
  { key: "latest", label: "latest", color: "var(--cat-2)" },
  { key: "intermediate", label: "intermediate", color: "var(--cat-3)" },
  { key: "pinned", label: "pinned", color: "var(--cat-4)" },
  { key: "checkpoints", label: "checkpoints", color: "var(--cat-5)" },
];

/** "base 1.2 MB, latest 1.2 MB, intermediate 0, …": the bar's accessible name. */
export function diskName(report: DiskReport): string {
  return PARTS.map((p) => {
    const value = report.parts[p.key];
    return `${p.label} ${value ? bytes(value) : "0"}`;
  }).join(", ");
}

export function DiskCard({ report, pending }: { report: DiskReport | undefined; pending: boolean }) {
  return (
    <section className="card" aria-label="Disk use">
      <h2>
        Disk use
        <span className="spacer" />
        <span className="num">{report ? bytes(report.disk_bytes) : ""}</span>
      </h2>
      {pending || !report ? (
        <Skeleton width={300} height={12} />
      ) : (
        <>
          <div className="stackbar" role="img" aria-label={diskName(report)}>
            {PARTS.map((p) => {
              const value = report.parts[p.key];
              if (!value || report.disk_bytes === 0) return null;
              const width = `${((100 * value) / report.disk_bytes).toFixed(1)}%`;
              return <i key={p.key} style={{ "--c": p.color, width } as CSSProperties} />;
            })}
          </div>
          <div className="parts">
            {PARTS.map((p) => (
              <Fragment key={p.key}>
                <span>
                  <i style={{ "--c": p.color } as CSSProperties} />
                  {p.label}
                </span>
                <span>{report.parts[p.key] ? bytes(report.parts[p.key]) : "0"}</span>
              </Fragment>
            ))}
            <span>on disk</span>
            {report.matches ? (
              <span className="ok">
                {bytes(report.disk_bytes)} <Icon name="check" />
                <span className="sr-only">matches the registry</span>
              </span>
            ) : (
              <span style={{ color: "var(--warning)" }}>
                {bytes(report.disk_bytes)} <Icon name="alert" /> registry says{" "}
                {bytes(report.registry_bytes)}
              </span>
            )}
          </div>
          {!report.matches ? (
            <p className="note2">
              {report.orphans.length ? `${String(report.orphans.length)} folder(s) no version accounts for. ` : ""}
              {report.missing.length ? `Missing: ${report.missing.join(", ")}. ` : ""}
              The next retention run removes orphans and finishes interrupted deletes (NFR-15).
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
