// SCR-03 regions 6 and 7: the rows toolbar and the rows themselves, as a virtualised table
// or a grid of images. Filters live in the URL (status, reason, split, q, view).
import * as Menu from "@radix-ui/react-dropdown-menu";
import { useVirtualizer } from "@tanstack/react-virtual";
import { type CSSProperties, useEffect, useMemo, useRef } from "react";

import { type RowFilters, useRows } from "../../api/queries";
import { EmptyState, ErrorState } from "../../components/states";
import { Button, Icon, Kbd, Skeleton } from "../../components/ui";
import { bytes } from "../../lib/format";
import { useShortcuts } from "../../shell/hooks";
import { dims, imageHash, type Row, STATUS_CLASS, type Status, thumbUrl } from "./rows";

export const STATUSES: Status[] = ["accepted", "rejected", "duplicate", "filtered"];
const ROW_HEIGHT = 44;

export function RowStatus({ status }: { status: Status }) {
  return (
    <span className={`rs ${STATUS_CLASS[status]}`}>
      <i />
      {status}
    </span>
  );
}

function Thumb({ row }: { row: Row }) {
  const sha = imageHash(row);
  if (!sha) return <span className="thumb" style={{ "--a": "var(--surface-raised)", "--b": "var(--surface-raised)" } as CSSProperties} />;
  return <img className="thumb" src={thumbUrl(sha, 64)} alt="" loading="lazy" decoding="async" />;
}

const dash = <span className="muted">–</span>;

function Cells({ row, labelled, split, details }: { row: Row; labelled: boolean; split: boolean; details: boolean }) {
  const reason = row.reason_code ? (
    <td className="reason">
      {row.reason_code}
      {details && row.reason_detail ? ` · ${row.reason_detail}` : ""}
    </td>
  ) : (
    <td className="muted">–</td>
  );
  if (labelled) {
    return (
      <>
        {reason}
        <td className="caption" title={row.caption ?? undefined}>
          {row.caption ?? dash}
        </td>
        <td className="r">{row.clip_score === null || row.clip_score === undefined ? dash : row.clip_score.toFixed(3)}</td>
        {split ? <td>{row.split ?? dash}</td> : null}
      </>
    );
  }
  return (
    <>
      {reason}
      <td className="r">{bytes(row.size_bytes)}</td>
      <td className="r">{dims(row.width, row.height) ?? dash}</td>
      <td className="r">{dims(row.derived_width, row.derived_height) ?? dash}</td>
      <td className={row.dup_of ? "id" : "muted"}>{row.dup_of ?? "–"}</td>
      <td className="hash">{row.phash ?? dash}</td>
    </>
  );
}

