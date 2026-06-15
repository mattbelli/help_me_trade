"use client";

import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchPortfolioHistory,
  formatMoney,
  type PnlRange,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const RANGE_LABELS: Record<PnlRange, string> = {
  "1D": "1D",
  "3D": "3D",
  "1W": "1W",
  "1M": "1M",
  "1Y": "1Y",
  ALL: "All",
};

const chartConfig = {
  value: {
    label: "Portfolio",
    color: "var(--chart-accent)",
  },
} satisfies ChartConfig;

function formatAxisLabel(range: PnlRange, timestamp: number): string {
  const date = new Date(timestamp * 1000);
  if (range === "1D" || range === "3D") {
    return date.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
  }
  if (range === "1W") {
    return date.toLocaleDateString(undefined, {
      weekday: "short",
      hour: "numeric",
    });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

type Props = {
  portfolioValue: number;
  range: PnlRange;
  onRangeChange: (range: PnlRange) => void;
};

export function PnlChart({ portfolioValue, range, onRangeChange }: Props) {
  const [series, setSeries] = useState<
    { label: string; value: number; timestamp: number }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchPortfolioHistory(range)
      .then((data) => {
        if (cancelled) return;
        setSeries(
          data.points.map((p) => ({
            timestamp: p.timestamp,
            value: p.equity,
            label: formatAxisLabel(range, p.timestamp),
          })),
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load history");
        setSeries([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [range]);

  const endValue = series.at(-1)?.value ?? portfolioValue;
  const startValue = series[0]?.value ?? portfolioValue;
  const change = endValue - startValue;
  const changePct = startValue ? (change / startValue) * 100 : 0;
  const positive = change >= 0;

  const chartData = useMemo(() => series, [series]);

  return (
    <Card className="border-border/80">
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle className="text-base font-medium tracking-tight">
            Portfolio value
          </CardTitle>
          <CardDescription>
            Equity curve from your Alpaca paper account
          </CardDescription>
          <div className="mt-3 flex flex-wrap items-baseline gap-3">
            <span className="text-2xl font-semibold tabular-nums tracking-tight">
              {formatMoney(portfolioValue)}
            </span>
            {!loading && chartData.length > 1 && (
              <span
                className={`text-sm font-medium tabular-nums ${positive ? "text-pnl-positive" : "text-pnl-negative"}`}
              >
                {positive ? "+" : ""}
                {formatMoney(change)} ({changePct >= 0 ? "+" : ""}
                {changePct.toFixed(2)}%)
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-1 rounded-lg border border-border p-1">
          {(Object.keys(RANGE_LABELS) as PnlRange[]).map((key) => (
            <Button
              key={key}
              type="button"
              size="sm"
              variant={range === key ? "default" : "ghost"}
              className={cn(
                "h-7 min-w-9 px-2 text-xs font-medium",
                range !== key && "text-muted-foreground",
              )}
              onClick={() => onRangeChange(key)}
            >
              {RANGE_LABELS[key]}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {error && (
          <p className="mb-3 text-sm text-destructive">{error}</p>
        )}
        {loading ? (
          <Skeleton className="h-[260px] w-full rounded-lg" />
        ) : chartData.length === 0 ? (
          <div className="flex h-[260px] items-center justify-center text-sm text-muted-foreground">
            No portfolio history for this range yet
          </div>
        ) : (
          <ChartContainer config={chartConfig} className="h-[260px] w-full">
            <AreaChart
              data={chartData}
              margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="0%"
                    stopColor="var(--chart-accent)"
                    stopOpacity={0.22}
                  />
                  <stop
                    offset="100%"
                    stopColor="var(--chart-accent)"
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                minTickGap={32}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={72}
                domain={["auto", "auto"]}
                tickFormatter={(v) =>
                  `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`
                }
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value) => formatMoney(Number(value))}
                  />
                }
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="var(--chart-accent)"
                strokeWidth={2}
                fill="url(#pnlFill)"
                dot={false}
              />
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}
