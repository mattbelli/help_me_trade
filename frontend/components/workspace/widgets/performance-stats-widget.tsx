"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import {
  WidgetEmpty,
  WidgetError,
} from "@/components/workspace/widget-states";
import {
  fetchPerformanceDashboard,
  type PerformanceDashboard,
  type PerformancePeriod,
} from "@/lib/performance-api";
import { formatTradeMoney, parseMoney } from "@/lib/trades-api";
import { cn } from "@/lib/utils";
import { useDashboardPrefs } from "@/lib/workspace/use-dashboard-prefs";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";

const PERIODS: { key: PerformancePeriod; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
  { key: "all", label: "All" },
];

function formatSignedMoney(value: number): string {
  const formatted = formatTradeMoney(Math.abs(value));
  if (value > 0) return `+${formatted}`;
  if (value < 0) return `-${formatted}`;
  return formatted;
}

function pnlClass(value: number): string {
  if (value > 0) return "text-[var(--c-green)]";
  if (value < 0) return "text-[var(--c-red)]";
  return "text-[var(--fg)]";
}

type StatProps = {
  label: string;
  value: string;
  sub?: string;
  valueClassName?: string;
};

function Stat({ label, value, sub, valueClassName }: StatProps) {
  return (
    <div className="min-w-[96px] flex-1 rounded-md border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-2">
      <p className="truncate font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 truncate font-mono text-[length:var(--fs-emph)] font-semibold tracking-tight tabular-nums",
          valueClassName,
        )}
      >
        {value}
      </p>
      {sub ? (
        <p className="mt-0.5 truncate font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
          {sub}
        </p>
      ) : null}
    </div>
  );
}

export function PerformanceStatsWidget() {
  const { realAccounts, realSummary, realAuthenticated, realLoading } =
    useRealAccounts();
  const { selectedAccountId } = useDashboardPrefs();
  const [period, setPeriod] = useState<PerformancePeriod>("month");
  const [dashboard, setDashboard] = useState<PerformanceDashboard | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPerformanceDashboard({ account_id: selectedAccountId, period })
      .then((data) => {
        if (cancelled) return;
        setDashboard(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load performance",
        );
        setDashboard(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period, selectedAccountId]);

  // Unrealized P/L is always "as of now" (not period-scoped). Per-account
  // scope shows that account's unrealized P&L; otherwise the real aggregate.
  const unrealizedPl = selectedAccountId.startsWith("schwab_")
    ? realAccounts.find((a) => a.id === selectedAccountId)?.unrealized_pl
    : realAuthenticated
      ? realSummary.unrealized_pl
      : undefined;

  const summary = dashboard?.summary;
  const realizedPl = dashboard ? parseMoney(dashboard.realized_pnl) : null;
  const winRate = dashboard?.stock_win_rate ?? null;
  const fees = summary ? parseMoney(summary.total_fees) : null;

  const periodButtonClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
      active
        ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
        : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
    );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1 border-b border-[var(--border)] px-3 py-1.5">
        {PERIODS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => setPeriod(key)}
            className={periodButtonClass(period === key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-2.5 scroll-shadow-y">
        {loading && !dashboard ? (
          <div className="flex flex-wrap gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-16 min-w-[96px] flex-1 rounded-md"
              />
            ))}
          </div>
        ) : error && !dashboard ? (
          <WidgetError message={error} className="p-1" />
        ) : !dashboard ? (
          <WidgetEmpty message="No performance data yet." className="p-1" />
        ) : (
          <div className="flex flex-wrap gap-2">
            <Stat
              label="Realized P&L"
              value={realizedPl != null ? formatSignedMoney(realizedPl) : "—"}
              valueClassName={
                realizedPl != null ? pnlClass(realizedPl) : undefined
              }
            />
            <Stat
              label="Unrealized P&L"
              value={
                unrealizedPl != null ? formatSignedMoney(unrealizedPl) : "—"
              }
              valueClassName={
                unrealizedPl != null ? pnlClass(unrealizedPl) : undefined
              }
              sub={
                !realLoading && !realAuthenticated
                  ? "Schwab not connected"
                  : undefined
              }
            />
            <Stat
              label="Win Rate"
              value={winRate != null ? `${(winRate * 100).toFixed(0)}%` : "—"}
              sub={summary ? `${summary.wins}W / ${summary.losses}L` : undefined}
            />
            <Stat
              label="Trades"
              value={summary ? String(summary.trade_count) : "—"}
            />
            <Stat
              label="Fees"
              value={
                fees != null
                  ? fees > 0
                    ? `-${formatTradeMoney(fees)}`
                    : formatTradeMoney(fees)
                  : "—"
              }
              valueClassName={fees != null && fees > 0 ? "text-[var(--c-red)]" : undefined}
            />
          </div>
        )}
      </div>
    </div>
  );
}
