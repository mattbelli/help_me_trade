/**
 * Time + price scales — pure math over the bar-index domain (CHART_ENGINE.md
 * → Time scale / Price scale). No DOM. Every function is total: callers may
 * hand it empty bars or degenerate viewports and get something clamped back.
 */

import {
  AUTOSCALE_PAD,
  DEFAULT_BAR_SPACING,
  DEFAULT_RIGHT_OFFSET,
  MAX_BAR_SPACING,
  MIN_BAR_SPACING,
  MIN_VISIBLE_BARS,
  type ChartStyle,
  type EngineBar,
  type PriceRange,
  type Viewport,
} from "./types";

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

// --- bar index ↔ x ------------------------------------------------------------------

/** Logical-px x of fractional bar index `i` (bar centers at integers). */
export function xOf(i: number, vp: Viewport, paneWidth: number): number {
  return paneWidth - (vp.rightEdgeIndex - i) * vp.barSpacing;
}

/** Fractional bar index under logical-px `x` — exact inverse of xOf. */
export function indexAt(x: number, vp: Viewport, paneWidth: number): number {
  return vp.rightEdgeIndex - (paneWidth - x) / vp.barSpacing;
}

// --- viewport ops --------------------------------------------------------------------

/**
 * Enforce the viewport invariants: barSpacing within bounds, at least
 * MIN_VISIBLE_BARS bars on screen (i.e. right-of-last-bar whitespace capped at
 * paneWidth/barSpacing − MIN_VISIBLE_BARS bars), and no scrolling the last
 * visible bar off the right past the first MIN_VISIBLE_BARS bars.
 */
export function clampViewport(
  vp: Viewport,
  paneWidth: number,
  barCount: number,
): Viewport {
  const barSpacing = clamp(vp.barSpacing, MIN_BAR_SPACING, MAX_BAR_SPACING);
  if (barCount <= 0) return { barSpacing, rightEdgeIndex: 0 };
  const lastIndex = barCount - 1;
  const visible = paneWidth / barSpacing;
  const reMax = lastIndex + Math.max(0, visible - MIN_VISIBLE_BARS);
  const reMin = Math.min(MIN_VISIBLE_BARS, barCount) - 1;
  const rightEdgeIndex = clamp(
    vp.rightEdgeIndex,
    Math.min(reMin, reMax),
    reMax,
  );
  return { barSpacing, rightEdgeIndex };
}

/** Default view: latest bar with DEFAULT_RIGHT_OFFSET bars of whitespace. */
export function defaultViewport(barCount: number, paneWidth: number): Viewport {
  return clampViewport(
    {
      barSpacing: DEFAULT_BAR_SPACING,
      rightEdgeIndex: barCount - 1 + DEFAULT_RIGHT_OFFSET,
    },
    paneWidth,
    barCount,
  );
}

/**
 * Anchored zoom: scale barSpacing by `factor` keeping the bar index under
 * logical-px `x` fixed (unless a clamp binds).
 */
export function zoomAt(
  vp: Viewport,
  x: number,
  factor: number,
  paneWidth: number,
  barCount: number,
): Viewport {
  const anchor = indexAt(x, vp, paneWidth);
  const barSpacing = clamp(
    vp.barSpacing * factor,
    MIN_BAR_SPACING,
    MAX_BAR_SPACING,
  );
  const rightEdgeIndex = anchor + (paneWidth - x) / barSpacing;
  return clampViewport({ barSpacing, rightEdgeIndex }, paneWidth, barCount);
}

/** Pan by `dx` logical px (drag right → content follows → older bars). */
export function pan(
  vp: Viewport,
  dx: number,
  paneWidth: number,
  barCount: number,
): Viewport {
  return clampViewport(
    { barSpacing: vp.barSpacing, rightEdgeIndex: vp.rightEdgeIndex - dx / vp.barSpacing },
    paneWidth,
    barCount,
  );
}

/**
 * Integer bar indices visible in the pane, inclusive, clamped to the data —
 * null when no bar is on screen. Half a bar of slack each side so a bar whose
 * center is just off-pane still draws its on-pane half.
 */
