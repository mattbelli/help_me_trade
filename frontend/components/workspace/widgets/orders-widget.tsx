"use client";

import { useEffect, useState } from "react";

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
import { formatMoney } from "@/lib/api";
import { label } from "@/lib/labels";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import {
  fetchOrderExecutions,
  fetchTradeEvents,
  formatTradeMoney,
  parseMoney,
  type OrderExecution,
  type TradeEvent,
} from "@/lib/trades-api";
import { cn } from "@/lib/utils";
import {
  resolveOpenOrders,
  useDashboardPrefs,
  type TaggedOrder,
} from "@/lib/workspace/use-dashboard-prefs";

type TabId = "filled" | "open" | "rejected";

const TABS: { id: TabId; label: string }[] = [
  { id: "filled", label: "Filled" },
  { id: "open", label: "Open" },
  { id: "rejected", label: "Rejected" },
];

type PublishProps = {
  publish: (symbol: string) => void;
  linked: boolean;
};

function statusClass(status: string): string {
  const s = status.toLowerCase();
  // partial before fill: "partially_filled" contains both.
  if (s.includes("partial")) return "text-[var(--c-amber)]";
  if (s.includes("fill")) return "text-[var(--c-green)]";
  if (s.includes("cancel") || s.includes("reject")) return "text-[var(--c-red)]";
  if (s.includes("new") || s.includes("accept") || s.includes("pending"))
    return "text-[var(--c-blue)]";
  return "text-[var(--fg-dim)]";
}

function sideClass(side: string | null | undefined): string {
  const s = (side ?? "").toLowerCase();
  if (s === "buy") return "text-[var(--c-green)]";
  if (s === "sell") return "text-[var(--c-red)]";
  return "text-[var(--fg-dim)]";
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// App-wide 12-hour ET clock (matches notifications/filings/scans widgets).
// Returns just the time-of-day, e.g. "2:48p"; "" when no fill timestamp.
function formatFillClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d
    .toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    })
    .replace(" AM", "a")
    .replace(" PM", "p");
}

function FilledTab({
  events,
  loading,
  error,
  publish,
  linked,
}: {
  events: TradeEvent[];
  loading: boolean;
  error: string | null;
} & PublishProps) {
  if (loading && events.length === 0) {
    return <WidgetLoading cols={[2, 1, 1, 2]} />;
  }
  if (error && events.length === 0) {
    return <WidgetError message={error} />;
  }
  if (events.length === 0) {
    return <WidgetEmpty message="No trade history" />;
  }

  const columns: DataTableColumn<TradeEvent>[] = [
    {
      key: "date",
      header: "Date",
      className: "whitespace-nowrap text-[var(--fg-dim)]",
      render: (e) => {
        const fillClock = formatFillClock(e.fill_time);
        return (
          <>
            {e.trade_date}
            {fillClock ? (
              <span className="ml-1.5 text-[var(--fg-faint)]">{fillClock}</span>
            ) : null}
          </>
        );
      },
    },
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium",
      render: (e) => {
        const sym = e.symbol;
        return sym ? (
          <ClickableSymbol
            symbol={sym}
            linked={linked}
            onActivate={() => publish(sym)}
          />
        ) : (
          "—"
        );
      },
    },
    {
      key: "side",
      header: "Side",
      absorb: true,
      className: (e) =>
        cn(
          "text-[length:var(--fs-body)] tracking-[0.08em] uppercase",
          sideClass(e.side),
        ),
      render: (e) => e.side ?? "—",
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (e) => e.quantity ?? "—",
    },
    {
      key: "price",
      header: "Price",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (e) => (e.price != null ? formatTradeMoney(e.price) : "—"),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      className: (e) =>
        cn(
          "tabular-nums",
          parseMoney(e.amount) >= 0
            ? "text-[var(--c-green)]"
            : "text-[var(--c-red)]",
        ),
      render: (e) => (
        <>
          {parseMoney(e.amount) >= 0 ? "+" : ""}
          {formatTradeMoney(e.amount)}
        </>
      ),
    },
  ];

  return (
    <div className="px-1.5 py-1">
      <DataTable columns={columns} rows={events} rowKey={(e) => e.id} />
    </div>
  );
}

