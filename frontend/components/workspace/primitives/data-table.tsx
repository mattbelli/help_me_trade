"use client";

import { Fragment, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** The house alignment rule: text anchors left, numbers go right. */
export type DataTableAlign = "left" | "right";

type PerRow<Row, T> = T | ((row: Row) => T | undefined);

export type DataTableColumn<Row, K extends string = string> = {
  key: K;
  header?: ReactNode;
  /** Accessible name for a deliberately blank header (icon/action columns). */
  headerLabel?: string;
  align?: DataTableAlign;
  /** The ONE filler column: takes all leftover width, truncates only when
   * genuinely out of room (the other columns are nowrap/content-sized), so
   * widening the widget widens this column. */
  absorb?: boolean;
  sortable?: boolean;
  headerClassName?: string;
  className?: PerRow<Row, string>;
  title?: PerRow<Row, string>;
  render: (row: Row) => ReactNode;
};

export type DataTableSort<K extends string = string> = {
  key: K;
  dir: "asc" | "desc";
};

export type DataTableProps<Row, K extends string = string> = {
  columns: readonly DataTableColumn<Row, K>[];
  rows: readonly Row[];
  rowKey: (row: Row, index: number) => string | number;
  /** Table-level override — e.g. a nested table at --fs-micro. */
  className?: string;
  rowClassName?: string | ((row: Row, index: number) => string | undefined);
  onRowClick?: (row: Row, index: number) => void;
  sort?: DataTableSort<K> | null;
  onSortChange?: (key: K) => void;
  /** Escape hatch: full-width detail row under `row`; null when collapsed. */
  renderExpanded?: (row: Row, index: number) => ReactNode;
  expandedClassName?: string;
  /** Full-width note row after the last data row (provenance/footnotes). */
  footer?: ReactNode;
};

const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
// group/row is load-bearing: ClickableSymbol's info button reveals on
// group-hover/row.
const ROW = "group/row transition-colors hover:bg-[var(--bg-hover)]";
// leading-[20px]: bordered rows at --fs-title need an integral pitch or
// Chromium snaps alternate 1px row borders to zero height (see globals.css).
const BODY = "leading-[20px]";

function resolve<Row, T>(
  value: PerRow<Row, T> | undefined,
  row: Row,
): T | undefined {
  return typeof value === "function"
    ? (value as (row: Row) => T | undefined)(row)
    : value;
}

/** The house data table: one source for the TH/TD/ROW dialect, header token,
 * row pitch and width-absorber idiom. Presentational only — sorting, fetching
 * and empty/loading/error states stay with the widget. */
export function DataTable<Row, K extends string = string>({
  columns,
  rows,
  rowKey,
  className,
  rowClassName,
  onRowClick,
  sort,
  onSortChange,
  renderExpanded,
  expandedClassName,
  footer,
}: DataTableProps<Row, K>) {
  return (
    <table
      className={cn(
        "w-full border-collapse font-mono text-[length:var(--fs-title)]",
        className,
      )}
    >
      <thead>
        <tr>
          {columns.map((col) => {
            const align = col.align ?? "left";
            const alignClass = align === "left" ? "text-left" : "text-right";
            return (
              <th
                key={col.key}
                aria-label={col.headerLabel}
                className={cn(TH, alignClass, col.headerClassName)}
              >
                {col.sortable && onSortChange ? (
                  <button
                    type="button"
                    onClick={() => onSortChange(col.key)}
                    // uppercase + alignClass re-applied: the UA button reset
                    // defeats inheritance from TH for both.
                    className={cn("w-full uppercase", alignClass)}
                  >
                    {col.header}
                    {sort?.key === col.key ? (
                      <span className="ml-0.5">
                        {sort.dir === "asc" ? "▲" : "▼"}
                      </span>
                    ) : null}
                  </button>
                ) : (
                  col.header
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody className={BODY}>
        {rows.map((row, index) => {
          const expanded = renderExpanded?.(row, index) ?? null;
          return (
            <Fragment key={rowKey(row, index)}>
              <tr
                className={cn(
                  ROW,
                  typeof rowClassName === "function"
                    ? rowClassName(row, index)
                    : rowClassName,
                )}
                onClick={
                  onRowClick ? () => onRowClick(row, index) : undefined
                }
                aria-expanded={renderExpanded ? expanded != null : undefined}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={cn(
                      TD,
                      col.align === "right" && "text-right",
                      col.absorb && "w-full max-w-0 truncate",
                      resolve(col.className, row),
                    )}
                    title={resolve(col.title, row)}
                  >
                    {col.render(row)}
                  </td>
                ))}
              </tr>
              {expanded ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className={cn(
                      "border-b border-[var(--line-divider)] bg-[var(--bg-hover)]",
                      expandedClassName,
                    )}
                  >
                    {expanded}
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
        {footer ? (
          <tr>
            <td
              colSpan={columns.length}
              className="px-2 py-1.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]"
            >
              {footer}
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}
