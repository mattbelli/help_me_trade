"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Cell,
  ReferenceLine,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

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
import type { ChannelId } from "@/lib/workspace/channels";
import { useDashboardPrefs } from "@/lib/workspace/use-dashboard-prefs";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const PERIODS: { key: PerformancePeriod; label: string }[] = [
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
  { key: "all", label: "All" },
];

type ScatterPoint = {
  symbol: string;
  holdDays: number;
  pnl: number;
};

// Whole days between "YYYY-MM-DD" open/close dates, clamped to >= 0.
function holdDays(openDate: string, closeDate: string): number {
  const open = Date.parse(`${openDate}T00:00:00Z`);
  const close = Date.parse(`${closeDate}T00:00:00Z`);
  if (!Number.isFinite(open) || !Number.isFinite(close)) return 0;
  const days = Math.round((close - open) / 86_400_000);
  return Math.max(0, days);
}

function buildPoints(roundTrips: RoundTrip[]): ScatterPoint[] {
  return roundTrips.map((rt) => ({
    symbol: rt.symbol,
    holdDays: holdDays(rt.open_date, rt.close_date),
    pnl: parseMoney(rt.pnl),
  }));
}

function ScatterTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ScatterPoint }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;

  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 shadow-sm">
      <p className="font-mono text-[length:var(--fs-table)] font-semibold text-foreground">
        {point.symbol}
      </p>
      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
        {point.holdDays} day{point.holdDays === 1 ? "" : "s"} held
      </p>
      <p
        className="font-mono text-[length:var(--fs-body)] font-semibold tabular-nums"
        style={{ color: point.pnl >= 0 ? "var(--c-green)" : "var(--c-red)" }}
      >
        {point.pnl >= 0 ? "+" : ""}
        {point.pnl.toFixed(2)}
      </p>
    </div>
  );
}

export function PnlScatterWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
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

  const points = useMemo(() => buildPoints(roundTrips), [roundTrips]);

  const periodButtonClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
      active
        ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
        : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
    );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--border)] px-3 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            {points.length} trade{points.length === 1 ? "" : "s"}
          </span>
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
        {loading && points.length === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : points.length === 0 ? (
          <WidgetEmpty message="No closed trades in this period" />
        ) : (
          <ChartContainer config={{}} className="h-full w-full aspect-auto">
            <ScatterChart margin={{ top: 4, right: 8, left: 4, bottom: 16 }}>
              <XAxis
                dataKey="holdDays"
                type="number"
                name="Hold days"
                tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                stroke="var(--border)"
                label={{
                  value: "hold days",
                  position: "bottom",
                  offset: 0,
                  fontSize: 10,
                  fill: "var(--fg-faint)",
                }}
              />
              <YAxis
                dataKey="pnl"
                type="number"
                name="$ P&L"
                tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                stroke="var(--border)"
                width={56}
                label={{
                  value: "$ P&L",
                  angle: -90,
                  position: "insideLeft",
                  fontSize: 10,
                  fill: "var(--fg-faint)",
                }}
              />
              <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
              <Tooltip content={<ScatterTooltip />} cursor={{ strokeDasharray: "3 3" }} />
              <Scatter
                data={points}
                isAnimationActive={false}
                cursor={linked ? "pointer" : undefined}
                onClick={(data: { payload?: ScatterPoint }) => {
                  const sym = data?.payload?.symbol;
                  if (sym) publish(sym);
                }}
              >
                {points.map((point, idx) => (
                  <Cell
                    key={`${point.symbol}-${idx}`}
                    fill={point.pnl >= 0 ? "var(--c-green)" : "var(--c-red)"}
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ChartContainer>
        )}
      </div>
    </div>
  );
}
