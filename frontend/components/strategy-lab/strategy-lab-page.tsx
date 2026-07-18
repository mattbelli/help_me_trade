"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, SlidersHorizontal } from "lucide-react";

import { PagesMenu } from "@/components/layout/pages-menu";
import { Button } from "@/components/ui/button";
import { TERMINAL_COLLAPSED_HEIGHT } from "@/lib/workspace/use-terminal-store";
import {
  fetchStrategyLabBenchmarks,
  fetchStrategyLabCatalog,
  fetchStrategyLabCohorts,
  fetchStrategyLabSleeve,
  type StrategyLabBenchmarks,
  type StrategyLabCohortCatalog,
  type StrategyLabPoint,
  type StrategyLabSleeve,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Evidence = "backtest" | "benchmark";
type LabSeries = {
  id: string;
  label: string;
  evidence: Evidence;
  generated: string;
  points: StrategyLabPoint[];
  color: string;
  sleeve?: StrategyLabSleeve;
};
type PeriodMode = "year" | "quarter";
type RangeMode = "all" | "10y" | "5y";

const SLEEVE_COLORS = ["#d97706", "#7c3aed", "#0891b2", "#be123c"];
const BENCHMARK_COLORS: Record<string, string> = {
  SPY: "#94a3b8",
  IWR: "#64748b",
  MDY: "#a1a1aa",
  IWM: "#71717a",
};

function dateMs(value: string): number {
  return Date.parse(`${value}T00:00:00Z`);
}

function money(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: 0,
  });
}

function pct(value: number | undefined, alreadyPercent = false): string {
  if (value == null) return "—";
  return `${(alreadyPercent ? value : value * 100).toFixed(1)}%`;
}

function filterRange(points: StrategyLabPoint[], range: RangeMode): StrategyLabPoint[] {
  if (range === "all" || points.length === 0) return points;
  const last = dateMs(points[points.length - 1][0]);
  const years = range === "10y" ? 10 : 5;
  const cutoff = last - years * 365.25 * 86_400_000;
  return points.filter(([date]) => dateMs(date) >= cutoff);
}

export function StrategyLabHeader({
  artifactTime,
  cohortTime,
  loading,
  onRefresh,
}: {
  artifactTime: string | null;
  cohortTime: string | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 sm:px-4">
      <div className="flex min-w-0 items-center gap-2 sm:gap-3">
        <PagesMenu />
        <div className="min-w-0">
          <h1 className="truncate font-mono text-[13px] font-semibold tracking-tight text-foreground">
            Strategy Lab
          </h1>
          <p className="truncate text-[10px] text-[var(--fg-dim)]">
            research backtests and forward journals · artifacts{" "}
            {artifactTime ?? "unavailable"} · cohorts {cohortTime ?? "not available"}
          </p>
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={onRefresh}
        disabled={loading}
        className="shrink-0 gap-1.5"
      >
        <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        <span className="hidden sm:inline">Refresh</span>
      </Button>
    </header>
  );
}

export function StrategyLabErrorBoundary({ message }: { message: string }) {
  return (
    <div className="border-y border-destructive/40 py-4 text-sm text-destructive" role="alert">
      {message}
    </div>
  );
}

export function StrategyLabEmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-y border-border/60 py-10 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

function PickerContents({
  series,
  selected,
  limit,
  limitMessage,
  onToggle,
}: {
  series: LabSeries[];
  selected: Set<string>;
  limit: number;
  limitMessage: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="space-y-4">
      {(["backtest", "benchmark"] as const).map((group) => (
        <fieldset key={group}>
          <legend className="mb-2 text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            {group === "backtest" ? "research sleeves" : "benchmarks"}
          </legend>
          <div className="space-y-1.5">
            {series
              .filter((item) => item.evidence === group)
              .map((item) => (
                <label key={item.id} className="flex cursor-pointer items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={selected.has(item.id)}
                    onChange={() => onToggle(item.id)}
                    className="size-3.5 accent-amber-600"
                  />
                  <span className="size-2" style={{ backgroundColor: item.color }} />
                  <span>{item.label}</span>
                  <span
                    className={group === "backtest" ? "text-amber-600" : "text-slate-500"}
                  >
                    {group}
                  </span>
                </label>
              ))}
          </div>
        </fieldset>
      ))}
      <p className="text-[11px] text-muted-foreground">
        {selected.size}/{limit} lines
        {limitMessage ? " · Deselect one to add." : ""}
      </p>
    </div>
  );
}