export function visibleBarRange(
  vp: Viewport,
  paneWidth: number,
  barCount: number,
): { from: number; to: number } | null {
  if (barCount <= 0) return null;
  const leftEdgeIndex = indexAt(0, vp, paneWidth);
  const from = Math.max(0, Math.ceil(leftEdgeIndex - 0.5));
  const to = Math.min(barCount - 1, Math.floor(vp.rightEdgeIndex + 0.5));
  if (from > to) return null;
  return { from, to };
}

// --- price scale ---------------------------------------------------------------------

/**
 * Autoscale range over the visible bars (high/low for candles, close for
 * line) with AUTOSCALE_PAD padding each side. Flat data gets a synthetic pad
 * so the range never collapses to zero height.
 */
export function autoPriceRange(
  bars: readonly EngineBar[],
  vp: Viewport,
  paneWidth: number,
  style: ChartStyle,
): PriceRange {
  const range = visibleBarRange(vp, paneWidth, bars.length);
  if (!range) return { min: 0, max: 1 };
  let min = Infinity;
  let max = -Infinity;
  for (let i = range.from; i <= range.to; i++) {
    const b = bars[i];
    if (style === "line") {
      if (b.close < min) min = b.close;
      if (b.close > max) max = b.close;
    } else {
      if (b.low < min) min = b.low;
      if (b.high > max) max = b.high;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 };
  const pad =
    max > min ? (max - min) * AUTOSCALE_PAD : Math.max(Math.abs(max) * 0.01, 0.01);
  return { min: min - pad, max: max + pad };
}

/**
 * Fractional bar index for a timestamp (epoch ms) — the data-space → bar-index
 * bridge for overlay anchors. Interpolates between the surrounding bars;
 * beyond the ends it extrapolates using the adjacent bar interval (so a
 * weekend anchor lands just right of Friday instead of at a wall-clock hole).
 */
export function indexOfTime(bars: readonly EngineBar[], timeMs: number): number {
  const n = bars.length;
  if (n === 0) return 0;
  const first = bars[0].time;
  const last = bars[n - 1].time;
  if (n === 1) return 0;
  if (timeMs <= first) {
    const step = bars[1].time - first;
    return step > 0 ? (timeMs - first) / step : 0;
  }
  if (timeMs >= last) {
    const step = last - bars[n - 2].time;
    return n - 1 + (step > 0 ? (timeMs - last) / step : 0);
  }
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= timeMs) lo = mid;
    else hi = mid;
  }
  const span = bars[hi].time - bars[lo].time;
  return span > 0 ? lo + (timeMs - bars[lo].time) / span : lo;
}

/**
 * Inverse of indexOfTime: fractional bar index → epoch ms, interpolating
 * between bars and extrapolating past both ends by the adjacent interval
 * (drawing anchors placed in right-edge whitespace need a real timestamp).
 */
export function timeOfIndex(bars: readonly EngineBar[], index: number): number {
  const n = bars.length;
  if (n === 0) return 0;
  if (n === 1) return bars[0].time;
  if (index <= 0) {
    return bars[0].time + index * (bars[1].time - bars[0].time);
  }
  if (index >= n - 1) {
    return bars[n - 1].time + (index - (n - 1)) * (bars[n - 1].time - bars[n - 2].time);
  }
  const lo = Math.floor(index);
  return bars[lo].time + (index - lo) * (bars[lo + 1].time - bars[lo].time);
}

/**
 * Scale a price range around its center (price-axis drag → manual scale).
 * Factor >1 expands (zoom out), <1 contracts. Span is floored so repeated
 * contraction can never collapse or invert the range.
 */
export function scaleRangeAround(range: PriceRange, factor: number): PriceRange {
  const center = (range.min + range.max) / 2;
  const minSpan = Math.max(Math.abs(center) * 1e-4, 1e-6);
  const half = Math.max(((range.max - range.min) / 2) * factor, minSpan / 2);
  return { min: center - half, max: center + half };
}

/** Logical-px y of `price` within a rect spanning [top, top+height). */
export function yOf(
  price: number,
  range: PriceRange,
  top: number,
  height: number,
): number {
  const span = range.max - range.min;
  if (span <= 0) return top + height / 2;
  return top + ((range.max - price) / span) * height;
}

/** Price at logical-px `y` — inverse of yOf. */
export function priceAt(
  y: number,
  range: PriceRange,
  top: number,
  height: number,
): number {
  if (height <= 0) return range.max;
  return range.max - ((y - top) / height) * (range.max - range.min);
}
