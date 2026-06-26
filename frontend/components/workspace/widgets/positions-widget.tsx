"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  resolvePositions,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";

export function PositionsWidget() {
  const { snapshot, loading } = useSnapshot();
  const { selectedAccountId } = useDashboardPrefs();
  const positions = resolvePositions(snapshot, selectedAccountId);

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
            {(
              ["Symbol", "Qty", "Side", "Price", "Value", "Unrealized"] as const
            ).map((h, idx) => (
              <th
                key={h}
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                  idx === 0 ? "text-left" : "text-right",
                  h === "Side" && "text-left",
                )}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p, idx) => (
            <tr
              key={`${p.accountLabel ?? ""}-${p.symbol}-${idx}`}
              className="transition-colors hover:bg-[var(--bg-hover)]"
            >
              <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-foreground">
                <span className="inline-flex items-center gap-1.5">
                  {p.symbol}
                  {p.accountLabel ? (
                    <span className="rounded border border-[var(--border)] px-1 text-[9px] text-[var(--fg-faint)]">
                      {p.accountLabel}
                    </span>
                  ) : null}
                </span>
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {p.qty}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] tracking-wide text-[var(--fg-faint)] uppercase">
                {p.side}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-[var(--fg-dim)]">
                {formatMoney(p.current_price)}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