export function SeriesPicker(props: Parameters<typeof PickerContents>[0]) {
  return (
    <div className="hidden min-w-56 border-l border-border/60 pl-5 md:block">
      <PickerContents {...props} />
    </div>
  );
}

export function MobileStrategyLabControls({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <details className="group md:hidden">
      <summary className="flex cursor-pointer list-none items-center gap-2 border-b border-border py-2 text-xs">
        <SlidersHorizontal className="size-3.5" />
        series & chart controls
      </summary>
      <div className="fixed inset-x-0 bottom-0 z-50 max-h-[78vh] overflow-y-auto border-t border-border bg-background p-5 shadow-2xl">
        <div className="mb-4 text-xs font-medium">Strategy Lab controls</div>
        {children}
        <p className="mt-5 text-[11px] text-muted-foreground">
          Tap “series & chart controls” again to close.
        </p>
      </div>
    </details>
  );
}

export function StrategyLabToolbar({
  logScale,
  range,
  periodMode,
  quarterCount,
  onLogScale,
  onRange,
  onPeriodMode,
  onQuarterCount,
  onReset,
}: {
  logScale: boolean;
  range: RangeMode;
  periodMode: PeriodMode;
  quarterCount: number;
  onLogScale: (value: boolean) => void;
  onRange: (value: RangeMode) => void;
  onPeriodMode: (value: PeriodMode) => void;
  onQuarterCount: (value: number) => void;
  onReset: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-border/60 py-2 font-mono text-[11px]">
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={logScale} onChange={(e) => onLogScale(e.target.checked)} />
        log
      </label>
      <label>
        range{" "}
        <select
          value={range}
          onChange={(e) => onRange(e.target.value as RangeMode)}
          className="bg-transparent text-foreground outline-none"
        >
          <option value="all">all</option>
          <option value="10y">10y</option>
          <option value="5y">5y</option>
        </select>
      </label>
      <div className="flex gap-3">
        {(["year", "quarter"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => onPeriodMode(mode)}
            className={cn("text-muted-foreground", periodMode === mode && "text-foreground underline")}
          >
            {mode}
          </button>
        ))}
      </div>
      {periodMode === "quarter" && (
        <label>
          quarters{" "}
          <select
            value={quarterCount}
            onChange={(e) => onQuarterCount(Number(e.target.value))}
            className="bg-transparent text-foreground outline-none"
          >
            {[8, 12, 16, 20].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      )}
      <button type="button" onClick={onReset} className="text-muted-foreground underline">
        reset
      </button>
    </div>
  );
}

