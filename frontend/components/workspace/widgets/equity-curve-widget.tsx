"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  YAxis,
} from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import {
  WidgetEmpty,
  WidgetError,
} from "@/components/workspace/widget-states";
import {
  fetchPortfolioHistory,
  fetchSchwabEquityCurve,
  formatMoney,
  type PnlRange,
  type PortfolioHistoryPoint,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  isRealScope,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";
import { usePagePoll } from "@/lib/workspace/use-page-poll";

const PAPER_RANGES: PnlRange[] = ["1D", "1W", "1M", "1Y"];
const REAL_RANGES = ["1W", "1M"] as const;
type RealRange = (typeof REAL_RANGES)[number];

const POLL_MS = 20_000;
const gradientId = "workspace-equity-fill";

type ChartPoint = {
  equity: number;
  timestamp: string | number;
};

function MoneyTypeBadge({ kind }: { kind: "real" | "paper" }) {
  if (kind === "real") {
    return (
      <span className="inline-flex rounded-full border border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] bg-[var(--bg-inset)] px-2 py-px font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-amber)] uppercase">
        Real
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full border border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[var(--bg-inset)] px-2 py-px font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-blue)] uppercase">
      Paper
    </span>
  );
}

function EquityTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ChartPoint }>;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as ChartPoint | undefined;
  if (!point) return null;

  const dateLabel =
    typeof point.timestamp === "string"
      ? point.timestamp
      : new Date(point.timestamp * 1000).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          year: "numeric",
        });

  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1.5 shadow-sm">
      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">{dateLabel}</p>
      <p className="font-mono text-[length:var(--fs-body)] font-semibold tabular-nums text-[var(--fg)]">
        {formatMoney(point.equity)}
      </p>
    </div>
  );
}

function resolveMoneyType(
  selectedAccountId: string,
  aggView: "real" | "paper",
): "real" | "paper" {
  if (selectedAccountId === "overview") return aggView;
  if (isRealScope(selectedAccountId)) return "real";
  return "paper";
}

export function EquityCurveWidget() {
  const { selectedAccountId } = useDashboardPrefs();
  const [aggView, setAggView] = useState<"real" | "paper">("real");
  const [paperRange, setPaperRange] = useState<PnlRange>("1M");
  const [realRange, setRealRange] = useState<RealRange>("1M");
  const [points, setPoints] = useState<ChartPoint[]>([]);
  const [currentValue, setCurrentValue] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [schwabConnected, setSchwabConnected] = useState(true);

  const isOverview = selectedAccountId === "overview";
  const moneyType = resolveMoneyType(selectedAccountId, aggView);
  const realDays = realRange === "1W" ? 7 : 30;

  const cancelledRef = useRef(false);

  const load = useCallback(
    async (isInitial: boolean) => {
      if (isInitial) setLoading(true);

      try {
        if (moneyType === "real") {
          const scope = selectedAccountId.startsWith("schwab_")
            ? selectedAccountId
            : undefined;
          const data = await fetchSchwabEquityCurve(scope, realDays);
          if (cancelledRef.current) return;

          setSchwabConnected(data.authenticated);
          if (!data.authenticated) {
            setPoints([]);
            setCurrentValue(null);
            setError(null);
            return;
          }

          const mapped = data.points.map((p) => ({
            equity: p.equity,
            timestamp: p.date,
          }));
          setPoints(mapped);
          setCurrentValue(
            data.current_liquidation ?? mapped.at(-1)?.equity ?? null,
          );
          setError(null);
          return;
        }

        const data = await fetchPortfolioHistory(paperRange);
        if (cancelledRef.current) return;

        const mapped: ChartPoint[] = data.points.map(
          (p: PortfolioHistoryPoint) => ({
            equity: p.equity,
            timestamp: p.timestamp,
          }),
        );
        setPoints(mapped);
        setCurrentValue(mapped.at(-1)?.equity ?? null);
        setSchwabConnected(true);
        setError(null);
      } catch (err) {
        if (cancelledRef.current) return;
        setError(err instanceof Error ? err.message : "Failed to load history");
        setPoints([]);
        setCurrentValue(null);
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    },
    [selectedAccountId, moneyType, paperRange, realDays],
  );

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  usePagePoll(load, POLL_MS);

  const chartData = useMemo(() => points, [points]);

  const yDomain = useMemo(() => {
    if (chartData.length < 2) return undefined;
    const values = chartData.map((d) => d.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = (max - min) * 0.08 || max * 0.01;
    return [min - pad, max + pad] as [number, number];
  }, [chartData]);

  const emptyMessage = useMemo(() => {
    if (moneyType === "real") {
      if (!schwabConnected) return "Schwab not connected";
      if (chartData.length === 0) return "No flat-day history yet";
      return null;
    }
    if (chartData.length < 2) {
      return `No portfolio history for ${paperRange}`;
    }
    return null;
  }, [moneyType, schwabConnected, chartData.length, paperRange]);

  const rangeButtonClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
      active
        ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
        : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
    );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--line-divider)] px-3 py-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {currentValue != null ? (
            <span className="truncate font-mono text-[length:var(--fs-title)] font-semibold tabular-nums text-[var(--fg)]">
              {formatMoney(currentValue)}
            </span>
          ) : null}
          <MoneyTypeBadge kind={moneyType} />
        </div>

        <div className="flex items-center gap-1">
          {isOverview ? (
            <>
              <button
                type="button"
                onClick={() => setAggView("real")}
                className={rangeButtonClass(aggView === "real")}
              >
                REAL
              </button>
              <button
                type="button"
                onClick={() => setAggView("paper")}
                className={rangeButtonClass(aggView === "paper")}
              >
                PAPER
              </button>
              <span className="mx-0.5 h-3 w-px bg-[var(--border)]" />
            </>
          ) : null}

          {moneyType === "real"
            ? REAL_RANGES.map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setRealRange(key)}
                  className={rangeButtonClass(realRange === key)}
                >
                  {key}
                </button>
              ))
            : PAPER_RANGES.map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPaperRange(key)}
                  className={rangeButtonClass(paperRange === key)}
                >
                  {key}
                </button>
              ))}
        </div>
      </div>

      <div className="relative min-h-0 flex-1 p-2">
        {error ? (
          <WidgetError message={error} className="p-2" />
        ) : null}
        {loading && chartData.length === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : emptyMessage ? (
          <WidgetEmpty message={emptyMessage} />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={chartData}
              margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="0%"
                    stopColor="var(--c-accent)"
                    stopOpacity={0.25}
                  />
                  <stop
                    offset="100%"
                    stopColor="var(--c-accent)"
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <YAxis hide domain={yDomain} />
              <Tooltip content={<EquityTooltip />} />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="var(--c-accent)"
                strokeWidth={2}
                fill={`url(#${gradientId})`}
                dot={{ r: 2, fill: "var(--c-accent)", strokeWidth: 0 }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
