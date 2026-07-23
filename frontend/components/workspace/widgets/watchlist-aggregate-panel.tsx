"use client";

import { useEffect, useRef, useState } from "react";

import { FloatingPanel } from "@/components/ui/floating-panel";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  fetchWatchlistAggregate,
  type WatchlistAggregate,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const RANGES = [
  { key: "1m", days: 30 },
  { key: "3m", days: 90 },
  { key: "6m", days: 180 },
  { key: "ytd", days: 0 }, // resolved at click time from Jan 1
  { key: "1y", days: 365 },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

function rangeDays(key: RangeKey): number {
  if (key === "ytd") {
    const now = new Date();
    const jan1 = new Date(now.getFullYear(), 0, 1).getTime();
    return Math.max(2, Math.ceil((now.getTime() - jan1) / 86_400_000));
  }
  return RANGES.find((r) => r.key === key)?.days ?? 90;
}

function fmtPct(p: number): string {
  return `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`;
}

// viewBox space — the SVG stretches to its container (preserveAspectRatio
// "none"), so strokes use non-scaling-stroke to stay hairline.
const W = 600;
const H = 220;

function AggregateCurve({
  points,
  hover,
  onHover,
}: {
  points: { date: string; pct: number }[];
  hover: number | null;
  onHover: (idx: number | null) => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const n = points.length;
  const lo = Math.min(0, ...points.map((p) => p.pct));
  const hi = Math.max(0, ...points.map((p) => p.pct));
  const pad = Math.max((hi - lo) * 0.06, 0.25);
  const top = hi + pad;
  const span = top - (lo - pad);
  const x = (i: number) => (n <= 1 ? W / 2 : (i / (n - 1)) * W);
  const y = (p: number) => ((top - p) / span) * H;

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.pct)}`).join(" ");
  const area = `${line} L${x(n - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const color = points[n - 1].pct >= 0 ? "var(--pos)" : "var(--neg)";

  return (
    <div
      ref={boxRef}
      className="relative h-48 w-full cursor-crosshair"
      onPointerMove={(e) => {
        const rect = boxRef.current?.getBoundingClientRect();
        if (!rect || rect.width <= 0) return;
        const frac = (e.clientX - rect.left) / rect.width;
        onHover(Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1)))));
      }}
      onPointerLeave={() => onHover(null)}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-full w-full"
        aria-hidden
      >
        <line
          x1={0}
          y1={y(0)}
          x2={W}
          y2={y(0)}
          stroke="var(--fg-faint)"
          strokeDasharray="3 4"
          vectorEffect="non-scaling-stroke"
        />
        <path d={area} fill={color} fillOpacity={0.08} stroke="none" />
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
        {hover != null ? (
          <line
            x1={x(hover)}
            y1={0}
            x2={x(hover)}
            y2={H}
            stroke="var(--fg-dim)"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </svg>
    </div>
  );
}

/** Floating equal-weight return curve for the displayed watchlist — the
 *  "how did this group do" view, launched from the widget header's ↗. */
export function WatchlistAggregatePanel({
  listId,
  linked,
  onSymbolClick,
  onClose,
}: {
  listId: string;
  linked: boolean;
  onSymbolClick: (symbol: string) => void;
  onClose: () => void;
}) {
  const [range, setRange] = useState<RangeKey>("3m");
  const [data, setData] = useState<WatchlistAggregate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setHover(null);
    fetchWatchlistAggregate(listId, rangeDays(range))
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch(() => {
        if (!cancelled) setError("failed to load aggregate");
      });
    return () => {
      cancelled = true;
    };
  }, [listId, range]);

  const points = data?.points ?? [];
  const active =
    hover != null && points[hover] ? points[hover] : points[points.length - 1];

  return (
    <FloatingPanel
      open
      onClose={onClose}
      seedKey={listId}
      initialWidth={560}
      initialHeight={500}
      ariaLabel="Watchlist aggregate return"
      className="w-[min(560px,92vw)] max-h-[min(660px,85vh)]"
      contentClassName="flex flex-col gap-3 overflow-y-auto p-4"
      header={
        <span className="font-mono text-[length:var(--fs-title)] leading-snug">
          {data?.name ?? "Watchlist"}{" "}
          <span className="text-[var(--fg-dim)]">· aggregate return</span>
        </span>
      }
    >
      {error ? (
        <div className="py-8 text-center text-[length:var(--fs-table)] text-[var(--neg)]">
          {error}
        </div>
      ) : !data ? (
        <div className="py-8 text-center text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          loading…
        </div>
      ) : (
        <>
          <div className="flex items-baseline gap-3">
            {active ? (
              <>
                <span
                  className="text-[length:var(--fs-hero)] font-semibold tabular-nums"
                  style={{ color: active.pct >= 0 ? "var(--pos)" : "var(--neg)" }}
                >
                  {fmtPct(active.pct)}
                </span>
                <span className="text-[length:var(--fs-table)] text-[var(--fg-dim)] tabular-nums">
                  {hover != null ? active.date : `${active.date} · ${data.per_symbol.length} symbols`}
                </span>
              </>
            ) : (
              <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                not enough history in this window
              </span>
            )}
            <div className="flex-1" />
            <div className="flex items-baseline gap-2">
              {RANGES.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  onClick={() => setRange(r.key)}
                  className={cn(
                    "text-[length:var(--fs-table)] lowercase outline-none",
                    range === r.key
                      ? "text-[var(--c-accent)]"
                      : "text-[var(--fg-dim)] hover:text-foreground",
                  )}
                >
                  {r.key}
                </button>
              ))}
            </div>
          </div>

          {points.length >= 2 ? (
            <AggregateCurve points={points} hover={hover} onHover={setHover} />
          ) : null}

          {data.per_symbol.length ? (
            <div className="grid grid-cols-3 gap-x-5 gap-y-1">
              {data.per_symbol.map((row) => (
                <div
                  key={row.symbol}
                  className="flex items-baseline justify-between gap-2 text-[length:var(--fs-table)]"
                >
                  <ClickableSymbol
                    symbol={row.symbol}
                    linked={linked}
                    onActivate={() => onSymbolClick(row.symbol)}
                  />
                  <span
                    className="tabular-nums"
                    style={{
                      color: row.return_pct >= 0 ? "var(--pos)" : "var(--neg)",
                    }}
                  >
                    {fmtPct(row.return_pct)}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {data.symbols_missing.length ? (
            <div className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              no data: {data.symbols_missing.join(", ")}
            </div>
          ) : null}

          <div className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            equal-weight · rebalanced daily · price return, no dividends
          </div>
        </>
      )}
    </FloatingPanel>
  );
}
