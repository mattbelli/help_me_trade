"use client";

import { useEffect, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { formatMoney } from "@/lib/api";
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

const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
const ROW = "group/row transition-colors hover:bg-[var(--bg-hover)]";

type PublishProps = {
  publish: (symbol: string) => void;
  linked: boolean;
};

function statusClass(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("fill")) return "text-[var(--c-green)]";
  if (s.includes("partial")) return "text-[var(--c-amber)]";
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

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Date</th>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left", "w-full")}>Side</th>
            <th className={cn(TH, "text-right")}>Qty</th>
            <th className={cn(TH, "text-right")}>Price</th>
            <th className={cn(TH, "text-right")}>Amount</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const amount = parseMoney(e.amount);
            const sym = e.symbol;
            const fillClock = formatFillClock(e.fill_time);
            return (
              <tr key={e.id} className={ROW}>
                <td
                  className={cn(TD, "whitespace-nowrap text-[var(--fg-dim)]")}
                >
                  {e.trade_date}
                  {fillClock ? (
                    <span className="ml-1.5 text-[var(--fg-faint)]">
                      {fillClock}
                    </span>
                  ) : null}
                </td>
                <td className={cn(TD, "font-medium")}>
                  {sym ? (
                    <ClickableSymbol
                      symbol={sym}
                      linked={linked}
                      onActivate={() => publish(sym)}
                    />
                  ) : (
                    "—"
                  )}
                </td>
                <td
                  className={cn(
                    TD,
                    "text-[length:var(--fs-body)] tracking-[0.08em] uppercase w-full",
                    sideClass(e.side),
                  )}
                >
                  {e.side ?? "—"}
                </td>
                <td
                  className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}
                >
                  {e.quantity ?? "—"}
                </td>
                <td
                  className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}
                >
                  {e.price != null ? formatTradeMoney(e.price) : "—"}
                </td>
                <td
                  className={cn(
                    TD,
                    "text-right tabular-nums",
                    amount >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
                  )}
                >
                  {amount >= 0 ? "+" : ""}
                  {formatTradeMoney(e.amount)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OpenTab({
  orders,
  snapshotLoading,
  hasSnapshot,
  publish,
  linked,
}: {
  orders: TaggedOrder[];
  snapshotLoading: boolean;
  hasSnapshot: boolean;
} & PublishProps) {
  if (snapshotLoading && !hasSnapshot) {
    return <WidgetLoading cols={[2, 1, 1, 2, 2]} />;
  }
  if (!hasSnapshot) {
    return <WidgetEmpty message="Waiting for orders…" />;
  }
  if (orders.length === 0) {
    return <WidgetEmpty message="No open orders" />;
  }

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left", "w-full")}>Side</th>
            <th className={cn(TH, "text-right")}>Qty</th>
            <th className={cn(TH, "text-right")}>Filled</th>
            <th className={cn(TH, "text-left")}>Type</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-right")}>Limit</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id} className={ROW}>
              <td className={cn(TD, "font-medium text-[var(--c-accent)]")}>
                <span className="inline-flex items-center gap-1.5">
                  <ClickableSymbol
                    symbol={o.symbol}
                    linked={linked}
                    onActivate={() => publish(o.symbol)}
                  />
                  {o.accountLabel ? (
                    <span className="rounded border border-[var(--border)] px-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      {o.accountLabel}
                    </span>
                  ) : null}
                </span>
              </td>
              <td
                className={cn(
                  TD,
                  "text-[length:var(--fs-body)] tracking-[0.08em] uppercase w-full",
                  sideClass(o.side),
                )}
              >
                {o.side}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {o.qty}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {o.filled_qty}
              </td>
              <td className={cn(TD, "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase")}>
                {o.order_type}
              </td>
              <td className={cn(TD, "text-[length:var(--fs-body)] uppercase", statusClass(o.status))}>
                {o.status.replace(/_/g, " ")}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {o.limit_price != null ? formatMoney(o.limit_price) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Time</th>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left", "w-full")}>Side</th>
            <th className={cn(TH, "text-right")}>Qty</th>
            <th className={cn(TH, "text-left")}>Type</th>
            <th className={cn(TH, "text-left")}>Mode</th>
            <th className={cn(TH, "text-left")}>Status</th>
          </tr>
        </thead>
        <tbody>
          {executions.map((x) => (
            <tr key={x.id} className={ROW}>
              <td className={cn(TD, "whitespace-nowrap text-[var(--fg-dim)]")}>
                {formatDateTime(x.created_at)}
              </td>
              <td className={cn(TD, "font-medium")}>
                <ClickableSymbol
                  symbol={x.symbol}
                  linked={linked}
                  onActivate={() => publish(x.symbol)}
                />
              </td>
              <td
                className={cn(
                  TD,
                  "text-[length:var(--fs-body)] tracking-[0.08em] uppercase w-full",
                  sideClass(x.side),
                )}
              >
                {x.side}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {x.qty}
              </td>
              <td className={cn(TD, "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase")}>
                {x.order_type}
              </td>
              <td className={cn(TD, "text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase")}>
                {x.execution_mode}
              </td>
              <td className={cn(TD, "text-[length:var(--fs-body)] uppercase", statusClass(x.status))}>
                {x.status.replace(/_/g, " ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OrdersWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { snapshot, loading: snapshotLoading } = useSnapshot();
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
            hasSnapshot={!!snapshot}
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
