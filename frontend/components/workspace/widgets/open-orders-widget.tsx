"use client";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { formatMoney, type Order } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";
import {
  resolveOpenOrders,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";

function statusClass(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("fill")) return "text-[var(--c-green)]";
  if (s.includes("partial")) return "text-[var(--c-amber)]";
  if (s.includes("cancel") || s.includes("reject")) return "text-[var(--c-red)]";
  if (s.includes("new") || s.includes("accept") || s.includes("pending"))
    return "text-[var(--c-blue)]";
  return "text-[var(--fg-dim)]";
}

function sideClass(side: string): string {
  const s = side.toLowerCase();
  if (s === "buy") return "text-[var(--c-green)]";
  if (s === "sell") return "text-[var(--c-red)]";
  return "text-[var(--fg-dim)]";
}

function formatQty(order: Order): string {
  if (order.filled_qty > 0 && order.filled_qty < order.qty) {
    return `${order.filled_qty}/${order.qty}`;
  }
  return String(order.qty);
}

export function OpenOrdersWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { snapshot, loading, error, refresh } = useSnapshot();
  const { selectedAccountId } = useDashboardPrefs();
  const orders = resolveOpenOrders(snapshot, selectedAccountId);

  if (loading && !snapshot) {
    return <WidgetLoading cols={[2, 1, 1, 2, 2]} />;
  }

  if (error && !snapshot) {
    return <WidgetError message={error} onRetry={refresh} />;
  }

  if (!snapshot) {
    return <WidgetEmpty message="Waiting for orders…" />;
  }

  if (orders.length === 0) {
    return <WidgetEmpty message="No open orders" />;
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1 scroll-shadow-y">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            {(["ID", "Sym", "Side", "Qty", "Type", "Px", "Status"] as const).map(
              (h, idx) => (
                <th
                  key={h}
                  className={cn(
                    "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase",
                    idx === 0 || h === "Sym" || h === "Side" || h === "Type"
                      ? "text-left"
                      : "text-right",
                    h === "Status" && "text-left",
                  )}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.id}
              className="group/row transition-colors hover:bg-[var(--bg-hover)]"
            >
              <td className="border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)]">
                #{order.id.slice(-5)}
              </td>
              <td className="border-b border-[var(--line-divider)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                <span className="inline-flex items-center gap-1.5">
                  <ClickableSymbol
                    symbol={order.symbol}
                    linked={linked}
                    onActivate={() => publish(order.symbol)}
                  />
                  {order.accountLabel ? (
                    <span className="rounded border border-[var(--border)] px-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      {order.accountLabel}
                    </span>
                  ) : null}
                </span>
              </td>
              <td
                className={cn(
                  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-body)] tracking-[0.08em] uppercase",
                  sideClass(order.side),
                )}
              >
                {order.side}
              </td>
              <td className="border-b border-[var(--line-divider)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {formatQty(order)}
              </td>
              <td className="border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)] uppercase">
                {order.order_type}
              </td>
              <td className="border-b border-[var(--line-divider)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {order.limit_price != null
                  ? formatMoney(order.limit_price)
                  : "—"}
              </td>
              <td
                className={cn(
                  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-body)] uppercase",
                  statusClass(order.status),
                )}
              >
                {order.status.replace(/_/g, " ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
