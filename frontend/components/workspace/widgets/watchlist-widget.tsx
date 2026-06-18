"use client";

import { useEffect, useMemo, useState } from "react";

import { Sparkline } from "@/components/trading/sparkline";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchWatchlists, type WatchlistsState } from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function WatchlistWidget() {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchWatchlists();
        if (cancelled) return;
        setState(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load watchlists",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  const activeList = state?.lists.find((list) => list.id === state.active_id);
  const symbols = useMemo(
    () => activeList?.symbols ?? EMPTY_SYMBOLS,
    [activeList?.symbols],
  );

  const { quotes, loading: marketLoading } = useWatchlistMarket({
    symbols,
    enabled: symbols.length > 0,
  });

  if (loading && !state) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (error) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--c-red)]">{error}</p>
    );
  }

  if (!activeList || symbols.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No symbols in the active watchlist
      </p>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border)] px-3 py-1.5">
        <p className="font-mono text-[10px] text-[var(--fg-faint)]">
          {activeList.name}
          {marketLoading ? " · loading…" : null}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-1">
        <table className="w-full border-collapse font-mono text-[12px]">
          <thead>
            <tr>
              {(["Sym", "Last", "Δ%", "7d"] as const).map((h, idx) => (
                <th
                  key={h}
                  className={cn(
                    "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                    idx === 0 ? "text-left" : "text-right",
                    h === "7d" && "text-right",
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((symbol) => {
              const quote = quotes[symbol];
              const changePct = quote?.change_pct ?? 0;
              const positive = changePct >= 0;

              return (
                <tr
                  key={symbol}
                  className="transition-colors hover:bg-[var(--bg-hover)]"
                >
                  <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                    {symbol}
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
                    {quote ? formatPrice(quote.price) : "—"}
                  </td>
                  <td
                    className={cn(
                      "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums",
                      quote
                        ? positive
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : "text-[var(--fg-dim)]",
                    )}
                  >
                    {quote ? formatPct(changePct) : "—"}
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-right">
                    <div className="flex justify-end">
                      <Sparkline
                        values={quote?.sparkline ?? []}
                        width={48}
                        height={18}
                        className={
                          quote
                            ? positive
                              ? "text-[var(--c-green)]"
                              : "text-[var(--c-red)]"
                            : "text-[var(--fg-faint)]"
                        }
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
