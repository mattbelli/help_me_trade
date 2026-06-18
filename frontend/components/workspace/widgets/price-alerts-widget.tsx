"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { fetchPriceAlerts, formatMoney, type PriceAlert } from "@/lib/api";
import { cn } from "@/lib/utils";

const POLL_MS = 20_000;

export function PriceAlertsWidget() {
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchPriceAlerts();
        if (cancelled) return;
        setAlerts(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load alerts");
        setAlerts([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const timer = setInterval(load, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (loading && alerts.length === 0) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  if (error) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--c-red)]">{error}</p>
    );
  }

  if (alerts.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No price alerts configured
      </p>
    );
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr>
            {(["Sym", "Price", "Dir", "Status", "Note"] as const).map(
              (h, idx) => (
                <th
                  key={h}
                  className={cn(
                    "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                    idx === 0 || h === "Dir" || h === "Status" || h === "Note"
                      ? "text-left"
                      : "text-right",
                  )}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <tr
              key={alert.id}
              className="transition-colors hover:bg-[var(--bg-hover)]"
            >
              <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                {alert.symbol}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
                {formatMoney(alert.price)}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] uppercase text-[var(--fg-dim)]">
                {alert.direction}
              </td>
              <td
                className={cn(
                  "border-b border-[var(--border)] px-2 py-1.5 text-[11px] uppercase",
                  alert.triggered
                    ? "text-[var(--c-green)]"
                    : "text-[var(--c-blue)]",
                )}
              >
                {alert.triggered ? "triggered" : "active"}
              </td>
              <td className="border-b border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--fg-faint)]">
                {alert.note || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
