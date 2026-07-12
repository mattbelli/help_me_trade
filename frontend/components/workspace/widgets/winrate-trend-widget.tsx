"use client";

import { useEffect, useMemo, useState } from "react";
import { Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";

import { ChartContainer } from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import {
  WidgetEmpty,
  WidgetError,
} from "@/components/workspace/widget-states";
import {
  fetchPerformanceDashboard,
  type PerformancePeriod,
  type RoundTrip,
} from "@/lib/performance-api";
import { parseMoney } from "@/lib/trades-api";
import { cn } from "@/lib/utils";
import { useDashboardPrefs } from "@/lib/workspace/use-dashboard-prefs";

const PERIODS: { key: PerformancePeriod; label: string }[] = [
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
  { key: "all", label: "All" },
];

const MIN_TRADES_TO_CHART = 8;

type TrendPoint = {
  seq: number;
  winRate: number;
};

type Stats = {
  currentWinRate: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  expectancy: number | null;
};

function computeStats(pnls: number[]): Stats {
  if (pnls.length === 0) {
    return { currentWinRate: null, avgWin: null, avgLoss: null, expectancy: null };
  }
  const wins = pnls.filter((p) => p >= 0);
  const losses = pnls.filter((p) => p < 0);
  const avgWin = wins.length
    ? wins.reduce((s, v) => s + v, 0) / wins.length
    : null;
  const avgLoss = losses.length
    ? losses.reduce((s, v) => s + v, 0) / losses.length
    : null;
  const expectancy = pnls.reduce((s, v) => s + v, 0) / pnls.length;
  const currentWinRate = Math.round((wins.length / pnls.length) * 100);
  return { currentWinRate, avgWin, avgLoss, expectancy };
}

// Rolling win-rate over a trailing window, plotted against trade sequence
// index. Window scales with sample size: min(20, ceil(n/4)), clamped >= 5.
function buildTrend(pnls: number[]): TrendPoint[] {
  const n = pnls.length;
  if (n < MIN_TRADES_TO_CHART) return [];
  const window = Math.max(5, Math.min(20, Math.ceil(n / 4)));

  const points: TrendPoint[] = [];
  for (let i = window - 1; i < n; i++) {
    const slice = pnls.slice(i - window + 1, i + 1);
    const wins = slice.filter((p) => p >= 0).length;
    points.push({ seq: i + 1, winRate: (wins / window) * 100 });
  }
  return points;
}

function TrendTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: TrendPoint }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;

  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 shadow-sm">
      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
        trade #{point.seq}
      </p>
      <p className="font-mono text-[length:var(--fs-body)] font-semibold tabular-nums text-foreground">
        {point.winRate.toFixed(0)}% win rate
      </p>
    </div>
  );
}

function fmtMoney(v: number | null): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

export function WinrateTrendWidget() {
  const { selectedAccountId } = useDashboardPrefs();
  const [period, setPeriod] = useState<PerformancePeriod>("all");
  const [roundTrips, setRoundTrips] = useState<RoundTrip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const dashboard = await fetchPerformanceDashboard({
          account_id: selectedAccountId,
          period,
        });
        if (cancelled) return;
        setRoundTrips(dashboard.round_trips ?? []);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load trades");
        setRoundTrips([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [period, selectedAccountId]);

  const sortedPnls = useMemo(() => {
    const sorted = [...roundTrips].sort((a, b) =>
      a.close_date < b.close_date ? -1 : a.close_date > b.close_date ? 1 : 0,
    );
    return sorted.map((rt) => parseMoney(rt.pnl));
  }, [roundTrips]);

  const trend = useMemo(() => buildTrend(sortedPnls), [sortedPnls]);
  const stats = useMemo(() => computeStats(sortedPnls), [sortedPnls]);

  const periodButtonClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
      active
        ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
        : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
    );

  const needsMoreData = sortedPnls.length < MIN_TRADES_TO_CHART;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--line-divider)] px-3 py-1.5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
          {stats.currentWinRate != null ? (
            <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              win {stats.currentWinRate}% · avg win {fmtMoney(stats.avgWin)} · avg
              loss {fmtMoney(stats.avgLoss)} · exp {fmtMoney(stats.expectancy)}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
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
      </div>

      <div className="relative min-h-0 flex-1 p-2">
        {error ? (
          <WidgetError message={error} className="p-2" />
        ) : null}
        {loading && sortedPnls.length === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : needsMoreData ? (
          <WidgetEmpty
            message={`Need more closed trades to chart a trend (${sortedPnls.length}/${MIN_TRADES_TO_CHART})`}
          />
        ) : (
          <ChartContainer config={{}} className="h-full w-full aspect-auto">
            <LineChart data={trend} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <XAxis
                dataKey="seq"
                tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                stroke="var(--border)"
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(v) => `${v}%`}
                tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                stroke="var(--border)"
                width={36}
              />
              <Tooltip content={<TrendTooltip />} cursor={{ stroke: "var(--border)" }} />
              <Line
                type="monotone"
                dataKey="winRate"
                stroke="var(--chart-line)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ChartContainer>
        )}
      </div>
    </div>
  );
}
