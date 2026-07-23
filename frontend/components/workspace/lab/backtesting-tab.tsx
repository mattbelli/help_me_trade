"use client";

// BACKTESTING — the evidence layer. A port of the existing research-backtest
// viewer (components/strategy-lab/*), restyled to the A2 grammar and rendered
// inside the workspace shell. Same live APIs: fetchStrategyLabCatalog / Sleeve /
// Benchmarks. The legacy /strategy-lab route stays untouched. Honesty is
// load-bearing: LEVELS are ~sim, RANKINGS are the trustworthy signal — every
// caveat the artifacts carry is preserved.

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchStrategyLabBenchmarks,
  fetchStrategyLabCatalog,
  fetchStrategyLabSleeve,
  type StrategyLabBenchmarks,
  type StrategyLabPoint,
  type StrategyLabSleeve,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  EmptyNote,
  ErrorNote,
  Hedge,
  HonestyBar,
  LoadingNote,
  SecHead,
} from "./lab-ui";

// Series colors as theme tokens (track any active theme; distinct hues).
const SLEEVE_COLORS = [
  "var(--c-accent)",
  "var(--c-violet)",
  "var(--warn)",
  "var(--pos)",
];
const benchmarkColor = (id: string) =>
  id === "SPY" ? "var(--fg-faint)" : "var(--fg-dim)";

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
type RangeMode = "all" | "10y" | "5y";

