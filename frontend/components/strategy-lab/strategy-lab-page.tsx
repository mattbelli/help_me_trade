"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, RefreshCw, SlidersHorizontal } from "lucide-react";

import { PagesMenu } from "@/components/layout/pages-menu";
import { Button } from "@/components/ui/button";
import { TERMINAL_COLLAPSED_HEIGHT } from "@/lib/workspace/use-terminal-store";
import {
  fetchStrategyLabBenchmarks,
  fetchStrategyLabCatalog,
  fetchStrategyLabCohorts,
  fetchStrategyLabPaper,
  fetchStrategyLabSleeve,
  type StrategyLabBenchmarks,
  type StrategyLabCohortCatalog,
  type StrategyLabPaperBook,
  type StrategyLabPaperCatalog,
  type StrategyLabPaperPosition,
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
type LabSection = "backtests" | "paper" | "journals";

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

function axisTicks(minValue: number, maxValue: number, logScale: boolean): number[] {
  if (!(maxValue > minValue)) return [minValue];
  if (logScale) {
    const ticks: number[] = [];
    const low = Math.floor(Math.log10(Math.max(minValue, 1)));
    const high = Math.ceil(Math.log10(maxValue));
    for (let exp = low; exp <= high; exp += 1) {
      for (const mantissa of [1, 2, 5]) {
        const value = mantissa * 10 ** exp;
        if (value >= minValue && value <= maxValue) ticks.push(value);
      }
    }
    let thinned = ticks;
    while (thinned.length > 7) thinned = thinned.filter((_, index) => index % 2 === 0);
    if (thinned.length >= 2) return thinned;
  }
  const span = maxValue - minValue;
  const rawStep = span / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => span / s <= 5.5) ?? 10 * magnitude;
  const ticks: number[] = [];
  for (let value = Math.ceil(minValue / step) * step; value <= maxValue + step / 1e6; value += step) {
    ticks.push(value);
  }
  return ticks.length >= 2 ? ticks : [minValue, maxValue];
}

function sampleLabel(item: LabSeries): string {
  if (item.evidence === "backtest") {
    return `${item.sleeve?.config.n_trades ?? "unknown"} trades`;
  }
  return `${item.points.length} observations`;
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
            research backtests, live paper, and forward journals · artifacts{" "}
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
    <div className="space-y-5">
      {(["backtest", "benchmark"] as const).map((group) => (
        <fieldset key={group}>
          <legend className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-faint)]">
            {group === "backtest" ? "Backtests" : "Benchmarks"}
          </legend>
          <div className="space-y-0.5">
            {series
              .filter((item) => item.evidence === group)
              .map((item) => (
                <label
                  key={item.id}
                  className={cn(
                    "group flex min-h-9 cursor-pointer items-center gap-2 border-l-2 px-2 text-[12px] transition-colors hover:bg-foreground/[0.04]",
                    selected.has(item.id)
                      ? "border-l-current bg-foreground/[0.035] text-foreground"
                      : "border-l-transparent text-muted-foreground",
                  )}
                  style={selected.has(item.id) ? { borderLeftColor: item.color } : undefined}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(item.id)}
                    onChange={() => onToggle(item.id)}
                    className="sr-only"
                  />
                  <span className="h-px w-3 shrink-0" style={{ backgroundColor: item.color }} />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  <span
                    className={cn(
                      "font-mono text-[9px]",
                      group === "backtest" ? "text-amber-600" : "text-slate-500",
                    )}
                  >
                    {group}
                  </span>
                </label>
              ))}
          </div>
        </fieldset>
      ))}
      <p className={cn("font-mono text-[10px] text-[var(--fg-faint)]", limitMessage && "text-amber-500")}>
        {selected.size}/{limit} lines
        {limitMessage ? " · Deselect one to add." : ""}
      </p>
    </div>
  );
}