export function SourceHonestyRail({ series }: { series: LabSeries[] }) {
  return (
    <div className="divide-y divide-border/50 border-y border-border/60">
      {series.map((item) => {
        const points = item.points;
        const sample =
          item.evidence === "backtest"
            ? `${item.sleeve?.config.n_trades ?? "unknown"} trades · ${points[0]?.[0]} → ${points.at(-1)?.[0]}`
            : `${points.length} observations · ${points[0]?.[0]} → ${points.at(-1)?.[0]}`;
        return (
          <div key={item.id} className="py-2 text-[11px] leading-relaxed">
            <span className="font-medium">{item.label}</span>
            {" · "}
            <span className={item.evidence === "backtest" ? "text-amber-600" : "text-slate-500"}>
              {item.evidence}
            </span>
            {" · "}
            <span className="font-mono text-muted-foreground">{sample}</span>
            {item.sleeve?.caveats.map((caveat) => (
              <span key={caveat} className="block pl-3 text-muted-foreground">
                {caveat}
              </span>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function BacktestEquityChart({
  series,
  logScale,
  range,
}: {
  series: LabSeries[];
  logScale: boolean;
  range: RangeMode;
}) {
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const plotted = series.map((item) => ({ ...item, points: filterRange(item.points, range) }));
  const all = plotted.flatMap((item) => item.points);
  if (all.length === 0) return <StrategyLabEmptyState>No equity observations.</StrategyLabEmptyState>;
  const xs = all.map(([date]) => dateMs(date));
  const rawYs = all.map(([, value]) => value);
  const ys = rawYs.map((value) => (logScale ? Math.log(Math.max(value, 1)) : value));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const x = (date: string) => 58 + ((dateMs(date) - minX) / Math.max(maxX - minX, 1)) * 922;
  const y = (value: number) =>
    18 +
    (1 - ((logScale ? Math.log(Math.max(value, 1)) : value) - minY) / Math.max(maxY - minY, 1)) *
      282;
  const hovered = hoverTime == null
    ? []
    : plotted.flatMap((item) => {
        if (item.points.length === 0) return [];
        let low = 0;
        let high = item.points.length - 1;
        while (low < high) {
          const middle = Math.floor((low + high) / 2);
          if (dateMs(item.points[middle][0]) < hoverTime) low = middle + 1;
          else high = middle;
        }
        const before = Math.max(0, low - 1);
        const index =
          Math.abs(dateMs(item.points[before][0]) - hoverTime) <
          Math.abs(dateMs(item.points[low][0]) - hoverTime)
            ? before
            : low;
        return [{ item, point: item.points[index] }];
      });

  return (
    <figure>
      <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
        {plotted.map((item) => (
          <span key={item.id}>
            <span style={{ color: item.color }}>━━</span> {item.label} ·{" "}
            <span className={item.evidence === "backtest" ? "text-amber-600" : "text-slate-500"}>
              {item.evidence}
            </span>
          </span>
        ))}
      </div>
      <svg
        viewBox="0 0 1000 330"
        className="h-[290px] w-full touch-pan-y"
        role="img"
        aria-label="Historical simulation equity"
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const viewX = ((event.clientX - bounds.left) / bounds.width) * 1000;
          const ratio = Math.max(0, Math.min(1, (viewX - 58) / 922));
          setHoverTime(minX + ratio * (maxX - minX));
        }}
        onPointerLeave={() => setHoverTime(null)}
      >
        {[0, 1, 2, 3, 4].map((tick) => {
          const yy = 18 + tick * 70.5;
          const transformed = maxY - (tick / 4) * (maxY - minY);
          const value = logScale ? Math.exp(transformed) : transformed;
          return (
            <g key={tick}>
              <line x1="58" x2="980" y1={yy} y2={yy} stroke="currentColor" opacity=".12" />
              <text x="52" y={yy + 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
                {money(value)}
              </text>
            </g>
          );
        })}
        {plotted.map((item) => (
          <path
            key={item.id}
            d={item.points
              .map(([date, value], index) => `${index === 0 ? "M" : "L"}${x(date).toFixed(2)},${y(value).toFixed(2)}`)
              .join(" ")}
            fill="none"
            stroke={item.color}
            strokeWidth={item.evidence === "backtest" ? 1.7 : 1.25}
            vectorEffect="non-scaling-stroke"
          >
            <title>
              {item.label} · {item.evidence} · generated {item.generated} · historical simulation
            </title>
          </path>
        ))}
        {hoverTime != null && (
          <line
            x1={58 + ((hoverTime - minX) / Math.max(maxX - minX, 1)) * 922}
            x2={58 + ((hoverTime - minX) / Math.max(maxX - minX, 1)) * 922}
            y1="18"
            y2="300"
            stroke="currentColor"
            opacity=".35"
          />
        )}
        <text x="58" y="322" className="fill-muted-foreground text-[10px]">
          {new Date(minX).toISOString().slice(0, 10)}
        </text>
        <text x="980" y="322" textAnchor="end" className="fill-muted-foreground text-[10px]">
          {new Date(maxX).toISOString().slice(0, 10)}
        </text>
      </svg>
      {hovered.length > 0 && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border/50 pt-2 font-mono text-[10px]">
          {hovered.map(({ item, point }) => (
            <span key={item.id}>
              <span style={{ color: item.color }}>{item.label}</span> · {point[0]} ·{" "}
              {money(point[1])} · {item.evidence} · generated {item.generated} · historical
              simulation
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}

type PeriodReturn = {
  key: string;
  label: string;
  partial: boolean;
  start: string;
  end: string;
  value: number;
};

function periodReturns(points: StrategyLabPoint[], mode: PeriodMode): PeriodReturn[] {
  const groups = new Map<string, StrategyLabPoint[]>();
  for (const point of points) {
    const date = new Date(`${point[0]}T00:00:00Z`);
    const quarter = Math.floor(date.getUTCMonth() / 3) + 1;
    const key = mode === "year" ? `${date.getUTCFullYear()}` : `${date.getUTCFullYear()}-Q${quarter}`;
    const group = groups.get(key) ?? [];
    group.push(point);
    groups.set(key, group);
  }
  return [...groups].map(([key, group], index) => {
    const first = group[0];
    const last = group[group.length - 1];
    return {
      key,
      label: index === 0 ? `${key}*` : key,
      partial: index === 0,
      start: first[0],
      end: last[0],
      value: (last[1] / first[1] - 1) * 100,
    };
  });
}

export function PeriodReturnsChart({
  series,
  mode,
  quarterCount,
}: {
  series: LabSeries[];
  mode: PeriodMode;
  quarterCount: number;
}) {
  const bySeries = series.map((item) => ({
    ...item,
    returns: periodReturns(item.points, mode),
  }));
  const keys = Array.from(new Set(bySeries.flatMap((item) => item.returns.map((r) => r.key)))).sort();
  const visibleKeys = mode === "quarter" ? keys.slice(-quarterCount) : keys;
  const values = bySeries.flatMap((item) =>
    item.returns.filter((r) => visibleKeys.includes(r.key)).map((r) => r.value),
  );
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const width = 1000;
  const zero = 145;
  const band = (width - 65) / Math.max(visibleKeys.length, 1);
  const barWidth = Math.max(2, Math.min(13, (band - 3) / Math.max(series.length, 1)));
  return (
    <figure>
      <svg viewBox="0 0 1000 310" className="h-[270px] w-full" role="img" aria-label={`${mode} returns`}>
        <line x1="55" x2="990" y1={zero} y2={zero} stroke="currentColor" opacity=".5" />
        {visibleKeys.map((key, keyIndex) => (
          <g key={key}>
            {bySeries.map((item, seriesIndex) => {
              const value = item.returns.find((r) => r.key === key);
              if (!value) return null;
              const height = (Math.abs(value.value) / maxAbs) * 115;
              const xx = 58 + keyIndex * band + seriesIndex * barWidth;
              return (
                <rect
                  key={item.id}
                  x={xx}
                  y={value.value >= 0 ? zero - height : zero}
                  width={barWidth - 1}
                  height={height}
                  fill={item.color}
                  opacity=".85"
                >
                  <title>
                    {item.label} · {value.label} · {value.value.toFixed(2)}%
                    {value.partial ? ` · partial period · ${value.start} → ${value.end}` : ""}
                  </title>
                </rect>
              );
            })}
            <text
              x={58 + keyIndex * band}
              y="282"
              transform={`rotate(-45 ${58 + keyIndex * band} 282)`}
              className="fill-muted-foreground text-[9px]"
            >
              {keyIndex === 0 ? `${key}*` : key}
            </text>
          </g>
        ))}
        <text x="5" y={zero - 116} className="fill-muted-foreground text-[9px]">
          +{maxAbs.toFixed(0)}%
        </text>
        <text x="5" y={zero + 120} className="fill-muted-foreground text-[9px]">
          -{maxAbs.toFixed(0)}%
        </text>
      </svg>
      <figcaption className="text-[10px] text-muted-foreground">
        * first partial period; hover for exact start/end dates. Bars are derived from native
        artifact observations without forward-fill.
      </figcaption>
    </figure>
  );
}

function derivedMetrics(points: StrategyLabPoint[]) {
  if (points.length < 2) return { cagr: undefined, maxDrawdown: undefined };
  const years = (dateMs(points.at(-1)![0]) - dateMs(points[0][0])) / (365.25 * 86_400_000);
  const cagr = Math.pow(points.at(-1)![1] / points[0][1], 1 / Math.max(years, 1 / 365)) - 1;
  let peak = points[0][1];
  let maxDrawdown = 0;
  for (const [, value] of points) {
    peak = Math.max(peak, value);
    maxDrawdown = Math.max(maxDrawdown, (peak - value) / peak);
  }
  return { cagr, maxDrawdown };
}

function overlapExcess(sleeve: LabSeries, spy: LabSeries | undefined) {
  if (!spy || sleeve.points.length < 2) return null;
  const start = sleeve.points[0][0] > spy.points[0][0] ? sleeve.points[0][0] : spy.points[0][0];
  const end = sleeve.points.at(-1)![0] < spy.points.at(-1)![0] ? sleeve.points.at(-1)![0] : spy.points.at(-1)![0];
  const trim = (points: StrategyLabPoint[]) => points.filter(([date]) => date >= start && date <= end);
  const sleeveMetrics = derivedMetrics(trim(sleeve.points));
  const spyMetrics = derivedMetrics(trim(spy.points));
  if (sleeveMetrics.cagr == null || spyMetrics.cagr == null) return null;
  return { value: sleeveMetrics.cagr - spyMetrics.cagr, start, end };
}

function needsLevelsWarning(sleeve: StrategyLabSleeve): boolean {
  const provenance = JSON.stringify({
    config: sleeve.config,
    provenance: sleeve.provenance,
    caveats: sleeve.caveats,
  }).toLowerCase();
  return provenance.includes("linear-accrual") || provenance.includes("optimistic fill");
}

export function BacktestMetricsStrip({ series }: { series: LabSeries[] }) {
  const spy = series.find((item) => item.id === "benchmark:SPY");
  return (
    <div className="divide-y divide-border/60 border-y border-border/60">
      {series.map((item) => {
        const derived = derivedMetrics(item.points);
        const metrics = item.sleeve?.metrics;
        const excess = item.sleeve ? overlapExcess(item, spy) : null;
        return (
          <div key={item.id} className="py-4">
            <div className="mb-2 flex flex-wrap items-baseline gap-x-2">
              <h3 className="text-sm font-medium">{item.label}</h3>
              <span className={item.evidence === "backtest" ? "text-xs text-amber-600" : "text-xs text-slate-500"}>
                {item.evidence}
              </span>
            </div>
            {item.sleeve && needsLevelsWarning(item.sleeve) && (
              <p className="mb-3 max-w-5xl border-l-2 border-amber-500/60 pl-3 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
                Absolute levels are not calibrated. Linear-accrual marking suppresses volatility
                and drawdown, and signal-close fills are optimistic. Use these simulations to
                compare rankings, not to trust absolute Sharpe or drawdown.
              </p>
            )}
            <dl className="grid grid-cols-2 gap-x-5 gap-y-3 font-mono text-[11px] sm:grid-cols-4 lg:grid-cols-8">
              <Metric label="modeled CAGR" value={pct(metrics?.cagr ?? derived.cagr)} />
              <Metric label="modeled max drawdown" value={pct(metrics?.max_drawdown ?? derived.maxDrawdown)} />
              {item.sleeve && (
                <>
                  <Metric label="modeled Sharpe" value={metrics?.sharpe?.toFixed(2) ?? "—"} />
                  <Metric label="average invested" value={pct(metrics?.avg_invested)} />
                  <Metric label="trade count" value={String(item.sleeve.config.n_trades ?? "—")} />
                  <Metric label="historical per-trade mean" value={pct(metrics?.per_trade_mean_pct, true)} />
                  <Metric label="raw historical win rate" value={pct(metrics?.win_pct, true)} />
                  <Metric
                    label={excess ? `modeled excess vs SPY · overlap ${excess.start} → ${excess.end}` : "modeled excess vs SPY"}
                    value={excess ? pct(excess.value) : "—"}
                  />
                </>
              )}
            </dl>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="min-h-8 text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

export function CohortSection({
  catalog,
  error,
}: {
  catalog: StrategyLabCohortCatalog | null;
  error: string | null;
}) {
  return (
    <section className="border-t border-border pt-8">
      <div className="mb-5">
        <h2 className="text-lg font-semibold">Forward cohort scorecards</h2>
        <p className="mt-1 max-w-4xl text-sm text-muted-foreground">
          Observational forward journals. Membership and rank are pinned before outcomes.
          Scorecards are not brokerage equity and do not imply significance.
        </p>
      </div>
      {error ? (
        <StrategyLabErrorBoundary message={error} />
      ) : catalog?.capability === "not_built" ? (
        <StrategyLabEmptyState>
          <span className="block text-foreground">Forward cohort scorecards are not built.</span>
          The canonical scheduler scorecard store is not available. No cohort return,
          benchmark, or cumulative series is reconstructed from ledger rows.
        </StrategyLabEmptyState>
      ) : catalog == null ? (
        <div className="py-10 text-sm text-muted-foreground">Loading cohort capability…</div>
      ) : (
        <StrategyLabEmptyState>No cohort lanes are available.</StrategyLabEmptyState>
      )}
    </section>
  );
}

export function StrategyLabPage() {
  const [sleeves, setSleeves] = useState<StrategyLabSleeve[]>([]);
  const [benchmarks, setBenchmarks] = useState<StrategyLabBenchmarks | null>(null);
  const [cohorts, setCohorts] = useState<StrategyLabCohortCatalog | null>(null);
  const [artifactCapability, setArtifactCapability] = useState<string | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [cohortError, setCohortError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isPhone, setIsPhone] = useState(false);
  const [limitMessage, setLimitMessage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [logScale, setLogScale] = useState(true);
  const [range, setRange] = useState<RangeMode>("all");
  const [periodMode, setPeriodMode] = useState<PeriodMode>("year");
  const [quarterCount, setQuarterCount] = useState(12);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsPhone(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setArtifactError(null);
    setCohortError(null);
    const artifactWork = (async () => {
      const catalog = await fetchStrategyLabCatalog();
      setArtifactCapability(catalog.capability ?? "available");
      if (catalog.capability === "unavailable") {
        setSleeves([]);
        setBenchmarks(null);
        return;
      }
      const [fullSleeves, benchmarkData] = await Promise.all([
        Promise.all(catalog.sleeves.map((item) => fetchStrategyLabSleeve(item.id))),
        fetchStrategyLabBenchmarks(),
      ]);
      setSleeves(fullSleeves);
      setBenchmarks(benchmarkData);
    })().catch((error: unknown) => {
      setArtifactError(error instanceof Error ? error.message : "Strategy Lab artifacts failed.");
    });
    const cohortWork = fetchStrategyLabCohorts()
      .then(setCohorts)
      .catch((error: unknown) =>
        setCohortError(error instanceof Error ? error.message : "Cohort capability failed."),
      );
    await Promise.allSettled([artifactWork, cohortWork]);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const allSeries = useMemo<LabSeries[]>(() => {
    const sleeveSeries = sleeves.map((sleeve, index) => ({
      id: sleeve.id,
      label: sleeve.label,
      evidence: "backtest" as const,
      generated: sleeve.generated,
      points: sleeve.equity,
      color: SLEEVE_COLORS[index % SLEEVE_COLORS.length],
      sleeve,
    }));
    const benchmarkSeries = Object.entries(benchmarks?.series ?? {}).map(([id, points]) => ({
      id: `benchmark:${id}`,
      label: id,
      evidence: "benchmark" as const,
      generated: benchmarks?.generated ?? "unknown",
      points,
      color: BENCHMARK_COLORS[id] ?? "#737373",
    }));
    return [...sleeveSeries, ...benchmarkSeries];
  }, [sleeves, benchmarks]);

  useEffect(() => {
    if (allSeries.length === 0) return;
    setSelected((current) => {
      if (current.size > 0) {
        if (!isPhone || current.size <= 2) return current;
      }
      const next = new Set<string>();
      if (isPhone) {
        const sleeve = allSeries.find((item) => item.evidence === "backtest");
        const spy = allSeries.find((item) => item.id === "benchmark:SPY");
        if (sleeve) next.add(sleeve.id);
        if (spy) next.add(spy.id);
      } else {
        allSeries
          .filter(
            (item) =>
              item.evidence === "backtest" ||
              item.id === "benchmark:SPY" ||
              item.id === "benchmark:IWR",
          )
          .slice(0, 6)
          .forEach((item) => next.add(item.id));
      }
      return next;
    });
  }, [allSeries, isPhone]);

  const lineLimit = isPhone ? 2 : 6;
  const visible = allSeries.filter((item) => selected.has(item.id));
  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
        setLimitMessage(false);
      } else if (next.size >= lineLimit) {
        setLimitMessage(true);
      } else {
        next.add(id);
        setLimitMessage(false);
      }
      return next;
    });
  };
  const reset = () => {
    setLogScale(true);
    setRange("all");
    setPeriodMode("year");
    setQuarterCount(12);
    const next = new Set<string>();
    if (isPhone) {
      const sleeve = allSeries.find((item) => item.evidence === "backtest");
      if (sleeve) next.add(sleeve.id);
      if (allSeries.some((item) => item.id === "benchmark:SPY")) next.add("benchmark:SPY");
    } else {
      allSeries
        .filter((item) => item.evidence === "backtest" || ["benchmark:SPY", "benchmark:IWR"].includes(item.id))
        .forEach((item) => next.add(item.id));
    }
    setSelected(next);
    setLimitMessage(false);
  };

  const picker = (
    <PickerContents
      series={allSeries}
      selected={selected}
      limit={lineLimit}
      limitMessage={limitMessage}
      onToggle={toggle}
    />
  );
  const controls = (
    <StrategyLabToolbar
      logScale={logScale}
      range={range}
      periodMode={periodMode}
      quarterCount={quarterCount}
      onLogScale={setLogScale}
      onRange={setRange}
      onPeriodMode={setPeriodMode}
      onQuarterCount={setQuarterCount}
      onReset={reset}
    />
  );

  return (
    <div className="flex h-svh min-h-0 flex-col bg-background">
      <StrategyLabHeader
        artifactTime={benchmarks?.generated ?? sleeves[0]?.generated ?? null}
        cohortTime={cohorts?.generated_at ?? null}
        loading={loading}
        onRefresh={() => void load()}
      />
      <main className="min-h-0 flex-1 overflow-auto">
        <div
          className="mx-auto w-full max-w-[1920px] space-y-9 px-4 py-5 sm:px-6 sm:py-6"
          // Reserve the collapsed terminal strip's height so the last section
          // isn't hidden under the fixed viewport overlay (board does this).
          style={{ paddingBottom: `calc(1.5rem + ${TERMINAL_COLLAPSED_HEIGHT}px)` }}
        >
        <p className="border-l-2 border-amber-500/70 pl-3 text-sm leading-relaxed text-foreground/90">
          Historical simulations and observational journals are different evidence. No
          curve on this page establishes significance or expected return.
        </p>
        <section>
          <div className="mb-5">
            <h2 className="text-lg font-semibold">Research backtests</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Committed historical research artifacts · native dates · no forward-fill
            </p>
          </div>
          {artifactError ? (
            <StrategyLabErrorBoundary message={artifactError} />
          ) : artifactCapability === "unavailable" ? (
            <StrategyLabEmptyState>
              No committed Strategy Lab artifacts are available.
            </StrategyLabEmptyState>
          ) : allSeries.length === 0 ? (
            <div className="py-10 text-sm text-muted-foreground">Loading committed artifacts…</div>
          ) : (
            <>
              <MobileStrategyLabControls>
                <div className="mb-5">{picker}</div>
                {controls}
              </MobileStrategyLabControls>
              <div className="hidden md:block">{controls}</div>
              <div className="mt-5 grid gap-5 md:grid-cols-[minmax(0,1fr)_15rem]">
                <div className="min-w-0 space-y-7">
                  <SourceHonestyRail series={visible} />
                  <div>
                    <h3 className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Equity · historical simulation
                    </h3>
                    <BacktestEquityChart series={visible} logScale={logScale} range={range} />
                  </div>
                  <div className="border-t border-border/60 pt-6">
                    <h3 className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Calendar {periodMode} returns
                    </h3>
                    <PeriodReturnsChart series={visible} mode={periodMode} quarterCount={quarterCount} />
                  </div>
                </div>
                <SeriesPicker
                  series={allSeries}
                  selected={selected}
                  limit={lineLimit}
                  limitMessage={limitMessage}
                  onToggle={toggle}
                />
              </div>
              <div className="mt-7">
                <h3 className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Reported metrics
                </h3>
                <BacktestMetricsStrip series={visible} />
              </div>
            </>
          )}
        </section>
        <CohortSection catalog={cohorts} error={cohortError} />
        </div>
      </main>
    </div>
  );
}
