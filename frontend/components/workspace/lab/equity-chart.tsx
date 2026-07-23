"use client";

// Book / account equity curve — ranges day|week|month|year|all, views
// equity|vs spy|drawdown. Data is live: GET /api/portfolio/history (account
// scoped) for the book, and GET /api/bars (SPY, 1Day) for the vs-spy overlay.
// Drawdown is derived client-side (running-peak). SVG idiom follows the house
// watchlist-aggregate curve: viewBox + non-scaling-stroke + hover crosshair.

import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchBars,
  fetchPortfolioHistory,
  type PnlRange,
  type PortfolioHistory,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { money, pct } from "./lab-ui";

const RANGES: { key: string; range: PnlRange; label: string }[] = [
  { key: "day", range: "1D", label: "day" },
  { key: "week", range: "1W", label: "week" },
  { key: "month", range: "1M", label: "month" },
  { key: "year", range: "1Y", label: "year" },
  { key: "all", range: "ALL", label: "all" },
];
const VIEWS = ["equity", "vs spy", "drawdown"] as const;
type View = (typeof VIEWS)[number];

const W = 1000;
const H = 240;
const GHOST_LINE = "color-mix(in srgb, var(--fg-faint) 85%, transparent)";

function toMs(ts: number): number {
  // Alpaca portfolio-history timestamps are unix seconds; be tolerant of ms.
  return ts < 2e10 ? ts * 1000 : ts;
}

function etDate(ms: number): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
  }).format(new Date(ms));
}

type Series = {
  label: string;
  color: string;
  values: number[];
  dashed?: boolean;
  area?: boolean;
};

