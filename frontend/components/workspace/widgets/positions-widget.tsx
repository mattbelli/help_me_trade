"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/workspace/primitives/data-table";
import { Tag } from "@/components/workspace/primitives/tag";
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
  maskMoney,
  resolveAccountView,
  resolvePositions,
  useDashboardPrefs,
  type TaggedPosition,
} from "@/lib/workspace/use-dashboard-prefs";

type SortKey =
  | "symbol"
  | "account"
  | "qty"
  | "side"
  | "entered_at"
  | "avg_entry_price"
  | "current_price"
  | "market_value"
  | "unrealized_pl";

/** Tick-flash wrapper — the cell highlights for 500ms when its number moves. */
function FlashCell({
  value,
  children,
}: {
  value: number;
  children: ReactNode;
}) {
  const flash = usePriceFlash(value);
  return (
    <span
      className={cn(
        "-mx-0.5 inline-flex w-fit rounded px-0.5",
        flash === "up" && "tick-flash-up",
        flash === "down" && "tick-flash-down",
      )}
    >
      {children}
    </span>
  );
}

// One alignment rule: Symbol and Account anchor left, everything else is
// right-aligned.
function paperColumns(
  linked: boolean,
  publish: (symbol: string) => void,
): DataTableColumn<TaggedPosition, SortKey>[] {
  return [
    {
      key: "symbol",
      header: "Symbol",
      sortable: true,
      className: "font-medium text-foreground",
      render: (p) => (
        <ClickableSymbol
          symbol={p.symbol}
          linked={linked}
          onActivate={() => publish(p.symbol)}
        />
      ),
    },
    {
      key: "account",
      header: "Account",
      sortable: true,
      render: (p) => <Tag tone="dim">{p.accountLabel || "—"}</Tag>,
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      sortable: true,
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (p) => formatQty(p.qty),
    },
    {
      key: "side",
      header: "Side",
      align: "right",
      sortable: true,
      className:
        "text-[length:var(--fs-body)] tracking-[0.08em] text-[var(--fg-faint)] uppercase",
      render: (p) => p.side,
    },
    {
      key: "entered_at",
      header: "Entered",
      align: "right",
      sortable: true,
      className: "tabular-nums text-[var(--fg-faint)]",
      title: (p) => p.entered_at ?? undefined,
      render: (p) => formatEntered(p.entered_at),
    },
    {
      key: "avg_entry_price",
      header: "Entry",
      align: "right",
      sortable: true,
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (p) => formatMoney(p.avg_entry_price),
    },
    {
      key: "current_price",
      header: "Price",
      align: "right",
      sortable: true,
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (p) => formatMoney(p.current_price),
    },
    {
      key: "market_value",
      header: "Value",
      align: "right",
      sortable: true,
      className: "tabular-nums text-foreground",
      render: (p) => (
        <FlashCell value={p.market_value}>
          {formatMoney(p.market_value)}
        </FlashCell>
      ),
    },
    {
      key: "unrealized_pl",
      header: "Unrealized",
      align: "right",
      sortable: true,
      className: (p) =>
        cn(
          "tabular-nums",
          p.unrealized_pl >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
        ),
      render: (p) => (
        <>
          {p.unrealized_pl >= 0 ? "+" : ""}
          {formatMoney(p.unrealized_pl)}
        </>
      ),
    },
  ];
}

const ENTERED_DATE_FMT = new Intl.DateTimeFormat("en-US", {
  month: "2-digit",
  day: "2-digit",
  year: "numeric",
});
const ENTERED_TIME_FMT = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

