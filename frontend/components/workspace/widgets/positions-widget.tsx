"use client";

import { useCallback, useMemo, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney, formatQty } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";
import {
  resolvePositions,
  useDashboardPrefs,
  type TaggedPosition,
} from "@/lib/workspace/use-dashboard-prefs";

type SortKey =
  | "symbol"
  | "qty"
  | "side"
  | "current_price"
  | "market_value"
  | "unrealized_pl";

const COLUMNS: {
  label: string;
  key: SortKey;
  align: "left" | "right";
}[] = [
  { label: "Symbol", key: "symbol", align: "left" },
  { label: "Qty", key: "qty", align: "right" },
  { label: "Side", key: "side", align: "left" },
  { label: "Price", key: "current_price", align: "right" },
  { label: "Value", key: "market_value", align: "right" },
  { label: "Unrealized", key: "unrealized_pl", align: "right" },
];

function sortPositions(
  rows: TaggedPosition[],
  sortState: { key: SortKey; dir: "asc" | "desc" } | null,
): TaggedPosition[] {
  if (!sortState) return rows;

  const { key, dir } = sortState;
  const mul = dir === "asc" ? 1 : -1;

  return [...rows].sort((a, b) => {
    const va = a[key];
    const vb = b[key];

    if (key === "symbol" || key === "side") {
      return mul * String(va).localeCompare(String(vb));
    }

    return mul * ((va as number) - (vb as number));
  });
}

function PositionRow({
  p,
  idx,
  linked,
  onPublish,
}: {
  p: TaggedPosition;
  idx: number;
  linked: boolean;
  onPublish: (symbol: string) => void;
}) {
  const valueFlash = usePriceFlash(p.market_value);

  return (
    <tr
      key={`${p.accountLabel ?? ""}-${p.symbol}-${idx}`}
      className="group/row transition-colors hover:bg-[var(--bg-hover)]"
    >
      <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ClickableSymbol
            symbol={p.symbol}
            linked={linked}
            onActivate={() => onPublish(p.symbol)}
          />
          {p.accountLabel ? (
            <span className="rounded border border-[var(--border)] px-1 text-[9px] text-[var(--fg-faint)]">
              {p.accountLabel}
            </span>
          ) : null}
        </span>
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatQty(p.qty)}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] tracking-wide text-[var(--fg-faint)] uppercase">
        {p.side}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatMoney(p.current_price)}
      </td>
      <td
        className={cn(
          "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground",
          valueFlash === "up" && "tick-flash-up",
          valueFlash === "down" && "tick-flash-down",
        )}
      >
        {formatMoney(p.market_value)}
      </td>
      <td
        className={cn(
          "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums",
          p.unrealized_pl >= 0
            ? "text-[var(--c-green)]"
            : "text-[var(--c-red)]",
        )}
      >
        {p.unrealized_pl >= 0 ? "+" : ""}
        {formatMoney(p.unrealized_pl)}
      </td>
    </tr>
  );
}

export function PositionsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { snapshot, loading } = useSnapshot();
  const { selectedAccountId } = useDashboardPrefs();
  const positions = resolvePositions(snapshot, selectedAccountId);
  const [sortState, setSortState] = useState<{
    key: SortKey;
    dir: "asc" | "desc";
  } | null>(null);

  const sortedPositions = useMemo(
    () => sortPositions(positions, sortState),
    [positions, sortState],
  );

  const handleSortHeaderClick = useCallback((key: SortKey) => {
    setSortState((prev) => {
      if (prev?.key === key) {
        if (prev.dir === "asc") return { key, dir: "desc" };
        return null;
      }
      return { key, dir: "asc" };
    });
  }, []);

  if (loading && !snapshot) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (!snapshot) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        Waiting for positions…
      </p>
    );
  }

  if (positions.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No open positions
      </p>
    );
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr>
            {COLUMNS.map(({ label, key, align }) => (
              <th
                key={key}
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                  align === "left" ? "text-left" : "text-right",
                )}
              >
                <button
                  type="button"
                  onClick={() => handleSortHeaderClick(key)}
                  className={cn(
                    "w-full",
                    align === "left" ? "text-left" : "text-right",
                  )}
                >
                  {label}
                  {sortState?.key === key ? (
                    <span className="ml-0.5">
                      {sortState.dir === "asc" ? "▲" : "▼"}
                    </span>
                  ) : null}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedPositions.map((p, idx) => (
            <PositionRow
              key={`${p.accountLabel ?? ""}-${p.symbol}-${idx}`}
              p={p}
              idx={idx}
              linked={linked}
              onPublish={publish}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