export function EquityChart({
  accountId,
  label,
  color = "var(--c-accent)",
}: {
  accountId: string;
  label: string;
  color?: string;
}) {
  const [rangeKey, setRangeKey] = useState("month");
  const [view, setView] = useState<View>("equity");
  const [hist, setHist] = useState<PortfolioHistory | null>(null);
  const [spy, setSpy] = useState<number[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<number | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const range = RANGES.find((r) => r.key === rangeKey)?.range ?? "1M";

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setHover(null);
    setSpy(null);
    fetchPortfolioHistory(range, accountId)
      .then((next) => {
        if (!cancelled) setHist(next);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "load failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, range]);

  // vs-spy: lazily fetch SPY daily bars spanning the same window and rebase.
  useEffect(() => {
    if (view !== "vs spy" || !hist || hist.points.length < 2) return;
    let cancelled = false;
    const first = toMs(hist.points[0].timestamp);
    const last = toMs(hist.points[hist.points.length - 1].timestamp);
    const days = Math.min(
      3650,
      Math.max(2, Math.ceil((last - first) / 86_400_000) + 3),
    );
    fetchBars("SPY", { timeframe: "1Day", days })
      .then((res) => {
        if (cancelled) return;
        const inWindow = res.bars.filter(
          (b) => new Date(b.time).getTime() >= first,
        );
        const base = inWindow[0]?.close;
        setSpy(
          base && base > 0
            ? inWindow.map((b) => (b.close / base - 1) * 100)
            : null,
        );
      })
      .catch(() => {
        if (!cancelled) setSpy(null);
      });
    return () => {
      cancelled = true;
    };
  }, [view, hist]);

  const points = hist?.points ?? [];
  const equityVals = points.map((p) => p.equity);
  const base = equityVals[0] ?? 0;
  const retVals =
    base > 0 ? equityVals.map((e) => (e / base - 1) * 100) : equityVals.map(() => 0);
  const ddVals = useMemo(() => {
    let peak = -Infinity;
    return equityVals.map((e) => {
      peak = Math.max(peak, e);
      return peak > 0 ? (e / peak - 1) * 100 : 0;
    });
  }, [equityVals.join(",")]);

  const unit: "usd" | "pct" = view === "equity" ? "usd" : "pct";
  const series: Series[] = useMemo(() => {
    if (view === "equity") {
      return [{ label, color, values: equityVals }];
    }
    if (view === "drawdown") {
      return [{ label: "drawdown", color: "var(--neg)", values: ddVals, area: true }];
    }
    const out: Series[] = [{ label, color, values: retVals }];
    if (spy && spy.length >= 2) {
      out.push({
        label: "spy",
        color: GHOST_LINE,
        values: spy,
        dashed: true,
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, label, color, equityVals.join(","), ddVals, retVals.join(","), spy?.join(",")]);

  const allValues = series.flatMap((s) => s.values);
  if (unit === "pct") allValues.push(0);
  let lo = Math.min(...allValues);
  let hi = Math.max(...allValues);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  const padAmt = Math.max((hi - lo) * 0.06, unit === "usd" ? 1 : 0.1);
  const top = hi + padAmt;
  const span = top - (lo - padAmt) || 1;
  const y = (v: number) => ((top - v) / span) * H;
  const xOf = (i: number, n: number) => (n <= 1 ? W / 2 : (i / (n - 1)) * W);

  const path = (s: Series) =>
    s.values
      .map((v, i) => `${i === 0 ? "M" : "L"}${xOf(i, s.values.length)},${y(v)}`)
      .join(" ");

  const primary = series[0];
  const hoverIdx =
    hover != null && primary
      ? Math.max(0, Math.min(primary.values.length - 1, Math.round(hover * (primary.values.length - 1))))
      : null;

  const fmt = (v: number) => (unit === "usd" ? money(v, { cents: true }) : pct(v, { sign: true }));

  const readoutDate =
    hoverIdx != null && points.length > 1
      ? etDate(toMs(points[Math.round((hoverIdx / Math.max(primary.values.length - 1, 1)) * (points.length - 1))].timestamp))
      : points.length
        ? `${etDate(toMs(points[0].timestamp))} – ${etDate(toMs(points[points.length - 1].timestamp))}`
        : "";

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-[length:var(--fs-micro)] font-semibold uppercase tracking-[0.11em] text-[var(--fg-faint)]">
          equity curve
        </span>
        <div className="flex items-baseline gap-2.5">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              onClick={() => setRangeKey(r.key)}
              className={cn(
                "text-[length:var(--fs-table)] lowercase outline-none",
                rangeKey === r.key
                  ? "text-[var(--c-accent)]"
                  : "text-[var(--fg-dim)] hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
          <span
            title="next round"
            className="cursor-not-allowed text-[length:var(--fs-table)] text-[color-mix(in_srgb,var(--fg-faint)_60%,transparent)]"
          >
            custom
          </span>
        </div>
        <div className="ml-auto flex items-baseline gap-2.5">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={cn(
                "text-[length:var(--fs-table)] lowercase outline-none",
                view === v
                  ? "text-[var(--c-accent)]"
                  : "text-[var(--fg-dim)] hover:text-foreground",
              )}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <div className="py-10 text-center text-[length:var(--fs-table)] text-[var(--neg)]">
          {error}
        </div>
      ) : loading ? (
        <div className="py-10 text-center text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          loading…
        </div>
      ) : points.length < 2 ? (
        <div className="py-10 text-center text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          not enough history in this window
        </div>
      ) : (
        <>
          <div
            ref={boxRef}
            className="relative h-52 w-full cursor-crosshair"
            onPointerMove={(e) => {
              const rect = boxRef.current?.getBoundingClientRect();
              if (!rect || rect.width <= 0) return;
              setHover(
                Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
              );
            }}
            onPointerLeave={() => setHover(null)}
          >
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="h-full w-full"
              aria-hidden
            >
              {unit === "pct" ? (
                <line
                  x1={0}
                  y1={y(0)}
                  x2={W}
                  y2={y(0)}
                  stroke="var(--fg-faint)"
                  strokeDasharray="3 4"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
              {primary?.area ? (
                <path
                  d={`${path(primary)} L${W},${y(0)} L0,${y(0)} Z`}
                  fill={primary.color}
                  fillOpacity={0.1}
                  stroke="none"
                />
              ) : null}
              {series.map((s) => (
                <path
                  key={s.label}
                  d={path(s)}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={s.dashed ? 1.3 : 1.6}
                  strokeDasharray={s.dashed ? "4 4" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {hover != null ? (
                <line
                  x1={hover * W}
                  y1={0}
                  x2={hover * W}
                  y2={H}
                  stroke="var(--fg-dim)"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null}
            </svg>
          </div>

          {/* readout row */}
          <div className="mt-1 flex flex-wrap items-baseline gap-x-4 border-t border-[var(--line-divider)] pt-1 text-[length:var(--fs-table)] tabular-nums text-[var(--fg-dim)]">
            <span className="text-[var(--fg-faint)]">{readoutDate}</span>
            {series.map((s) => {
              const idx =
                hoverIdx != null
                  ? Math.max(0, Math.min(s.values.length - 1, Math.round((hoverIdx / Math.max((primary?.values.length ?? 1) - 1, 1)) * (s.values.length - 1))))
                  : s.values.length - 1;
              const v = s.values[idx];
              return (
                <span key={s.label} className="inline-flex items-center gap-1.5">
                  <span
                    className="inline-block h-0 w-3.5 border-t-2"
                    style={{ borderColor: s.color }}
                  />
                  <span className="text-[var(--fg-faint)]">{s.label}</span>
                  <span style={{ color: s.color }}>{fmt(v)}</span>
                </span>
              );
            })}
            {view === "vs spy" ? (
              <span className="text-[var(--fg-faint)]">price return</span>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
