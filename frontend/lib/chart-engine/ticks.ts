/**
 * Tick generation — pure (Intl is allowed; DOM is not). Price ticks step in
 * 1-2-5·10ᵏ; time ticks land on REAL bars, preferring year > month > day
 * boundaries over plain times (CHART_ENGINE.md → Time scale / Price scale).
 */

import { xOf, visibleBarRange } from "./scales";
import {
  PRICE_TICK_TARGET_PX,
  TIME_TICK_TARGET_PX,
  type EngineBar,
  type PriceRange,
  type Viewport,
} from "./types";

// --- price ticks ---------------------------------------------------------------------

export type PriceTick = { price: number; label: string };

/** Smallest 1-2-5·10ᵏ step ≥ raw. */
export function niceStep(raw: number): number {
  if (!(raw > 0) || !Number.isFinite(raw)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) {
    if (mag * m >= raw - mag * 1e-9) return mag * m;
  }
  return mag * 10;
}

/** Label decimals for a step: at least 2 (finance convention), more when the step is finer. */
export function decimalsFor(step: number): number {
  if (!(step > 0) || !Number.isFinite(step)) return 2;
  const needed = -Math.floor(Math.log10(step) + 1e-9);
  return Math.min(6, Math.max(2, needed));
}

/** Ticks covering `range`, ~1 per PRICE_TICK_TARGET_PX of `heightPx`. */
export function priceTicks(range: PriceRange, heightPx: number): PriceTick[] {
  const span = range.max - range.min;
  if (!(span > 0) || heightPx <= 0) return [];
  const count = Math.max(1, heightPx / PRICE_TICK_TARGET_PX);
  const step = niceStep(span / count);
  const decimals = decimalsFor(step);
  const ticks: PriceTick[] = [];
  const first = Math.ceil(range.min / step) * step;
  // Guard against float drift walking one past the top.
  for (let p = first; p <= range.max + step * 1e-6; p += step) {
    const price = Number(p.toFixed(8));
    ticks.push({ price, label: price.toFixed(decimals) });
  }
  return ticks;
}

// --- ET calendar parts (memoized — computed per timestamp, not per repaint) ----------

const ET = "America/New_York";
const ET_PARTS_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: ET,
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

type EtParts = {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  dayPeriod: string;
};

// Intl.formatToParts costs ~0.2ms a call; uncached that pinned the app at ~1
// FPS once before (chart-time.ts sessionMemo incident) — same defense here.
const partsMemo = new Map<number, EtParts>();

export function etParts(timeMs: number): EtParts {
  const memo = partsMemo.get(timeMs);
  if (memo) return memo;
  const raw = ET_PARTS_FMT.formatToParts(new Date(timeMs));
  const get = (type: string) => raw.find((p) => p.type === type)?.value ?? "";
  const parts: EtParts = {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hour: get("hour"),
    minute: get("minute"),
    dayPeriod: get("dayPeriod"),
  };
  if (partsMemo.size > 50_000) partsMemo.clear();
  partsMemo.set(timeMs, parts);
  return parts;
}

/** Crosshair/time-axis chip label, e.g. `Wed Jul 9 · 10:30 AM` / `Wed Jul 9 2026`. */
export function crosshairTimeLabel(timeMs: number, intraday: boolean): string {
  const date = new Date(timeMs);
  if (intraday) {
    return date.toLocaleString("en-US", {
      timeZone: ET,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  }
  return date.toLocaleDateString("en-US", {
    timeZone: ET,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// --- time ticks ----------------------------------------------------------------------

export type TimeTick = {
  index: number;
  x: number;
  label: string;
  /** Calendar boundary (year/month/day) vs a plain intraday time. */
  major: boolean;
};

/**
 * Boundary rank of bar `i` vs its predecessor: 0 year, 1 month, 2 day, then
 * intraday: 3 hour top, 4 half-hour, 5 plain — so the axis prefers round ET
 * times over arbitrary bar strides when density forces a choice.
 */
function rankOf(bars: readonly EngineBar[], i: number): number {
  const cur = etParts(bars[i].time);
  if (i === 0) return 2; // first bar of the series gets a date label
  const prev = etParts(bars[i - 1].time);
  if (cur.year !== prev.year) return 0;
  if (cur.month !== prev.month) return 1;
  if (cur.day !== prev.day) return 2;
  if (cur.hour !== prev.hour || cur.dayPeriod !== prev.dayPeriod) return 3;
  if (cur.minute !== prev.minute && Number(cur.minute) % 30 === 0) return 4;
  return 5;
}

function labelFor(bars: readonly EngineBar[], i: number, rank: number, intraday: boolean): string {
  const p = etParts(bars[i].time);
  switch (rank) {
    case 0:
      return p.year;
    case 1:
      return p.month;
    case 2:
      return `${p.month} ${p.day}`;
    default:
      return intraday ? `${p.hour}:${p.minute} ${p.dayPeriod}` : `${p.month} ${p.day}`;
  }
}

/**
 * Pick time ticks for the visible window: candidates ranked by calendar
 * boundary, accepted greedily best-rank-first with ≥TIME_TICK_TARGET_PX
 * spacing — so a month boundary beats a nearby plain time, and labels never
 * crowd. Returned sorted by index.
 */
export function timeTicks(
  bars: readonly EngineBar[],
  vp: Viewport,
  paneWidth: number,
  intraday: boolean,
): TimeTick[] {
  const range = visibleBarRange(vp, paneWidth, bars.length);
  if (!range) return [];

  const candidates: { index: number; rank: number }[] = [];
  for (let i = range.from; i <= range.to; i++) {
    candidates.push({ index: i, rank: rankOf(bars, i) });
  }
  candidates.sort((a, b) => a.rank - b.rank || a.index - b.index);

  const accepted: TimeTick[] = [];
  const minGap = TIME_TICK_TARGET_PX;
  for (const c of candidates) {
    const x = xOf(c.index, vp, paneWidth);
    let ok = true;
    for (const t of accepted) {
      if (Math.abs(t.x - x) < minGap) {
        ok = false;
        break;
      }
    }
    if (!ok) continue;
    accepted.push({
      index: c.index,
      x,
      label: labelFor(bars, c.index, c.rank, intraday),
      major: c.rank < 3,
    });
  }
  accepted.sort((a, b) => a.index - b.index);
  return accepted;
}
