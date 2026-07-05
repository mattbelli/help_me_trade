"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ReferenceLine,
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
  fetchPerformanceSectors,
  type PerformancePeriod,
  type SectorBreakdown,
} from "@/lib/performance-api";
import { formatTradeMoney, parseMoney } from "@/lib/trades-api";
import { cn } from "@/lib/utils";
import { useDashboardPrefs } from "@/lib/workspace/use-dashboard-prefs";

const PERIODS: { key: PerformancePeriod; label: string }[] = [
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
  { key: "all", label: "All" },
];

type Mode = "allocation" | "pnl";

// Below this many categories a radar polygon degenerates into a wedge/line,
// so allocation mode falls back to a horizontal bar chart instead.
const MIN_SECTORS_FOR_RADAR = 3;

type SectorRow = {
  sector: string;
  weightPct: number; // 0..1
  marketValue: number;
  realizedPnl: number;
  positionCount: number;
  symbols: string[];
};

type ChartRow = SectorRow & { allocationPct: number };

function buildRows(breakdown: SectorBreakdown | null): SectorRow[] {
  if (!breakdown) return [];

  const rows: SectorRow[] = breakdown.sectors.map((s) => ({
    sector: s.sector,
    weightPct: s.weight_pct,
    marketValue: parseMoney(s.market_value),
    realizedPnl: parseMoney(s.realized_pnl),
    positionCount: s.position_count,
    symbols: s.symbols,
  }));

  const uc = breakdown.unclassified;
  const ucMarketValue = parseMoney(uc.market_value);
  const ucRealized = parseMoney(uc.realized_pnl);
  if (ucMarketValue !== 0 || ucRealized !== 0) {
    // unclassified has no weight_pct from the API — derive it from market
    // value so the legend/radar stay consistent with the sector slices.
    const totalMarketValue =
      rows.reduce((sum, r) => sum + r.marketValue, 0) + ucMarketValue;
    rows.push({
      sector: "Unclassified",
      weightPct: totalMarketValue > 0 ? ucMarketValue / totalMarketValue : 0,
      marketValue: ucMarketValue,
      realizedPnl: ucRealized,
      positionCount: uc.symbols.length,
      symbols: uc.symbols,
    });
  }

  return rows;
}

function SectorTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ChartRow }>;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;

  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 shadow-sm">
      <p className="font-mono text-[length:var(--fs-table)] font-semibold text-foreground">
        {row.sector}
      </p>
      <p className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
        {(row.weightPct * 100).toFixed(1)}% · {row.positionCount} pos
      </p>
      <p
        className="font-mono text-[length:var(--fs-body)] font-semibold tabular-nums"
        style={{ color: row.realizedPnl >= 0 ? "var(--c-green)" : "var(--c-red)" }}
      >
        {row.realizedPnl >= 0 ? "+" : ""}
        {formatTradeMoney(row.realizedPnl)} realized
      </p>
    </div>
  );
}

