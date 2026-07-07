"use client";

import { useCallback, useMemo, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { formatMoney, formatQty, type SchwabAccount } from "@/lib/api";
import { useLiveMarks } from "@/lib/use-live-marks";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";
import {
  isRealScope,
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

// Delta-adjust a snapshot row from a live regular-session mark. `current_price`
// is the REST baseline; each poll re-baselines it, so the delta never walks.
function markPaper(
  p: TaggedPosition,
  marks: Record<string, number>,
): TaggedPosition {
  const px = marks[p.symbol];
  if (px == null || px === p.current_price) return p;
  const delta = p.qty * (px - p.current_price);
  return {
    ...p,
    current_price: px,
    market_value: p.market_value + delta,
    unrealized_pl: p.unrealized_pl + delta,
  };
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
            <span className="rounded border border-[var(--border)] px-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              {p.accountLabel}
            </span>
          ) : null}
        </span>
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatQty(p.qty)}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-body)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
        {p.side}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatMoney(p.current_price)}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
        <span
          className={cn(
            "-mx-0.5 inline-flex w-fit rounded px-0.5",
            valueFlash === "up" && "tick-flash-up",
            valueFlash === "down" && "tick-flash-down",
          )}
        >
          {formatMoney(p.market_value)}
        </span>
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

function PaperPositions({
  linked,
  publish,
}: {
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  const { snapshot, loading, error, refresh } = useSnapshot();
  const { selectedAccountId } = useDashboardPrefs();
  const positions = resolvePositions(snapshot, selectedAccountId);
  const [sortState, setSortState] = useState<{
    key: SortKey;
    dir: "asc" | "desc";
  } | null>(null);

  const symbols = useMemo(
    () => Array.from(new Set(positions.map((p) => p.symbol))),
    [positions],
  );
  const marks = useLiveMarks(symbols);

  const markedPositions = useMemo(
    () => positions.map((p) => markPaper(p, marks)),
    [positions, marks],
  );

  const sortedPositions = useMemo(
    () => sortPositions(markedPositions, sortState),
    [markedPositions, sortState],
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
    return <WidgetLoading cols={[3, 2, 2, 2]} />;
  }

  if (error && !snapshot) {
    return <WidgetError message={error} onRetry={refresh} />;
  }

  if (!snapshot) {
    return <WidgetEmpty message="Waiting for positions…" />;
  }

  if (positions.length === 0) {
    return <WidgetEmpty message="No open positions" />;
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1 scroll-shadow-y">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            {COLUMNS.map(({ label, key, align }) => (
              <th
                key={key}
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase",
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

const REAL_COLUMNS: { label: string; align: "left" | "right" }[] = [
  { label: "Symbol", align: "left" },
  { label: "Qty", align: "right" },
  { label: "Price", align: "right" },
  { label: "Unrealized", align: "right" },
  { label: "Day P/L", align: "right" },
];

type RealRow = {
  symbol: string;
  qty: number;
  current_price: number;
  unrealized_pl: number;
  day_pl: number;
  accountLast4?: string;
};

function realRows(
  accounts: SchwabAccount[],
  selectedAccountId: string,
): RealRow[] {
  const scoped =
    selectedAccountId === "all_real"
      ? accounts
      : accounts.filter((a) => a.id === selectedAccountId);
  const multi = scoped.length > 1;
  return scoped.flatMap((a) =>
    (a.positions ?? []).map((p) => ({
      symbol: p.symbol,
      qty: p.qty,
      current_price: p.current_price,
      unrealized_pl: p.unrealized_pl,
      day_pl: p.day_pl,
      accountLast4: multi ? a.last4 : undefined,
    })),
  );
}

function markReal(p: RealRow, marks: Record<string, number>): RealRow {
  const px = marks[p.symbol];
  if (px == null || px === p.current_price) return p;
  const delta = p.qty * (px - p.current_price);
  return {
    ...p,
    current_price: px,
    unrealized_pl: p.unrealized_pl + delta,
    day_pl: p.day_pl + delta,
  };
}

function RealPositionRow({
  p,
  idx,
  linked,
  publish,
}: {
  p: RealRow;
  idx: number;
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  const unrealizedFlash = usePriceFlash(p.unrealized_pl);
  const dayFlash = usePriceFlash(p.day_pl);

  return (
    <tr
      key={`${p.accountLast4 ?? ""}-${p.symbol}-${idx}`}
      className="group/row transition-colors hover:bg-[var(--bg-hover)]"
    >
      <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ClickableSymbol
            symbol={p.symbol}
            linked={linked}
            onActivate={() => publish(p.symbol)}
          />
          {p.accountLast4 ? (
            <span className="rounded border border-[var(--border)] px-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              ••{p.accountLast4}
            </span>
          ) : null}
        </span>
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatQty(p.qty)}
      </td>
      <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
        {formatMoney(p.current_price)}
      </td>
      <td
        className={cn(
          "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums",
          p.unrealized_pl >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
        )}
      >
        <span
          className={cn(
            "-mx-0.5 inline-flex w-fit rounded px-0.5",
            unrealizedFlash === "up" && "tick-flash-up",
            unrealizedFlash === "down" && "tick-flash-down",
          )}
        >
          {p.unrealized_pl >= 0 ? "+" : ""}
          {formatMoney(p.unrealized_pl)}
        </span>
      </td>
      <td
        className={cn(
          "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums",
          p.day_pl >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
        )}
      >
        <span
          className={cn(
            "-mx-0.5 inline-flex w-fit rounded px-0.5",
            dayFlash === "up" && "tick-flash-up",
            dayFlash === "down" && "tick-flash-down",
          )}
        >
          {p.day_pl >= 0 ? "+" : ""}
          {formatMoney(p.day_pl)}
        </span>
      </td>
    </tr>
  );
}

function RealPositions({
  linked,
  publish,
}: {
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  const { selectedAccountId } = useDashboardPrefs();
  const { realAccounts, realAuthenticated, realLoading } = useRealAccounts();

  const baseRows = useMemo(
    () => realRows(realAccounts, selectedAccountId),
    [realAccounts, selectedAccountId],
  );

  const symbols = useMemo(
    () => Array.from(new Set(baseRows.map((p) => p.symbol))),
    [baseRows],
  );
  const marks = useLiveMarks(symbols);

  const rows = useMemo(
    () => baseRows.map((p) => markReal(p, marks)),
    [baseRows, marks],
  );

  if (realLoading && realAccounts.length === 0) {
    return <WidgetLoading cols={[3, 2, 2, 2]} />;
  }

  if (!realAuthenticated) {
    return <WidgetEmpty message="Schwab not connected" />;
  }

  if (rows.length === 0) {
    return <WidgetEmpty message="No open positions" />;
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1 scroll-shadow-y">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            {REAL_COLUMNS.map(({ label, align }) => (
              <th
                key={label}
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase",
                  align === "left" ? "text-left" : "text-right",
                )}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((p, idx) => (
            <RealPositionRow
              key={`${p.accountLast4 ?? ""}-${p.symbol}-${idx}`}
              p={p}
              idx={idx}
              linked={linked}
              publish={publish}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PositionsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { selectedAccountId } = useDashboardPrefs();

  // Toolbar scope is the ONLY switch — no in-widget chrome. Real scopes read
  // Schwab; overview and paper scopes keep the original snapshot behavior.
  return isRealScope(selectedAccountId) ? (
    <RealPositions linked={linked} publish={publish} />
  ) : (
    <PaperPositions linked={linked} publish={publish} />
  );
}