function OpenTab({
  orders,
  snapshotLoading,
  snapshotError,
  hasSnapshot,
  onRetry,
  publish,
  linked,
}: {
  orders: TaggedOrder[];
  snapshotLoading: boolean;
  snapshotError: string | null;
  hasSnapshot: boolean;
  onRetry: () => void;
} & PublishProps) {
  if (snapshotLoading && !hasSnapshot) {
    return <WidgetLoading cols={[2, 2, 3, 1, 1, 1, 2, 2]} />;
  }
  if (snapshotError && !hasSnapshot) {
    return <WidgetError message={snapshotError} onRetry={onRetry} />;
  }
  if (!hasSnapshot) {
    return <WidgetEmpty message="Waiting for orders…" />;
  }
  if (orders.length === 0) {
    return <WidgetEmpty message="No open orders" />;
  }

  const columns: DataTableColumn<TaggedOrder>[] = [
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium text-[var(--c-accent)]",
      render: (o) => (
        <ClickableSymbol
          symbol={o.symbol}
          linked={linked}
          onActivate={() => publish(o.symbol)}
        />
      ),
    },
    {
      key: "account",
      header: "Account",
      // nowrap: `side` absorbs the leftover width, so without this the label
      // wraps mid-word and the row goes back to two lines.
      className: "whitespace-nowrap",
      render: (o) => <Tag tone="dim">{o.accountLabel || "—"}</Tag>,
    },
    {
      key: "side",
      header: "Side",
      absorb: true,
      className: (o) =>
        cn(
          "text-[length:var(--fs-body)] tracking-[0.08em] uppercase",
          sideClass(o.side),
        ),
      render: (o) => o.side,
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (o) => o.qty,
    },
    {
      key: "filled_qty",
      header: "Filled",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (o) => o.filled_qty,
    },
    {
      key: "order_type",
      header: "Type",
      className: "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase",
      render: (o) => o.order_type,
    },
    {
      key: "status",
      header: "Status",
      className: (o) =>
        cn(
          "whitespace-nowrap text-[length:var(--fs-body)]",
          statusClass(o.status),
        ),
      render: (o) => label("order_status", o.status),
    },
    {
      key: "limit_price",
      header: "Limit",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (o) => (o.limit_price != null ? formatMoney(o.limit_price) : "—"),
    },
  ];

  return (
    <div className="px-1.5 py-1">
      <DataTable columns={columns} rows={orders} rowKey={(o) => o.id} />
    </div>
  );
}

function RejectedTab({
  executions,
  loading,
  error,
  publish,
  linked,
}: {
  executions: OrderExecution[];
  loading: boolean;
  error: string | null;
} & PublishProps) {
  if (loading && executions.length === 0) {
    return <WidgetLoading cols={[2, 1, 1, 2]} />;
  }
  if (error && executions.length === 0) {
    return <WidgetError message={error} />;
  }
  if (executions.length === 0) {
    return <WidgetEmpty message="No rejected orders" />;
  }

  const columns: DataTableColumn<OrderExecution>[] = [
    {
      key: "created_at",
      header: "Time",
      className: "whitespace-nowrap text-[var(--fg-dim)]",
      render: (x) => formatDateTime(x.created_at),
    },
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium",
      render: (x) => (
        <ClickableSymbol
          symbol={x.symbol}
          linked={linked}
          onActivate={() => publish(x.symbol)}
        />
      ),
    },
    {
      key: "side",
      header: "Side",
      absorb: true,
      className: (x) =>
        cn(
          "text-[length:var(--fs-body)] tracking-[0.08em] uppercase",
          sideClass(x.side),
        ),
      render: (x) => x.side,
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (x) => x.qty,
    },
    {
      key: "order_type",
      header: "Type",
      className: "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase",
      render: (x) => x.order_type,
    },
    {
      key: "execution_mode",
      header: "Mode",
      className: "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase",
      render: (x) => x.execution_mode,
    },
    {
      key: "status",
      header: "Status",
      className: (x) =>
        cn("text-[length:var(--fs-body)]", statusClass(x.status)),
      render: (x) => label("order_status", x.status),
    },
  ];

  return (
    <div className="px-1.5 py-1">
      <DataTable columns={columns} rows={executions} rowKey={(x) => x.id} />
    </div>
  );
}