export function SeriesPicker(
  props: Parameters<typeof PickerContents>[0] & {
    activeSection: LabSection;
    onNavigate: (section: LabSection) => void;
  },
) {
  const { activeSection, onNavigate, ...pickerProps } = props;
  return (
    <aside className="hidden h-full w-[244px] shrink-0 flex-col border-r border-[var(--line-divider)] bg-[var(--bg-raised)] md:flex">
      <nav className="border-b border-[var(--line-divider)] p-3" aria-label="Strategy Lab sections">
        {([
          ["backtests", "Research backtests"],
          ["paper", "Paper book"],
          ["journals", "Forward journals"],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => onNavigate(id)}
            className={cn(
              "flex h-9 w-full items-center border-l-2 px-3 text-left text-[13px] transition-colors",
              activeSection === id
                ? "border-l-[var(--c-accent)] bg-foreground/[0.045] text-foreground"
                : "border-l-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <PickerContents {...pickerProps} />
      </div>
      <p
        className="shrink-0 border-t border-[var(--line-divider)] p-3 text-[10px] leading-[1.45] text-[var(--fg-dim)]"
        style={{ paddingBottom: `calc(0.75rem + ${TERMINAL_COLLAPSED_HEIGHT}px)` }}
      >
        Historical simulations, live paper positions, and observational journals are different
        evidence. No curve on this page establishes significance or expected return.
      </p>
    </aside>
  );
}

export function MobileStrategyLabControls({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <details className="group md:hidden">
      <summary className="flex h-11 cursor-pointer list-none items-center gap-2 border-b border-border px-4 text-[13px]">
        <SlidersHorizontal className="size-3.5" />
        Navigate & select series
        <ChevronDown className="ml-auto size-3.5 transition-transform group-open:rotate-180" />
      </summary>
      <div className="fixed inset-x-0 bottom-0 z-50 max-h-[82vh] overflow-y-auto border-t border-border bg-background p-5 shadow-2xl">
        <div className="mb-5 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Lab navigation
        </div>
        {children}
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
    <div className="grid gap-2 xl:grid-cols-2">
      {series.map((item) => {
        const points = item.points;
        const sample = `${sampleLabel(item)} · ${points[0]?.[0]} → ${points.at(-1)?.[0]}`;
        const caveats = item.sleeve?.caveats ?? [];
        const warning = item.sleeve && needsLevelsWarning(item.sleeve);
        return (
          <div
            key={item.id}
            className={cn(
              "min-w-0 border border-border/60 bg-[var(--bg-raised)] px-3 py-2.5",
              item.sleeve && "border-amber-500/30",
            )}
          >
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
              <span className="truncate font-medium text-foreground">{item.label}</span>
              <span className={item.evidence === "backtest" ? "text-amber-500" : "text-slate-500"}>
                {item.evidence}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">{sample}</span>
            </div>
            {warning ? (
              <p className="mt-2 text-[11px] leading-snug text-amber-500">
                Absolute levels are not calibrated. Linear-accrual marking suppresses volatility
                and drawdown, and signal-close fills are optimistic. Use these simulations to
                compare rankings, not to trust absolute Sharpe or drawdown.
              </p>
            ) : null}
            {caveats.length > 0 ? (
              <div className="mt-2 border-t border-amber-500/20 pt-2 text-[11px] leading-snug text-muted-foreground">
                <p>{caveats[0]}</p>
                {caveats.length > 1 ? (
                  <details className="group/caveats mt-1">
                    <summary className="cursor-pointer list-none font-mono text-[10px] text-amber-500/80">
                      +{caveats.length - 1} caveat{caveats.length === 2 ? "" : "s"}
                    </summary>
                    <div className="mt-1.5 space-y-1">
                      {caveats.slice(1).map((caveat, index) => (
                        <p key={`${index}:${caveat}`}>{caveat}</p>
                      ))}
                    </div>
                  </details>
                ) : null}
              </div>
            ) : null}
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
  const x = (date: string) => 74 + ((dateMs(date) - minX) / Math.max(maxX - minX, 1)) * 1102;
  const y = (value: number) =>
    24 +
    (1 - ((logScale ? Math.log(Math.max(value, 1)) : value) - minY) / Math.max(maxY - minY, 1)) *
      398;
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
    <figure className="min-w-0">
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1.5 text-[12px]">
        {plotted.map((item) => (
          <span key={item.id} className="flex items-baseline gap-1.5">
            <span className="font-mono" style={{ color: item.color }}>━━</span>
            <span>{item.label}</span>
            <span className={item.evidence === "backtest" ? "text-amber-600" : "text-slate-500"}>
              {item.evidence}
            </span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {sampleLabel(item)}
            </span>
          </span>
        ))}
      </div>
      <svg
        viewBox="0 0 1200 470"
        className="h-[clamp(420px,58vh,680px)] w-full touch-pan-y"
        role="img"
        aria-label="Historical simulation equity"
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const viewX = ((event.clientX - bounds.left) / bounds.width) * 1200;
          const ratio = Math.max(0, Math.min(1, (viewX - 74) / 1102));
          setHoverTime(minX + ratio * (maxX - minX));
        }}
        onPointerLeave={() => setHoverTime(null)}
      >
        {axisTicks(
          logScale ? Math.exp(minY) : minY,
          logScale ? Math.exp(maxY) : maxY,
          logScale,
        ).map((value) => (
          <g key={value}>
            <line
              x1="74"
              x2="1176"
              y1={y(value)}
              y2={y(value)}
              stroke="currentColor"
              opacity=".1"
            />
            <text
              x="64"
              y={y(value) + 5}
              textAnchor="end"
              className="fill-muted-foreground text-[13px] tabular-nums"
            >
              {money(value)}
            </text>
          </g>
        ))}
        {plotted.map((item) => {
          const path = item.points
            .map(([date, value], index) => `${index === 0 ? "M" : "L"}${x(date).toFixed(2)},${y(value).toFixed(2)}`)
            .join(" ");
          const title = `${item.label} · ${item.evidence} · ${sampleLabel(item)} · generated ${item.generated} · historical simulation`;
          return (
            <g key={item.id}>
              <path
                d={path}
                fill="none"
                stroke="transparent"
                strokeWidth="16"
                vectorEffect="non-scaling-stroke"
              >
                <title>{title}</title>
              </path>
              <path
                d={path}
                fill="none"
                stroke={item.color}
                strokeWidth={item.evidence === "backtest" ? 2.4 : 1.8}
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              >
                <title>{title}</title>
              </path>
            </g>
          );
        })}
        {hoverTime != null && (
          <line
            x1={74 + ((hoverTime - minX) / Math.max(maxX - minX, 1)) * 1102}
            x2={74 + ((hoverTime - minX) / Math.max(maxX - minX, 1)) * 1102}
            y1="24"
            y2="422"
            stroke="currentColor"
            opacity=".35"
          />
        )}
        {(() => {
          const firstYear = new Date(minX).getUTCFullYear() + 1;
          const lastYear = new Date(maxX).getUTCFullYear();
          const step = Math.max(1, Math.ceil((lastYear - firstYear + 1) / 8));
          const years: number[] = [];
          for (let year = firstYear; year <= lastYear; year += step) years.push(year);
          return years.map((year) => {
            const xx = 74 + ((Date.UTC(year, 0, 1) - minX) / Math.max(maxX - minX, 1)) * 1102;
            return (
              <g key={year}>
                <line x1={xx} x2={xx} y1="24" y2="422" stroke="currentColor" opacity=".06" />
                <text
                  x={xx}
                  y="456"
                  textAnchor="middle"
                  className="fill-muted-foreground text-[13px] tabular-nums"
                >
                  {year}
                </text>
              </g>
            );
          });
        })()}
      </svg>
      {hovered.length > 0 && (
        <div className="flex min-h-8 flex-wrap gap-x-5 gap-y-1 border-t border-border/50 pt-2 font-mono text-[11px]">
          {hovered.map(({ item, point }) => (
            <span key={item.id}>
              <span style={{ color: item.color }}>{item.label}</span> · {point[0]} ·{" "}
              {money(point[1])} · {item.evidence} · {sampleLabel(item)} · generated{" "}
              {item.generated} · historical simulation
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
      <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
        {series.map((item) => (
          <span key={item.id}>
            <span style={{ color: item.color }}>■</span> {item.label} ·{" "}
            <span className={item.evidence === "backtest" ? "text-amber-600" : "text-slate-500"}>
              {item.evidence}
            </span>{" "}
            · <span className="font-mono text-muted-foreground">{sampleLabel(item)}</span>
          </span>
        ))}
      </div>
      <svg viewBox="0 0 1000 330" className="h-[320px] w-full" role="img" aria-label={`${mode} returns`}>
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
                    {item.label} · {item.evidence} · {sampleLabel(item)} · {value.label} ·{" "}
                    {value.value.toFixed(2)}%
                    {value.partial ? ` · partial period · ${value.start} → ${value.end}` : ""}
                  </title>
                </rect>
              );
            })}
            <text
              x={58 + keyIndex * band}
              y="290"
              transform={`rotate(-45 ${58 + keyIndex * band} 290)`}
              className="fill-muted-foreground text-[12px]"
            >
              {keyIndex === 0 ? `${key}*` : key}
            </text>
          </g>
        ))}
        <text x="5" y={zero - 116} className="fill-muted-foreground text-[12px]">
          +{maxAbs.toFixed(0)}%
        </text>
        <text x="5" y={zero + 120} className="fill-muted-foreground text-[12px]">
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
    <div className="grid gap-px border border-border/60 bg-border/60 xl:grid-cols-2">
      {series.map((item) => {
        const derived = derivedMetrics(item.points);
        const metrics = item.sleeve?.metrics;
        const excess = item.sleeve ? overlapExcess(item, spy) : null;
        return (
          <div key={item.id} className="bg-background p-4">
            <div className="mb-4 flex flex-wrap items-baseline gap-x-2">
              <h3 className="text-[14px] font-medium">{item.label}</h3>
              <span className={item.evidence === "backtest" ? "text-[11px] text-amber-600" : "text-[11px] text-slate-500"}>
                {item.evidence}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">
                {sampleLabel(item)}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-x-5 gap-y-5 font-mono sm:grid-cols-4">
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

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[9px] uppercase leading-tight tracking-[0.1em] text-[var(--fg-faint)]">
        {label}
      </dt>
      <dd className={cn("mt-1.5 text-[18px] leading-none tabular-nums", tone ?? "text-foreground")}>
        {value}
      </dd>
    </div>
  );
}

function paperPrice(value: string | number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function paperPct(value: number | null): string {
  if (value == null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function paperQty(value: string | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function paperUsd(value: number | null, signed = false): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
  return signed ? `${value < 0 ? "−" : "+"}$${abs}` : `$${abs}`;
}

function paperTone(value: number | null): string | undefined {
  if (value == null) return undefined;
  return value >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]";
}

type EnrichedPaperPosition = StrategyLabPaperPosition & {
  value: number | null;
  unrealizedUsd: number | null;
};

function enrichPaperPosition(position: StrategyLabPaperPosition): EnrichedPaperPosition {
  const qty = position.qty != null ? Number(position.qty) : Number.NaN;
  const entry = position.entry_price != null ? Number(position.entry_price) : Number.NaN;
  const value =
    Number.isFinite(qty) && position.mark != null ? qty * position.mark : null;
  const unrealizedUsd =
    Number.isFinite(qty) && position.mark != null && Number.isFinite(entry)
      ? qty * (position.mark - entry)
      : null;
  return { ...position, value, unrealizedUsd };
}

function PaperPositionsTable({
  bookId,
  positions,
}: {
  bookId: string;
  positions: EnrichedPaperPosition[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse font-mono text-[11px] tabular-nums">
        <thead className="text-[9px] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
          <tr className="h-5 border-b border-border/50">
            <th className="px-2 text-left font-normal">symbol</th>
            <th className="px-2 text-right font-normal">qty</th>
            <th className="px-2 text-right font-normal">entered</th>
            <th className="px-2 text-right font-normal">entry</th>
            <th className="px-2 text-right font-normal">mark</th>
            <th className="px-2 text-right font-normal">value</th>
            <th className="px-2 text-right font-normal">unrl $</th>
            <th className="px-2 text-right font-normal">unrl %</th>
            <th className="px-2 text-right font-normal">days</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr
              key={`${bookId}:${position.symbol}`}
              className="h-5 border-b border-border/35 leading-[20px] last:border-b-0"
            >
              <td className="px-2 text-left text-foreground">{position.symbol}</td>
              <td className="px-2 text-right text-[var(--fg-dim)]">{paperQty(position.qty)}</td>
              <td className="px-2 text-right text-[var(--fg-dim)]">
                {position.entry_date.slice(0, 10)}
              </td>
              <td className="px-2 text-right">{paperPrice(position.entry_price)}</td>
              <td className="px-2 text-right">{paperPrice(position.mark)}</td>
              <td className="px-2 text-right">{paperUsd(position.value)}</td>
              <td className={cn("px-2 text-right", paperTone(position.unrealizedUsd))}>
                {paperUsd(position.unrealizedUsd, true)}
              </td>
              <td className={cn("px-2 text-right", paperTone(position.unrealized_pct))}>
                {paperPct(position.unrealized_pct)}
              </td>
              <td className="px-2 text-right text-[var(--fg-dim)]">
                {position.days_held ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaperBookBlock({ book }: { book: StrategyLabPaperBook }) {
  const positions = book.positions.map(enrichPaperPosition);
  const marked = positions.filter((position) => position.unrealizedUsd != null);
  const totalValue = positions.reduce((sum, position) => sum + (position.value ?? 0), 0);
  const totalUnrealized = marked.reduce(
    (sum, position) => sum + (position.unrealizedUsd ?? 0),
    0,
  );
  const totalCost = marked.reduce(
    (sum, position) => sum + (position.value ?? 0) - (position.unrealizedUsd ?? 0),
    0,
  );
  const totalPct = marked.length > 0 && totalCost > 0 ? (totalUnrealized / totalCost) * 100 : null;
  const age = Math.max(...positions.map((position) => position.days_held ?? 0), 0);

  const collapsed = positions.length > 12;
  const movers = [...marked].sort(
    (a, b) => Math.abs(b.unrealizedUsd ?? 0) - Math.abs(a.unrealizedUsd ?? 0),
  );
  const preview = collapsed
    ? movers.length > 0
      ? movers.slice(0, 8)
      : [...positions].sort((a, b) => (b.value ?? 0) - (a.value ?? 0)).slice(0, 8)
    : positions;

  return (
    <div className="border-y border-border/60">
      <div className="flex min-h-9 flex-wrap items-baseline gap-x-2 border-b border-border/60 py-2">
        <h3 className="text-[13px] font-medium text-foreground">{book.label}</h3>
        <span className="font-mono text-[10px] lowercase text-[var(--c-cyan)]">paper</span>
        <span className="font-mono text-[10px] text-[var(--fg-dim)]">
          {age}d old · n={book.n_positions}
          {book.planned_exit_note ? ` · ${book.planned_exit_note}` : ""}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-b border-border/50 py-4 font-mono sm:grid-cols-4">
        <Metric label="market value" value={paperUsd(totalValue)} />
        <Metric
          label="unrealized $"
          value={marked.length > 0 ? paperUsd(totalUnrealized, true) : "—"}
          tone={marked.length > 0 ? paperTone(totalUnrealized) : undefined}
        />
        <Metric
          label="unrealized %"
          value={paperPct(totalPct)}
          tone={paperTone(totalPct)}
        />
        <Metric
          label="mean / position"
          value={paperPct(book.mean_unrealized_pct)}
          tone={paperTone(book.mean_unrealized_pct)}
        />
      </dl>
      {(() => {
        const brokerAvg = positions.filter((p) => p.entry_source === "broker_avg").length;
        const unmarked = positions.length - marked.length;
        if (brokerAvg === 0 && unmarked === 0) return null;
        return (
          <p className="border-b border-border/35 py-2 font-mono text-[10px] text-[var(--fg-dim)]">
            {brokerAvg > 0
              ? `${brokerAvg}/${positions.length} entries use broker avg cost (no strategy fill linked — blended if another strategy holds the same symbol)`
              : null}
            {brokerAvg > 0 && unmarked > 0 ? " · " : null}
            {unmarked > 0 ? `${unmarked} positions have no mark — excluded from totals` : null}
          </p>
        );
      })()}
      {collapsed ? (
        <>
          <p className="py-2 font-mono text-[10px] text-[var(--fg-dim)]">
            top {preview.length} of {positions.length} by{" "}
            {movers.length > 0 ? "unrealized $" : "market value"}
          </p>
          <PaperPositionsTable bookId={book.strategy_id} positions={preview} />
          <details className="group border-t border-border/35">
            <summary className="flex h-8 cursor-pointer list-none items-center font-mono text-[10px] text-[var(--fg-dim)] hover:text-foreground">
              <span className="group-open:hidden">show all {positions.length} positions</span>
              <span className="hidden group-open:inline">collapse</span>
            </summary>
            <PaperPositionsTable bookId={`${book.strategy_id}:all`} positions={positions} />
          </details>
        </>
      ) : (
        <PaperPositionsTable bookId={book.strategy_id} positions={positions} />
      )}
    </div>
  );
}

export function PaperBookSection({
  catalog,
  error,
}: {
  catalog: StrategyLabPaperCatalog | null;
  error: string | null;
}) {
  return (
    <section id="paper-book" className="scroll-mt-4 border-t border-border px-4 py-8 sm:px-6">
      <div className="mb-5">
        <p className="font-mono text-[10px] lowercase text-cyan-500">paper</p>
        <h2 className="mt-1 text-[16px] font-semibold">Live paper book</h2>
        <p className="mt-1 text-[11px] text-[var(--fg-dim)]">
          live paper positions · unrealized marks, not results · a few days of data establishes
          nothing
        </p>
      </div>
      {error ? (
        <StrategyLabErrorBoundary message={error} />
      ) : catalog == null ? (
        <div className="py-10 text-sm text-muted-foreground">Loading paper positions…</div>
      ) : catalog.books.length === 0 ? (
        <StrategyLabEmptyState>
          no strategies currently hold paper positions
        </StrategyLabEmptyState>
      ) : (
        <div className="space-y-6">
          {[...catalog.books]
            .sort((a, b) => {
              const coverage = (book: StrategyLabPaperBook) =>
                book.positions.filter((position) => position.unrealized_pct != null).length /
                Math.max(book.positions.length, 1);
              return coverage(b) - coverage(a);
            })
            .map((book) => (
              <PaperBookBlock key={book.strategy_id} book={book} />
            ))}
        </div>
      )}
    </section>
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
    <section id="forward-journals" className="scroll-mt-4 border-t border-border px-4 py-8 sm:px-6">
      <div className="mb-5">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--c-blue)]">
          live-tracked
        </p>
        <h2 className="mt-1 text-[16px] font-semibold">Forward cohort scorecards</h2>
        <p className="mt-1 max-w-4xl text-[13px] text-muted-foreground">
          Observational forward journals. Membership and rank are pinned before outcomes.
          Scorecards are not brokerage equity and do not imply significance.
        </p>
      </div>
      {error ? (
        <StrategyLabErrorBoundary message={error} />
      ) : catalog?.capability === "not_built" ? (
        <StrategyLabEmptyState>
          <span className="block text-[15px] text-foreground">
            Forward cohort scorecards are not built.
          </span>
          <span className="mt-1 block text-[12px]">
            The canonical scheduler scorecard store is not available. No cohort return,
            benchmark, or cumulative series is reconstructed from ledger rows.
          </span>
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
  const [paper, setPaper] = useState<StrategyLabPaperCatalog | null>(null);
  const [artifactCapability, setArtifactCapability] = useState<string | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [cohortError, setCohortError] = useState<string | null>(null);
  const [paperError, setPaperError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isPhone, setIsPhone] = useState(false);
  const [limitMessage, setLimitMessage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [logScale, setLogScale] = useState(true);
  const [range, setRange] = useState<RangeMode>("all");
  const [periodMode, setPeriodMode] = useState<PeriodMode>("year");
  const [quarterCount, setQuarterCount] = useState(12);
  const [activeSection, setActiveSection] = useState<LabSection>("backtests");
  const contentRef = useRef<HTMLElement>(null);
  const backtestsRef = useRef<HTMLElement>(null);
  const paperRef = useRef<HTMLDivElement>(null);
  const journalsRef = useRef<HTMLDivElement>(null);

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
    setPaperError(null);
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
    const paperWork = fetchStrategyLabPaper()
      .then(setPaper)
      .catch((error: unknown) =>
        setPaperError(error instanceof Error ? error.message : "Paper book failed."),
      );
    await Promise.allSettled([artifactWork, paperWork, cohortWork]);
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
  const navigate = (section: LabSection) => {
    setActiveSection(section);
    const target =
      section === "backtests"
        ? backtestsRef.current
        : section === "paper"
          ? paperRef.current
          : journalsRef.current;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  useEffect(() => {
    const root = contentRef.current;
    const paperSection = paperRef.current;
    const journals = journalsRef.current;
    if (!root || !paperSection || !journals) return;
    const sync = () => {
      const rootTop = root.getBoundingClientRect().top;
      const paperTop = paperSection.getBoundingClientRect().top;
      const journalTop = journals.getBoundingClientRect().top;
      const threshold = rootTop + Math.min(root.clientHeight * 0.45, 320);
      setActiveSection(
        journalTop <= threshold ? "journals" : paperTop <= threshold ? "paper" : "backtests",
      );
    };
    sync();
    root.addEventListener("scroll", sync, { passive: true });
    return () => root.removeEventListener("scroll", sync);
  }, [allSeries.length, paper?.books.length, cohorts?.capability]);

  return (
    <div className="flex h-svh min-h-0 flex-col bg-background">
      <StrategyLabHeader
        artifactTime={benchmarks?.generated ?? sleeves[0]?.generated ?? null}
        cohortTime={cohorts?.generated_at ?? null}
        loading={loading}
        onRefresh={() => void load()}
      />
      <div className="flex min-h-0 flex-1">
        <SeriesPicker
          series={allSeries}
          selected={selected}
          limit={lineLimit}
          limitMessage={limitMessage}
          onToggle={toggle}
          activeSection={activeSection}
          onNavigate={navigate}
        />
        <main ref={contentRef} className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <p className="border-b border-border/60 px-4 py-2.5 text-[11px] leading-snug text-muted-foreground md:hidden">
            Historical simulations, live paper positions, and observational journals are
            different evidence. No curve on this page establishes significance or expected
            return.
          </p>
          <MobileStrategyLabControls>
            <nav className="mb-5 grid grid-cols-3 gap-px border border-border bg-border">
              <button
                type="button"
                onClick={() => navigate("backtests")}
                className="bg-background px-3 py-3 text-left text-[12px] text-foreground"
              >
                Research backtests
              </button>
              <button
                type="button"
                onClick={() => navigate("paper")}
                className="bg-background px-3 py-3 text-left text-[12px] text-foreground"
              >
                Paper book
              </button>
              <button
                type="button"
                onClick={() => navigate("journals")}
                className="bg-background px-3 py-3 text-left text-[12px] text-foreground"
              >
                Forward journals
              </button>
            </nav>
            <div className="mb-5">{picker}</div>
            {controls}
          </MobileStrategyLabControls>
          <div
            className="mx-auto w-full max-w-[2200px]"
            // Reserve the collapsed terminal strip's height so the last section
            // isn't hidden under the fixed viewport overlay (board does this).
            style={{ paddingBottom: `calc(1.5rem + ${TERMINAL_COLLAPSED_HEIGHT}px)` }}
          >
            <section ref={backtestsRef} id="research-backtests" className="scroll-mt-0">
              <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border/60 px-4 py-4 sm:px-6">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-amber-500">
                    backtest
                  </p>
                  <h2 className="mt-1 text-[16px] font-semibold">Research backtests</h2>
                </div>
                <p className="font-mono text-[10px] text-muted-foreground">
                  native dates · no forward-fill
                </p>
              </div>
              {artifactError ? (
                <div className="px-4 py-6 sm:px-6">
                  <StrategyLabErrorBoundary message={artifactError} />
                </div>
              ) : artifactCapability === "unavailable" ? (
                <div className="px-4 py-6 sm:px-6">
                  <StrategyLabEmptyState>
                    No committed Strategy Lab artifacts are available.
                  </StrategyLabEmptyState>
                </div>
              ) : allSeries.length === 0 ? (
                <div className="px-4 py-12 text-sm text-muted-foreground sm:px-6">
                  Loading committed artifacts…
                </div>
              ) : (
                <>
                  <div className="hidden px-4 sm:px-6 md:block">{controls}</div>
                  <div className="space-y-7 px-4 py-5 sm:px-6">
                    <SourceHonestyRail series={visible} />
                    <div>
                      <h3 className="mb-2 text-[14px] font-medium">
                        Equity <span className="font-normal text-muted-foreground">· historical simulation</span>
                      </h3>
                      <BacktestEquityChart series={visible} logScale={logScale} range={range} />
                    </div>
                    <div className="border-t border-border/60 pt-6">
                      <h3 className="mb-3 text-[14px] font-medium">
                        Calendar {periodMode} returns
                      </h3>
                      <PeriodReturnsChart
                        series={visible}
                        mode={periodMode}
                        quarterCount={quarterCount}
                      />
                    </div>
                    <div className="border-t border-border/60 pt-6">
                      <h3 className="mb-3 text-[14px] font-medium">Reported metrics</h3>
                      <BacktestMetricsStrip series={visible} />
                    </div>
                  </div>
                </>
              )}
            </section>
            <div ref={paperRef}>
              <PaperBookSection catalog={paper} error={paperError} />
            </div>
            <div ref={journalsRef}>
              <CohortSection catalog={cohorts} error={cohortError} />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