export function RowsPanel({ versionId, filters, setFilter, reasons, splits, labelled, view, selected, onSelect, onOpen }: {
  versionId: string;
  filters: RowFilters;
  setFilter: (patch: Partial<Record<"status" | "reason" | "split" | "q" | "view", string | null>>) => void;
  reasons: string[];
  splits: string[];
  labelled: boolean;
  view: "table" | "grid";
  selected: number;
  onSelect: (index: number) => void;
  onOpen: (index: number, rows: Row[]) => void;
}) {
  const rows = useRows(versionId, filters);
  const items = useMemo(() => rows.data?.pages.flatMap((p) => p.items) ?? [], [rows.data]);
  const matched = rows.data?.pages[0]?.matched ?? 0;
  const scroller = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const split = splits.length > 0;

  const virtual = useVirtualizer({
    count: items.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
    // Before the first measurement (and in tests, which have no layout) assume a window's worth.
    initialRect: { width: 1000, height: 600 },
  });
  const visible = virtual.getVirtualItems();
  const lastVisible = visible[visible.length - 1]?.index ?? 0;
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = rows;
  useEffect(() => {
    if (view === "table" && hasNextPage && !isFetchingNextPage && lastVisible >= items.length - 20) {
      void fetchNextPage();
    }
  }, [view, lastVisible, items.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const bindings = useMemo(
    () => ({
      "/": () => search.current?.focus(),
      j: () => {
        const next = Math.min(items.length - 1, selected + 1);
        onSelect(next);
        virtual.scrollToIndex(next);
      },
      k: () => {
        const next = Math.max(0, selected - 1);
        onSelect(next);
        virtual.scrollToIndex(next);
      },
      Enter: () => {
        // Only when no control has focus or a row has it: Enter on a button is the button's.
        const active = document.activeElement;
        if (active && active !== document.body && active.tagName !== "TR") return;
        if (items[selected]) onOpen(selected, items);
      },
    }),
    [items, selected, onSelect, onOpen, virtual],
  );
  useShortcuts(bindings);

  const status = filters.status ?? null;
  const top = visible[0]?.start ?? 0;
  const bottom = virtual.getTotalSize() - (visible[visible.length - 1]?.end ?? 0);
  const columns = labelled
    ? ["", "Row key", "Status", "Reason", "Caption", "CLIP", ...(split ? ["Split"] : [])]
    : ["", "Row key", "Status", "Reason", "Size", "Original", "Copy", "Duplicate of", "pHash"];
  const right = new Set(["Size", "Original", "Copy", "pHash", "CLIP"]);

  return (
    <>
      <div className="toolbar">
        <span className="group-btns" role="group" aria-label="Status">
          {[null, ...STATUSES].map((s) => (
            <button
              key={s ?? "all"}
              type="button"
              className="btn sm"
              aria-pressed={status === s}
              onClick={() => {
                setFilter({ status: s });
              }}
            >
              {s ? s.charAt(0).toUpperCase() + s.slice(1) : "All"}
            </button>
          ))}
        </span>
        <Menu.Root>
          <Menu.Trigger asChild>
            <Button size="sm" aria-pressed={filters.reason_code !== undefined}>
              {filters.reason_code ?? "Reason"}
              <Icon name="down" />
            </Button>
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content className="menu-pop" align="start" sideOffset={4}>
              <Menu.Item onSelect={() => { setFilter({ reason: null }); }}>Any reason</Menu.Item>
              {reasons.map((r) => (
                <Menu.Item key={r} onSelect={() => { setFilter({ reason: r }); }}>
                  <span className="mono">{r}</span>
                </Menu.Item>
              ))}
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
        {split ? (
          <Menu.Root>
            <Menu.Trigger asChild>
              <Button size="sm" aria-pressed={filters.split !== undefined}>
                {filters.split ?? "Split"}
                <Icon name="down" />
              </Button>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu-pop" align="start" sideOffset={4}>
                <Menu.Item onSelect={() => { setFilter({ split: null }); }}>Any split</Menu.Item>
                {splits.map((s) => (
                  <Menu.Item key={s} onSelect={() => { setFilter({ split: s }); }}>
                    <span className="mono">{s}</span>
                  </Menu.Item>
                ))}
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        ) : null}
        <span className="spacer" />
        <span className="search">
          <Icon name="search" />
          <input
            ref={search}
            className="input"
            placeholder="Row key"
            aria-label="Filter by row key"
            defaultValue={filters.q ?? ""}
            onKeyDown={(e) => {
              if (e.key === "Enter") setFilter({ q: e.currentTarget.value.trim() || null });
              if (e.key === "Escape") e.currentTarget.blur();
            }}
            onBlur={(e) => {
              setFilter({ q: e.currentTarget.value.trim() || null });
            }}
          />
          <Kbd>/</Kbd>
        </span>
        <span className="group-btns" role="group" aria-label="View">
          <button type="button" className="btn sm" aria-pressed={view === "table"} onClick={() => { setFilter({ view: null }); }}>
            <Icon name="list" />
            Table
          </button>
          <button type="button" className="btn sm" aria-pressed={view === "grid"} onClick={() => { setFilter({ view: "grid" }); }}>
            <Icon name="grid" />
            Grid
          </button>
        </span>
      </div>

      {rows.isPending ? (
        <div className="table-wrap" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} style={{ display: "flex", gap: "var(--sp-3)", padding: "var(--sp-2)" }}>
              <Skeleton width={32} height={32} />
              <Skeleton width={220} />
              <Skeleton width={80} />
            </div>
          ))}
        </div>
      ) : rows.isError ? (
        <ErrorState error={rows.error} title="Rows could not be loaded" onRetry={() => void rows.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState icon="search" title="No rows match">
          Clear a filter to see more rows.
        </EmptyState>
      ) : view === "grid" ? (
        <div className="rows-wrap" ref={scroller}>
          <div className="grid-view" role="listbox" aria-label={`Rows, ${String(matched)}`}>
            {items.map((row, i) => {
              const sha = imageHash(row);
              return (
                <button
                  key={row.row_key}
                  type="button"
                  role="option"
                  className="gcard"
                  aria-selected={i === selected}
                  tabIndex={i === selected ? 0 : -1}
                  onClick={() => { onOpen(i, items); }}
                  onFocus={() => { onSelect(i); }}
                >
                  {sha ? <img src={thumbUrl(sha, 256)} alt="" loading="lazy" /> : <span className="noimg">no image</span>}
                  <span>{row.row_key}</span>
                  <RowStatus status={row.status} />
                </button>
              );
            })}
          </div>
          {rows.hasNextPage ? (
            <p style={{ textAlign: "center" }}>
              <Button size="sm" onClick={() => void rows.fetchNextPage()} disabled={rows.isFetchingNextPage}>
                {rows.isFetchingNextPage ? "Loading…" : `Show more (${String(items.length)} of ${String(matched)})`}
              </Button>
            </p>
          ) : null}
        </div>
      ) : (
        <div className="table-wrap rows-wrap" ref={scroller}>
          <table className="t" aria-label={`Rows, ${String(matched)}`} aria-rowcount={matched + 1}>
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={`${c}-${String(i)}`} className={right.has(c) ? "r" : undefined}>
                    {c || <span className="sr-only">Image</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {top > 0 ? <tr aria-hidden="true" style={{ height: top }} /> : null}
              {visible.map((v) => {
                const row = items[v.index] as Row;
                return (
                  <tr
                    key={row.row_key}
                    aria-rowindex={v.index + 2}
                    aria-selected={v.index === selected}
                    tabIndex={v.index === selected ? 0 : -1}
                    onClick={() => { onSelect(v.index); onOpen(v.index, items); }}
                  >
                    <td>
                      <Thumb row={row} />
                    </td>
                    <td className="id">{row.row_key}</td>
                    <td>
                      <RowStatus status={row.status} />
                    </td>
                    <Cells row={row} labelled={labelled} split={split} details={status === "rejected"} />
                  </tr>
                );
              })}
              {bottom > 0 ? <tr aria-hidden="true" style={{ height: bottom }} /> : null}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