export function OrdersWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const {
    snapshot,
    loading: snapshotLoading,
    error: snapshotError,
    refresh,
  } = useSnapshot();
  const { selectedAccountId } = useDashboardPrefs();

  const [tab, setTab] = useState<TabId>("filled");

  const [filledEvents, setFilledEvents] = useState<TradeEvent[]>([]);
  const [filledLoading, setFilledLoading] = useState(true);
  const [filledError, setFilledError] = useState<string | null>(null);

  const [rejectedExecutions, setRejectedExecutions] = useState<
    OrderExecution[]
  >([]);
  const [rejectedLoading, setRejectedLoading] = useState(false);
  const [rejectedError, setRejectedError] = useState<string | null>(null);
  const [rejectedFetchedFor, setRejectedFetchedFor] = useState<string | null>(
    null,
  );

  const openOrders = resolveOpenOrders(snapshot, selectedAccountId);

  // Filled — the star tab: fetch on mount. The backend doesn't (yet) filter by
  // event_type over this endpoint, so trim to actual trade fills client-side
  // (drops dividends/fees/etc rows).
  useEffect(() => {
    let cancelled = false;
    setFilledLoading(true);
    setFilledError(null);
    fetchTradeEvents({ account_id: selectedAccountId, limit: 200 })
      .then((events) => {
        if (cancelled) return;
        setFilledEvents(events.filter((e) => e.event_type === "trade"));
      })
      .catch((err) => {
        if (cancelled) return;
        setFilledError(
          err instanceof Error ? err.message : "Failed to load trades",
        );
      })
      .finally(() => {
        if (!cancelled) setFilledLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedAccountId]);

  // Rejected — lazy: only fetch once the tab is first shown.
  useEffect(() => {
    if (tab !== "rejected") return;
    const key = "__all__";
    if (rejectedFetchedFor === key) return;

    let cancelled = false;
    setRejectedLoading(true);
    setRejectedError(null);
    // this endpoint isn't resolver-wired; rejects are a paper audit list, so
    // keep showing all regardless of the workspace account selection.
    fetchOrderExecutions({
      status: "rejected",
      account_id: undefined,
      limit: 100,
    })
      .then((data) => {
        if (cancelled) return;
        setRejectedExecutions(data.executions);
        setRejectedFetchedFor(key);
      })
      .catch((err) => {
        if (cancelled) return;
        setRejectedError(
          err instanceof Error ? err.message : "Failed to load rejected orders",
        );
      })
      .finally(() => {
        if (!cancelled) setRejectedLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, rejectedFetchedFor]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--line-divider)] px-3 py-1.5">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              tab === id
                ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
        {tab === "filled" ? (
          <FilledTab
            events={filledEvents}
            loading={filledLoading}
            error={filledError}
            publish={publish}
            linked={linked}
          />
        ) : null}
        {tab === "open" ? (
          <OpenTab
            orders={openOrders}
            snapshotLoading={snapshotLoading}
            snapshotError={snapshotError}
            hasSnapshot={!!snapshot}
            onRetry={refresh}
            publish={publish}
            linked={linked}
          />
        ) : null}
        {tab === "rejected" ? (
          <RejectedTab
            executions={rejectedExecutions}
            loading={rejectedLoading}
            error={rejectedError}
            publish={publish}
            linked={linked}
          />
        ) : null}
      </div>
    </div>
  );
}
