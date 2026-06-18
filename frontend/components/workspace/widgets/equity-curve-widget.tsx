"use client";

import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchPortfolioHistory,
  type PnlRange,
  type PortfolioHistoryPoint,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const RANGES: PnlRange[] = ["1D", "1W", "1M", "1Y"];
const POLL_MS = 20_000;

const gradientId = "workspace-equity-fill";

export function EquityCurveWidget() {
  const [range, setRange] = useState<PnlRange>("1M");
  const [points, setPoints] = useState<PortfolioHistoryPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load(isInitial: boolean) {
      if (isInitial) setLoading(true);
      try {
        const data = await fetchPortfolioHistory(range);
        if (cancelled) return;
        setPoints(data.points);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load history");
        setPoints([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load(true);
    const timer = setInterval(() => void load(false), POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [range]);

  const chartData = useMemo(
    () =>
      points.map((p) => ({
        equity: p.equity,
        timestamp: p.timestamp,
      })),
    [points],
  );

  const yDomain = useMemo(() => {
    if (chartData.length < 2) return undefined;
    const values = chartData.map((d) => d.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = (max - min) * 0.08 || max * 0.01;
    return [min - pad, max + pad] as [number, number];
  }, [chartData]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--border)] px-3 py-1.5">
        {RANGES.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setRange(key)}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[10px] transition-colors",
              range === key
                ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {key}
          </button>
        ))}
      </div>

      <div className="relative min-h-0 flex-1 p-2">
        {error ? (
          <p className="p-2 font-mono text-[11px] text-[var(--c-red)]">{error}</p>
        ) : null}
        {loading && chartData.length === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : chartData.length < 2 ? (
          <div className="flex h-full min-h-24 items-center justify-center">
            <p className="font-mono text-xs text-[var(--fg-dim)]">
              No portfolio history for {range}
            </p>
          </div>
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
              <Area
                type="monotone"
                dataKey="equity"
                stroke="var(--c-accent)"
                strokeWidth={2}
                fill={`url(#${gradientId})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