export function SectorRadarWidget() {
  const { selectedAccountId } = useDashboardPrefs();
  const [mode, setMode] = useState<Mode>("allocation");
  const [period, setPeriod] = useState<PerformancePeriod>("all");
  const [breakdown, setBreakdown] = useState<SectorBreakdown | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const data = await fetchPerformanceSectors({
          account_id: selectedAccountId,
          period,
        });
        if (cancelled) return;
        setBreakdown(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load sector data",
        );
        setBreakdown(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [period, selectedAccountId]);

  const rows = useMemo(() => buildRows(breakdown), [breakdown]);

  const sortedRows = useMemo(() => {
    const arr = [...rows];
    if (mode === "allocation") {
      arr.sort((a, b) => b.weightPct - a.weightPct);
    } else {
      arr.sort((a, b) => Math.abs(b.realizedPnl) - Math.abs(a.realizedPnl));
    }
    return arr;
  }, [rows, mode]);

  const chartData: ChartRow[] = useMemo(
    () => sortedRows.map((r) => ({ ...r, allocationPct: r.weightPct * 100 })),
    [sortedRows],
  );

  const totalMarketValue = useMemo(
    () => rows.reduce((sum, r) => sum + r.marketValue, 0),
    [rows],
  );
  const totalRealized = useMemo(
    () => rows.reduce((sum, r) => sum + r.realizedPnl, 0),
    [rows],
  );

  const pillClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
      active
        ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
        : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
    );

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--border)] px-3 py-1.5">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
          {rows.length > 0 ? (
            <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
              {rows.length} sector{rows.length === 1 ? "" : "s"}
              {mode === "pnl" ? (
                <>
                  {" · "}
                  <span
                    style={{
                      color:
                        totalRealized >= 0 ? "var(--c-green)" : "var(--c-red)",
                    }}
                  >
                    {totalRealized >= 0 ? "+" : ""}
                    {formatTradeMoney(totalRealized)}
                  </span>
                </>
              ) : (
                ` · ${formatTradeMoney(totalMarketValue)}`
              )}
            </span>
          ) : null}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMode("allocation")}
            className={pillClass(mode === "allocation")}
          >
            ALLOCATION
          </button>
          <button
            type="button"
            onClick={() => setMode("pnl")}
            className={pillClass(mode === "pnl")}
          >
            P&amp;L
          </button>
          <span className="mx-0.5 h-3 w-px bg-[var(--border)]" />
          {PERIODS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setPeriod(key)}
              className={pillClass(period === key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 p-2">
        {error ? (
          <WidgetError message={error} className="p-2" />
        ) : null}
        {loading && rows.length === 0 ? (
          <Skeleton className="h-full min-h-24 rounded-md" />
        ) : rows.length === 0 ? (
          <WidgetEmpty message="No sector data — no open positions or trades in range" />
        ) : (
          <div className="flex h-full flex-col gap-2">
            <div className="min-h-0 flex-1">
              {mode === "allocation" && totalMarketValue <= 0 ? (
                <WidgetEmpty
                  message="No open positions — allocation unavailable"
                  hint="Switch to P&L for realized sector performance"
                />
              ) : (
              <ChartContainer config={{}} className="h-full w-full aspect-auto">
                {mode === "pnl" ? (
                  <BarChart
                    data={chartData}
                    layout="vertical"
                    margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
                  >
                    <XAxis
                      type="number"
                      tickFormatter={(v: number) => formatTradeMoney(v)}
                      tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                      stroke="var(--border)"
                    />
                    <YAxis
                      type="category"
                      dataKey="sector"
                      tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                      stroke="var(--border)"
                      width={88}
                    />
                    <ReferenceLine x={0} stroke="var(--border)" />
                    <Tooltip
                      content={<SectorTooltip />}
                      cursor={{ fill: "var(--bg-inset)" }}
                    />
                    <Bar
                      dataKey="realizedPnl"
                      isAnimationActive={false}
                      radius={[0, 4, 4, 0]}
                    >
                      {chartData.map((row) => (
                        <Cell
                          key={row.sector}
                          fill={
                            row.realizedPnl >= 0 ? "var(--c-green)" : "var(--c-red)"
                          }
                        />
                      ))}
                    </Bar>
                  </BarChart>
                ) : chartData.length < MIN_SECTORS_FOR_RADAR ? (
                  <BarChart
                    data={chartData}
                    layout="vertical"
                    margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
                  >
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                      tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                      stroke="var(--border)"
                    />
                    <YAxis
                      type="category"
                      dataKey="sector"
                      tick={{ fill: "var(--fg-faint)", fontSize: 10 }}
                      stroke="var(--border)"
                      width={88}
                    />
                    <Tooltip
                      content={<SectorTooltip />}
                      cursor={{ fill: "var(--bg-inset)" }}
                    />
                    <Bar
                      dataKey="allocationPct"
                      fill="var(--chart-accent)"
                      isAnimationActive={false}
                      radius={[0, 4, 4, 0]}
                    />
                  </BarChart>
                ) : (
                  <RadarChart
                    data={chartData}
                    outerRadius="72%"
                    margin={{ top: 8, right: 16, bottom: 8, left: 16 }}
                  >
                    <PolarGrid stroke="var(--border)" />
                    <PolarAngleAxis
                      dataKey="sector"
                      tick={{ fill: "var(--fg-faint)", fontSize: 9 }}
                    />
                    <Radar
                      name="Allocation"
                      dataKey="allocationPct"
                      stroke="var(--chart-accent)"
                      fill="var(--chart-accent)"
                      fillOpacity={0.35}
                      isAnimationActive={false}
                    />
                    <Tooltip content={<SectorTooltip />} />
                  </RadarChart>
                )}
              </ChartContainer>
              )}
            </div>

            <div className="max-h-[42%] shrink-0 space-y-1 overflow-y-auto border-t border-[var(--border)] pt-1 scroll-shadow-y">
              {sortedRows.map((row) => (
                <div
                  key={row.sector}
                  className="flex items-center justify-between gap-2 font-mono text-[length:var(--fs-table)]"
                >
                  <span className="min-w-0 flex-1 truncate text-[var(--fg-dim)]">
                    {row.sector}
                  </span>
                  <span className="tabular-nums text-[var(--fg-faint)]">
                    {(row.weightPct * 100).toFixed(1)}%
                  </span>
                  <span
                    className="w-16 text-right tabular-nums"
                    style={{
                      color: row.realizedPnl >= 0 ? "var(--c-green)" : "var(--c-red)",
                    }}
                  >
                    {row.realizedPnl >= 0 ? "+" : ""}
                    {formatTradeMoney(row.realizedPnl)}
                  </span>
                  <span className="w-6 text-right tabular-nums text-[var(--fg-faint)]">
                    {row.positionCount}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
