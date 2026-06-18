"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney, type Order } from "@/lib/api";
import { cn } from "@/lib/utils";

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

export function OpenOrdersWidget() {
  const { snapshot, loading } = useSnapshot();
  const orders = snapshot?.open_orders ?? [];

  if (loading && !snapshot) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (!snapshot) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        Waiting for orders…
      </p>
    );
  }

  if (orders.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No open orders
      </p>
    );
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr>
            {(["ID", "Sym", "Side", "Qty", "Type", "Px", "Status"] as const).map(
              (h, idx) => (
                <th
                  key={h}
                  className={cn(
                    "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
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
              className="transition-colors hover:bg-[var(--bg-hover)]"
            >
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--fg-faint)]">
                #{order.id.slice(-5)}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                {order.symbol}
              </td>
              <td
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[11px] tracking-wide uppercase",
                  sideClass(order.side),
                )}
              >
                {order.side}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {formatQty(order)}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--fg-faint)] uppercase">
                {order.order_type}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {order.limit_price != null
                  ? formatMoney(order.limit_price)
                  : "—"}
              </td>
              <td
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[11px] uppercase",
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
