"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, Cell, Tooltip, XAxis, YAxis } from "recharts";

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

// Data-adaptive symmetric-around-zero step, rounded to a "nice" 1/2/5 * 10^n
// number so the 8 buckets (index -4..3, step below) stay readable regardless
// of whether this account trades in $10s or $1000s.
function niceStep(rough: number): number {
  if (!Number.isFinite(rough) || rough <= 0) return 50;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / magnitude;
  const niceNorm = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return niceNorm * magnitude;
}

function fmtEdge(v: number): string {
  return `$${Math.round(Math.abs(v)).toLocaleString()}`;
}

type Bucket = {
  index: number;
  label: string;
  count: number;
};

// 8 buckets: index -4 (<= -3*step, tail) .. index 3 (>= 3*step, tail).
// pnl === 0 lands in the [0, step) bucket (index 0), so "wins" (pnl >= 0)
// always color green and "losses" (pnl < 0) always color red.
function buildBuckets(pnls: number[]): { buckets: Bucket[]; step: number } {
  const maxAbs = pnls.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  const step = niceStep(maxAbs / 3 || 50);

  const buckets: Bucket[] = [];
  for (let index = -4; index <= 3; index++) {
    let label: string;
    if (index === -4) {
      label = `≤ -${fmtEdge(3 * step)}`;
    } else if (index === 3) {
      label = `≥ ${fmtEdge(3 * step)}`;
    } else if (index === -1) {
      label = `-${fmtEdge(step)} to $0`;
    } else if (index === 0) {
      label = `$0 to ${fmtEdge(step)}`;
    } else if (index < 0) {
      label = `-${fmtEdge((-index) * step)} to -${fmtEdge((-index - 1) * step)}`;
    } else {
      label = `${fmtEdge(index * step)} to ${fmtEdge((index + 1) * step)}`;
    }
    buckets.push({ index, label, count: 0 });
  }

  for (const pnl of pnls) {
    const raw = Math.floor(pnl / step);
    const clamped = Math.max(-4, Math.min(3, raw));
    const bucket = buckets.find((b) => b.index === clamped);
    if (bucket) bucket.count += 1;
  }

  return { buckets, step };
}

function DistTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: Bucket }>;
}) {
  if (!active || !payload?.length) return null;
  const bucket = payload[0]?.payload;
  if (!bucket) return null;

  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 shadow-sm">
      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">{bucket.label}</p>
      <p className="font-mono text-[length:var(--fs-body)] font-semibold tabular-nums text-foreground">
        {bucket.count} trade{bucket.count === 1 ? "" : "s"}
      </p>
    </div>
  );
}

export function OutcomeDistributionWidget() {
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

  const pnls = useMemo(
    () => roundTrips.map((rt) => parseMoney(rt.pnl)),
    [roundTrips],
  );
  const { buckets } = useMemo(() => buildBuckets(pnls), [pnls]);

  const wins = pnls.filter((p) => p >= 0).length;
  const losses = pnls.filter((p) => p < 0).length;
  const total = pnls.length;
  const winPct = total > 0 ? Math.round((wins / total) * 100) : null;

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
          {total > 0 ? (
            <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              <span style={{ color: "var(--c-green)" }}>{wins}W</span>
              {" / "}
              <span style={{ color: "var(--c-red)" }}>{losses}L</span>
              {winPct != null ? ` · ${winPct}%` : ""}
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
        {loading && total === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : total === 0 ? (
          <WidgetEmpty message="No closed trades in this period" />
        ) : (
          <ChartContainer config={{}} className="h-full w-full aspect-auto">
            <BarChart data={buckets} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <XAxis
                dataKey="label"
                tick={{ fill: "var(--fg-faint)", fontSize: 9 }}
                stroke="var(--border)"
                interval={0}
                angle={-35}
                textAnchor="end"
                height={48}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                stroke="var(--border)"
                width={28}
              />
              <Tooltip
                content={<DistTooltip />}
                cursor={{ fill: "var(--bg-inset)" }}
              />
              <Bar dataKey="count" isAnimationActive={false}>
                {buckets.map((bucket) => (
                  <Cell
                    key={bucket.index}
                    fill={bucket.index >= 0 ? "var(--c-green-bg)" : "var(--c-red)"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ChartContainer>
        )}
      </div>
    </div>
  );
}