// ISO timestamp → "07/16/2026 09:33" in the viewer's local time.
function formatEntered(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${ENTERED_DATE_FMT.format(d)} ${ENTERED_TIME_FMT.format(d)}`;
}

function sortPositions(
  rows: TaggedPosition[],
  sortState: { key: SortKey; dir: "asc" | "desc" } | null,
): TaggedPosition[] {
  if (!sortState) return rows;

  const { key, dir } = sortState;
  const mul = dir === "asc" ? 1 : -1;

  return [...rows].sort((a, b) => {
    if (key === "account") {
      return mul * (a.accountLabel ?? "").localeCompare(b.accountLabel ?? "");
    }

    const va = a[key];
    const vb = b[key];

    if (key === "symbol" || key === "side" || key === "entered_at") {
      return mul * String(va ?? "").localeCompare(String(vb ?? ""));
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
    return <WidgetLoading cols={[3, 2, 1, 1, 3, 2, 2, 2, 2]} />;
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
      <DataTable
        columns={paperColumns(linked, publish)}
        rows={sortedPositions}
        rowKey={(p, idx) => `${p.accountLabel ?? ""}-${p.symbol}-${idx}`}
        sort={sortState}
        onSortChange={handleSortHeaderClick}
      />
    </div>
  );
}

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

function realColumns(
  linked: boolean,
  publish: (symbol: string) => void,
): DataTableColumn<RealRow>[] {
  return [
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium text-foreground",
      render: (p) => (
        <ClickableSymbol
          symbol={p.symbol}
          linked={linked}
          onActivate={() => publish(p.symbol)}
        />
      ),
    },
    {
      key: "account",
      header: "Account",
      render: (p) => (
        <Tag tone="dim">{p.accountLast4 ? `••${p.accountLast4}` : "—"}</Tag>
      ),
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (p) => formatQty(p.qty),
    },
    {
      key: "current_price",
      header: "Price",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (p) => formatMoney(p.current_price),
    },
    {
      key: "unrealized_pl",
      header: "Unrealized",
      align: "right",
      className: (p) =>
        cn(
          "tabular-nums",
          p.unrealized_pl >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
        ),
      render: (p) => (
        <FlashCell value={p.unrealized_pl}>
          {p.unrealized_pl >= 0 ? "+" : ""}
          {formatMoney(p.unrealized_pl)}
        </FlashCell>
      ),
    },
    {
      key: "day_pl",
      header: "Day P/L",
      align: "right",
      className: (p) =>
        cn(
          "tabular-nums",
          p.day_pl >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
        ),
      render: (p) => (
        <FlashCell value={p.day_pl}>
          {p.day_pl >= 0 ? "+" : ""}
          {formatMoney(p.day_pl)}
        </FlashCell>
      ),
    },
  ];
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
    return <WidgetLoading cols={[3, 2, 1, 2, 2, 2]} />;
  }

  if (!realAuthenticated) {
    return <WidgetEmpty message="Schwab not connected" />;
  }

  if (rows.length === 0) {
    return <WidgetEmpty message="No open positions" />;
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1 scroll-shadow-y">
      <DataTable
        columns={realColumns(linked, publish)}
        rows={rows}
        rowKey={(p, idx) => `${p.accountLast4 ?? ""}-${p.symbol}-${idx}`}
      />
    </div>
  );
}

/** One-line account summary above the table — absorbed the account-summary
 * widget (2026-08-09 consolidation). Follows the toolbar account scope and
 * the shared privacy mask; real (Schwab) and paper never blend. */
function SummaryStrip() {
  const { selectedAccountId, privacyMode } = useDashboardPrefs();
  const { snapshot } = useSnapshot();
  const { realAccounts, realSummary, realAuthenticated } = useRealAccounts();

  const money = (v: number) => maskMoney(v, privacyMode, formatMoney);
  const signed = (v: number) =>
    maskMoney(v, privacyMode, (x: number) => `${x >= 0 ? "+" : ""}${formatMoney(x)}`);

  let parts: { label: string; value: string; className?: string }[] | null = null;
  if (isRealScope(selectedAccountId)) {
    if (!realAuthenticated) return null;
    const acct =
      selectedAccountId === "all_real"
        ? null
        : realAccounts.find((a) => a.id === selectedAccountId);
    const portfolio = acct ? acct.liquidation_value : (realSummary.liquidation_value ?? 0);
    const cash = acct ? acct.cash : (realSummary.cash ?? 0);
    const unrealized = acct ? acct.unrealized_pl : realSummary.unrealized_pl;
    parts = [
      { label: "portfolio", value: money(portfolio) },
      { label: "cash", value: money(cash) },
    ];
    if (unrealized != null) {
      parts.push({
        label: "unrlzd",
        value: signed(unrealized),
        className: privacyMode
          ? undefined
          : unrealized >= 0
            ? "text-[var(--c-green)]"
            : "text-[var(--c-red)]",
      });
    }
  } else {
    if (!snapshot) return null;
    const view =
      selectedAccountId === "overview"
        ? snapshot.all_accounts_summary
        : resolveAccountView(snapshot, selectedAccountId);
    if (!view) return null;
    parts = [
      { label: "portfolio", value: money(view.portfolio_value) },
      { label: "bp", value: money(view.buying_power) },
      { label: "cash", value: money(view.cash) },
      {
        label: "unrlzd",
        value: signed(view.unrealized_pl),
        className: privacyMode
          ? undefined
          : view.unrealized_pl >= 0
            ? "text-[var(--c-green)]"
            : "text-[var(--c-red)]",
      },
    ];
  }

  return (
    <div className="flex shrink-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-[var(--line-divider)] px-2 py-1.5 font-mono text-[length:var(--fs-table)]">
      {parts.map((p) => (
        <span key={p.label} className="whitespace-nowrap">
          <span className="text-[var(--fg-faint)]">{p.label} </span>
          <span className={cn("tabular-nums text-[var(--fg-dim)]", p.className)}>
            {p.value}
          </span>
        </span>
      ))}
    </div>
  );
}

export function PositionsWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { selectedAccountId } = useDashboardPrefs();

  // Toolbar scope is the ONLY switch — no in-widget chrome. Real scopes read
  // Schwab; overview and paper scopes keep the original snapshot behavior.
  return (
    <div className="flex h-full flex-col">
      <SummaryStrip />
      <div className="min-h-0 flex-1">
        {isRealScope(selectedAccountId) ? (
          <RealPositions linked={linked} publish={publish} />
        ) : (
          <PaperPositions linked={linked} publish={publish} />
        )}
      </div>
    </div>
  );
}