function dateMs(value: string): number {
  return Date.parse(`${value}T00:00:00Z`);
}
function money(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function pct(value: number | undefined, alreadyPercent = false): string {
  if (value == null) return "—";
  return `${(alreadyPercent ? value : value * 100).toFixed(1)}%`;
}
function filterRange(points: StrategyLabPoint[], range: RangeMode) {
  if (range === "all" || points.length === 0) return points;
  const last = dateMs(points[points.length - 1][0]);
  const years = range === "10y" ? 10 : 5;
  const cutoff = last - years * 365.25 * 86_400_000;
  return points.filter(([date]) => dateMs(date) >= cutoff);
}
function sampleLabel(item: LabSeries): string {
  if (item.evidence === "backtest")
    return `${item.sleeve?.config.n_trades ?? "unknown"} trades`;
  return `${item.points.length} obs`;
}
function derivedMetrics(points: StrategyLabPoint[]) {
  if (points.length < 2) return { cagr: undefined, maxDrawdown: undefined };
  const years =
    (dateMs(points.at(-1)![0]) - dateMs(points[0][0])) / (365.25 * 86_400_000);
  const cagr =
    Math.pow(points.at(-1)![1] / points[0][1], 1 / Math.max(years, 1 / 365)) - 1;
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
  const start =
    sleeve.points[0][0] > spy.points[0][0] ? sleeve.points[0][0] : spy.points[0][0];
  const end =
    sleeve.points.at(-1)![0] < spy.points.at(-1)![0]
      ? sleeve.points.at(-1)![0]
      : spy.points.at(-1)![0];
  const trim = (p: StrategyLabPoint[]) =>
    p.filter(([date]) => date >= start && date <= end);
  const s = derivedMetrics(trim(sleeve.points));
  const b = derivedMetrics(trim(spy.points));
  if (s.cagr == null || b.cagr == null) return null;
  return { value: s.cagr - b.cagr, start, end };
}
function needsLevelsWarning(sleeve: StrategyLabSleeve): boolean {
  const blob = JSON.stringify({
    config: sleeve.config,
    provenance: sleeve.provenance,
    caveats: sleeve.caveats,
  }).toLowerCase();
  return (
    blob.includes("linear-accrual") || blob.includes("optimistic fill")
  );
}

// ── the equity chart (SVG math preserved from the legacy viewer) ────────────
function BacktestEquityChart({
  series,
  logScale,
  range,
}: {
  series: LabSeries[];
  logScale: boolean;
  range: RangeMode;
}) {
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const plotted = series.map((item) => ({
    ...item,
    points: filterRange(item.points, range),
  }));
  const all = plotted.flatMap((item) => item.points);
  if (all.length === 0)
    return <EmptyNote>no equity observations in this window</EmptyNote>;
  const xs = all.map(([date]) => dateMs(date));
  const ys = all.map(([, v]) => (logScale ? Math.log(Math.max(v, 1)) : v));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const x = (date: string) =>
    74 + ((dateMs(date) - minX) / Math.max(maxX - minX, 1)) * 1102;
  const y = (value: number) =>
    24 +
    (1 -
      ((logScale ? Math.log(Math.max(value, 1)) : value) - minY) /
        Math.max(maxY - minY, 1)) *
      398;

  return (
    <figure className="min-w-0">
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1.5 text-[length:var(--fs-body)]">
        {plotted.map((item) => (
          <span key={item.id} className="flex items-baseline gap-1.5">
            <span className="font-mono" style={{ color: item.color }}>
              ━━
            </span>
            <span className="text-foreground">{item.label}</span>
            {item.evidence === "backtest" ? (
              <Hedge>~sim</Hedge>
            ) : (
              <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                benchmark
              </span>
            )}
            <span className="font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              {sampleLabel(item)}
            </span>
          </span>
        ))}
      </div>
      <svg
        viewBox="0 0 1200 470"
        className="h-[clamp(360px,52vh,620px)] w-full touch-pan-y text-[var(--fg-faint)]"
        role="img"
        aria-label="Historical simulation equity (~sim levels)"
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const viewX = ((event.clientX - bounds.left) / bounds.width) * 1200;
          const ratio = Math.max(0, Math.min(1, (viewX - 74) / 1102));
          setHoverTime(minX + ratio * (maxX - minX));
        }}
        onPointerLeave={() => setHoverTime(null)}
      >
        {[0, 1, 2, 3, 4].map((tick) => {
          const yy = 24 + tick * 99.5;
          const transformed = maxY - (tick / 4) * (maxY - minY);
          const value = logScale ? Math.exp(transformed) : transformed;
          return (
            <g key={tick}>
              <line
                x1="74"
                x2="1176"
                y1={yy}
                y2={yy}
                stroke="currentColor"
                opacity=".14"
              />
              <text
                x="64"
                y={yy + 5}
                textAnchor="end"
                className="fill-[var(--fg-faint)] text-[14px]"
              >
                {money(value)}
              </text>
            </g>
          );
        })}
        {plotted.map((item) => {
          const path = item.points
            .map(
              ([date, value], index) =>
                `${index === 0 ? "M" : "L"}${x(date).toFixed(2)},${y(value).toFixed(2)}`,
            )
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
                strokeWidth={item.evidence === "backtest" ? 2.2 : 1.5}
                strokeDasharray={item.evidence === "benchmark" ? "4 4" : undefined}
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
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
            opacity=".4"
          />
        )}
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
          <text
            key={ratio}
            x={74 + ratio * 1102}
            y="456"
            textAnchor={ratio === 0 ? "start" : ratio === 1 ? "end" : "middle"}
            className="fill-[var(--fg-faint)] text-[13px]"
          >
            {new Date(minX + ratio * (maxX - minX)).toISOString().slice(0, 7)}
          </text>
        ))}
      </svg>
    </figure>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[length:var(--fs-micro)] uppercase leading-tight tracking-[0.1em] text-[var(--fg-faint)]">
        {label}
      </dt>
      <dd className="mt-1.5 text-[length:var(--fs-emph)] leading-none tabular-nums text-foreground">
        {value}
      </dd>
    </div>
  );
}

function BacktestMetricsStrip({ series }: { series: LabSeries[] }) {
  const spy = series.find((item) => item.id === "benchmark:SPY");
  return (
    <div className="grid gap-px border border-[var(--line-divider)] bg-[var(--line-divider)] xl:grid-cols-2">
      {series.map((item) => {
        const derived = derivedMetrics(item.points);
        const metrics = item.sleeve?.metrics;
        const excess = item.sleeve ? overlapExcess(item, spy) : null;
        return (
          <div key={item.id} className="bg-[var(--surface-canvas)] p-4">
            <div className="mb-4 flex flex-wrap items-baseline gap-x-2">
              <h3 className="text-[length:var(--fs-body)] font-medium text-foreground">
                {item.label}
              </h3>
              {item.evidence === "backtest" ? (
                <Hedge>~sim</Hedge>
              ) : (
                <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                  benchmark
                </span>
              )}
              <span className="font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                {sampleLabel(item)}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-x-5 gap-y-4 font-mono sm:grid-cols-4">
              <Metric label="modeled CAGR" value={pct(metrics?.cagr ?? derived.cagr)} />
              <Metric
                label="modeled max drawdown"
                value={pct(metrics?.max_drawdown ?? derived.maxDrawdown)}
              />
              {item.sleeve && (
                <>
                  <Metric
                    label="modeled Sharpe"
                    value={metrics?.sharpe?.toFixed(2) ?? "—"}
                  />
                  <Metric label="average invested" value={pct(metrics?.avg_invested)} />
                  <Metric
                    label="trade count"
                    value={String(item.sleeve.config.n_trades ?? "—")}
                  />
                  <Metric
                    label="historical per-trade mean"
                    value={pct(metrics?.per_trade_mean_pct, true)}
                  />
                  <Metric
                    label="raw historical win rate"
                    value={pct(metrics?.win_pct, true)}
                  />
                  <Metric
                    label={
                      excess
                        ? `modeled excess vs SPY · ${excess.start} → ${excess.end}`
                        : "modeled excess vs SPY"
                    }
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

function SourceHonestyRail({ series }: { series: LabSeries[] }) {
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
              "min-w-0 border border-[var(--line-divider)] bg-[var(--surface-widget)] px-3 py-2.5",
              item.sleeve &&
                "border-[color-mix(in_srgb,var(--warn)_35%,var(--line-divider))]",
            )}
          >
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[length:var(--fs-table)]">
              <span className="truncate font-medium text-foreground">
                {item.label}
              </span>
              {item.evidence === "backtest" ? (
                <Hedge>~sim</Hedge>
              ) : (
                <span className="text-[var(--fg-faint)]">benchmark</span>
              )}
              <span className="font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                {sample}
              </span>
            </div>
            {warning ? (
              <p className="mt-2 text-[length:var(--fs-table)] leading-snug text-[var(--warn)]">
                Absolute levels are not calibrated. Linear-accrual marking
                suppresses volatility and drawdown, and signal-close fills are
                optimistic. Use these simulations to compare rankings, not to
                trust absolute Sharpe or drawdown.
              </p>
            ) : null}
            {caveats.length > 0 ? (
              <div className="mt-2 border-t border-[color-mix(in_srgb,var(--warn)_20%,transparent)] pt-2 text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
                <p>{caveats[0]}</p>
                {caveats.length > 1 ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer list-none font-mono text-[length:var(--fs-micro)] text-[var(--warn)]">
                      +{caveats.length - 1} caveat
                      {caveats.length === 2 ? "" : "s"}
                    </summary>
                    <div className="mt-1.5 space-y-1">
                      {caveats.slice(1).map((c, i) => (
                        <p key={`${i}:${c}`}>{c}</p>
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

// word-toggle helper (plain lowercase words, accent on active — no pills)
function WordToggle<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <span className="inline-flex items-baseline gap-2.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={cn(
            "text-[length:var(--fs-table)] lowercase outline-none",
            value === o
              ? "text-[var(--c-accent)]"
              : "text-[var(--fg-dim)] hover:text-foreground",
          )}
        >
          {o}
        </button>
      ))}
    </span>
  );
}

const SELECT_LIMIT = 6;

export function BacktestingTab() {
  const [sleeves, setSleeves] = useState<StrategyLabSleeve[]>([]);
  const [benchmarks, setBenchmarks] = useState<StrategyLabBenchmarks | null>(
    null,
  );
  const [capability, setCapability] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [logScale, setLogScale] = useState(true);
  const [range, setRange] = useState<RangeMode>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const catalog = await fetchStrategyLabCatalog();
      setCapability(catalog.capability ?? "available");
      if (catalog.capability === "unavailable") {
        setSleeves([]);
        setBenchmarks(null);
        return;
      }
      const [fullSleeves, benchmarkData] = await Promise.all([
        Promise.all(catalog.sleeves.map((s) => fetchStrategyLabSleeve(s.id))),
        fetchStrategyLabBenchmarks(),
      ]);
      setSleeves(fullSleeves);
      setBenchmarks(benchmarkData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "backtest artifacts failed");
    } finally {
      setLoading(false);
    }
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
    const benchmarkSeries = Object.entries(benchmarks?.series ?? {}).map(
      ([id, points]) => ({
        id: `benchmark:${id}`,
        label: id,
        evidence: "benchmark" as const,
        generated: benchmarks?.generated ?? "unknown",
        points,
        color: benchmarkColor(id),
      }),
    );
    return [...sleeveSeries, ...benchmarkSeries];
  }, [sleeves, benchmarks]);

  useEffect(() => {
    if (allSeries.length === 0) return;
    setSelected((current) => {
      if (current.size > 0) return current;
      const next = new Set<string>();
      for (const item of allSeries) {
        if (
          item.evidence === "backtest" ||
          item.id === "benchmark:SPY" ||
          item.id === "benchmark:IWR"
        ) {
          if (next.size < SELECT_LIMIT) next.add(item.id);
        }
      }
      return next;
    });
  }, [allSeries]);

  const chosen = allSeries.filter((s) => selected.has(s.id));
  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else if (next.size < SELECT_LIMIT) next.add(id);
      return next;
    });

  return (
    <div className="space-y-4">
      <HonestyBar>
        <b className="text-[var(--warn)]">Backtest LEVELS are approximate</b> —
        simulated fills &amp; costs. Read the P&amp;L as a shape, not an
        achievable number. <b className="text-foreground">RANKINGS</b> — which
        strategy / parameter set beats which — are the trustworthy signal. Every
        simulated figure is marked <Hedge>~sim</Hedge>; unvalidated signals are
        labeled perception, never dressed as edge.
      </HonestyBar>

      {error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : loading ? (
        <LoadingNote label="loading backtest artifacts…" />
      ) : capability === "unavailable" || allSeries.length === 0 ? (
        <EmptyNote>
          no backtest artifacts available — the research artifact directory is
          empty on this host (run the offline backtests to populate it).
        </EmptyNote>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[220px_1fr]">
          {/* series picker */}
          <aside className="space-y-4">
            <div>
              <SecHead label="series" meta={`${selected.size}/${SELECT_LIMIT}`} />
              <div className="space-y-0.5">
                {allSeries.map((item) => (
                  <label
                    key={item.id}
                    className={cn(
                      "flex min-h-8 cursor-pointer items-center gap-2 border-l-2 px-2 text-[length:var(--fs-body)] transition-colors hover:bg-[var(--surface-hover)]",
                      selected.has(item.id)
                        ? "bg-[color-mix(in_srgb,var(--foreground)_4%,transparent)] text-foreground"
                        : "border-l-transparent text-[var(--fg-dim)]",
                    )}
                    style={
                      selected.has(item.id)
                        ? { borderLeftColor: item.color }
                        : undefined
                    }
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(item.id)}
                      onChange={() => toggle(item.id)}
                      className="sr-only"
                    />
                    <span
                      className="h-px w-3 shrink-0"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.evidence === "backtest" ? (
                      <Hedge>~sim</Hedge>
                    ) : (
                      <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                        bench
                      </span>
                    )}
                  </label>
                ))}
              </div>
            </div>
            <div className="space-y-2 border-t border-[var(--line-divider)] pt-3">
              <div className="flex items-baseline justify-between">
                <span className="text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
                  y-scale
                </span>
                <WordToggle
                  options={["log", "linear"] as const}
                  value={logScale ? "log" : "linear"}
                  onChange={(v) => setLogScale(v === "log")}
                />
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
                  range
                </span>
                <WordToggle
                  options={["all", "10y", "5y"] as const}
                  value={range}
                  onChange={setRange}
                />
              </div>
            </div>
          </aside>

          {/* results */}
          <div className="min-w-0 space-y-5">
            {chosen.length === 0 ? (
              <EmptyNote>select a series to plot</EmptyNote>
            ) : (
              <>
                <div>
                  <SecHead
                    label="simulated equity vs benchmarks"
                    note={<Hedge>~sim levels · rankings trustworthy</Hedge>}
                  />
                  <BacktestEquityChart
                    series={chosen}
                    logScale={logScale}
                    range={range}
                  />
                </div>
                <div>
                  <SecHead label="modeled metrics" note={<Hedge>~sim</Hedge>} />
                  <BacktestMetricsStrip series={chosen} />
                </div>
                <div>
                  <SecHead label="source honesty" meta="what each curve is" />
                  <SourceHonestyRail series={chosen} />
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
