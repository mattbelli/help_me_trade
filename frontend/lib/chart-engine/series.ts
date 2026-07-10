/**
 * Scene + cursor drawing — pure geometry to Painter calls. No DOM: everything
 * here runs identically under a future RN-Skia painter. The engine (DOM side)
 * computes nothing visual itself; it hands this module the pane size, data,
 * viewport, and colors and gets pixels.
 */

import type { Painter } from "./painter";
import { autoPriceRange, priceAt, visibleBarRange, xOf, yOf, indexAt } from "./scales";
import { crosshairTimeLabel, priceTicks, timeTicks, type PriceTick, type TimeTick } from "./ticks";
import {
  PRICE_AXIS_WIDTH,
  TIME_AXIS_HEIGHT,
  VOLUME_FRACTION,
  type ChartStyle,
  type EngineBar,
  type EngineColors,
  type PriceRange,
  type Rect,
  type Viewport,
} from "./types";

export type Layout = {
  /** Full chart pane (price + volume), excluding both axes. */
  pane: Rect;
  /** Candle/line area — pane minus the volume strip. */
  price: Rect;
  /** Volume strip along the pane bottom. */
  volume: Rect;
  priceAxis: Rect;
  timeAxis: Rect;
};

export function computeLayout(width: number, height: number): Layout {
  const paneW = Math.max(0, width - PRICE_AXIS_WIDTH);
  const paneH = Math.max(0, height - TIME_AXIS_HEIGHT);
  const volumeH = Math.round(paneH * VOLUME_FRACTION);
  return {
    pane: { x: 0, y: 0, w: paneW, h: paneH },
    price: { x: 0, y: 0, w: paneW, h: paneH - volumeH },
    volume: { x: 0, y: paneH - volumeH, w: paneW, h: volumeH },
    priceAxis: { x: paneW, y: 0, w: PRICE_AXIS_WIDTH, h: paneH },
    timeAxis: { x: 0, y: paneH, w: width, h: TIME_AXIS_HEIGHT },
  };
}

/** Everything the cursor layer needs from the last scene paint. */
export type Scene = {
  layout: Layout;
  priceRange: PriceRange;
  pTicks: PriceTick[];
  tTicks: TimeTick[];
};

const AXIS_FONT_PX = 10;
const LABEL_PAD = 6;
const CHIP_PAD_X = 5;
const CHIP_H = 16;

function axisFont(fontFamily: string): string {
  return `${AXIS_FONT_PX}px ${fontFamily}`;
}

function candleBodyWidth(barSpacing: number): number {
  // ~70% of the slot, at least 1px, capped so huge zoom keeps a gap.
  return Math.max(1, Math.min(Math.round(barSpacing * 0.7), barSpacing - 2));
}

export type SceneInput = {
  width: number;
  height: number;
  bars: readonly EngineBar[];
  vp: Viewport;
  colors: EngineColors;
  style: ChartStyle;
  intraday: boolean;
  fontFamily: string;
};

/** Paint the full scene (grid, series, volume, axes, last-price line). */
export function drawScene(p: Painter, input: SceneInput): Scene {
  const { width, height, bars, vp, colors, style, intraday, fontFamily } = input;
  const layout = computeLayout(width, height);
  const { pane, price, volume, priceAxis, timeAxis } = layout;
  const font = axisFont(fontFamily);

  p.clear();
  p.fillRect(0, 0, width, height, colors.background);

  const priceRange = autoPriceRange(bars, vp, pane.w, style);
  const pTicks = priceTicks(priceRange, price.h);
  const tTicks = timeTicks(bars, vp, pane.w, intraday);

  // Grid under everything else.
  for (const t of pTicks) {
    const y = yOf(t.price, priceRange, price.y, price.h);
    p.line(pane.x, y, pane.x + pane.w, y, { color: colors.grid, width: 1 });
  }
  for (const t of tTicks) {
    if (t.x < pane.x || t.x > pane.x + pane.w) continue;
    p.line(t.x, pane.y, t.x, pane.y + pane.h, { color: colors.grid, width: 1 });
  }

  const range = visibleBarRange(vp, pane.w, bars.length);

  // Volume strip (own hidden 0..max scale).
  if (range && volume.h > 0) {
    let vmax = 0;
    for (let i = range.from; i <= range.to; i++) {
      if (bars[i].volume > vmax) vmax = bars[i].volume;
    }
    if (vmax > 0) {
      const bodyW = candleBodyWidth(vp.barSpacing);
      p.withClip(volume.x, volume.y, volume.w, volume.h, () => {
        for (let i = range.from; i <= range.to; i++) {
          const b = bars[i];
          if (b.volume <= 0) continue;
          const x = xOf(i, vp, pane.w);
          const h = Math.max(1, (b.volume / vmax) * volume.h);
          const prevClose = i > 0 ? bars[i - 1].close : b.open;
          const color = b.close >= prevClose ? colors.volumeUp : colors.volumeDown;
          p.fillRect(x - bodyW / 2, volume.y + volume.h - h, bodyW, h, color);
        }
      });
    }
  }

  // Main series.
  if (range) {
    p.withClip(price.x, price.y, price.w, price.h, () => {
      if (style === "line") {
        const points: { x: number; y: number }[] = [];
        for (let i = range.from; i <= range.to; i++) {
          points.push({
            x: xOf(i, vp, pane.w),
            y: yOf(bars[i].close, priceRange, price.y, price.h),
          });
        }
        p.polyline(points, { color: colors.accent, width: 2 });
      } else {
        const bodyW = candleBodyWidth(vp.barSpacing);
        for (let i = range.from; i <= range.to; i++) {
          const b = bars[i];
          const x = xOf(i, vp, pane.w);
          const color = b.close >= b.open ? colors.up : colors.down;
          const yHigh = yOf(b.high, priceRange, price.y, price.h);
          const yLow = yOf(b.low, priceRange, price.y, price.h);
          p.line(x, yHigh, x, yLow, { color, width: 1 });
          if (bodyW > 1) {
            const yO = yOf(b.open, priceRange, price.y, price.h);
            const yC = yOf(b.close, priceRange, price.y, price.h);
            const top = Math.min(yO, yC);
            p.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, Math.abs(yC - yO)), color);
          }
        }
      }
    });
  }

  // Last-price line + axis chip (colored by the forming bar's direction).
  const last = bars[bars.length - 1];
  if (last) {
    const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : last.open;
    const color = last.close >= prevClose ? colors.up : colors.down;
    const y = yOf(last.close, priceRange, price.y, price.h);
    if (y >= price.y && y <= price.y + price.h) {
      p.withClip(pane.x, pane.y, pane.w, pane.h, () => {
        p.line(pane.x, y, pane.x + pane.w, y, { color, width: 1, dash: [2, 2] });
      });
    }
  }

  // Price axis: border, tick labels, then the last-price chip on top.
  p.fillRect(priceAxis.x, 0, priceAxis.w, height, colors.background);
  p.line(priceAxis.x, 0, priceAxis.x, priceAxis.y + priceAxis.h, {
    color: colors.grid,
    width: 1,
  });
  for (const t of pTicks) {
    const y = yOf(t.price, priceRange, price.y, price.h);
    p.text(t.label, priceAxis.x + LABEL_PAD, y, {
      color: colors.textDim,
      font,
      align: "left",
      baseline: "middle",
    });
  }
  if (last) {
    const prevClose = bars.length >= 2 ? bars[bars.length - 2].close : last.open;
    const color = last.close >= prevClose ? colors.up : colors.down;
    const y = yOf(last.close, priceRange, price.y, price.h);
    if (y >= price.y - CHIP_H && y <= priceAxis.y + priceAxis.h + CHIP_H) {
      const label = formatChipPrice(last.close, pTicks);
      p.fillRect(priceAxis.x, y - CHIP_H / 2, priceAxis.w, CHIP_H, color);
      p.text(label, priceAxis.x + LABEL_PAD, y, {
        color: colors.background,
        font,
        align: "left",
        baseline: "middle",
      });
    }
  }

  // Time axis: border + labels centered on their bars.
  p.line(timeAxis.x, timeAxis.y, timeAxis.x + timeAxis.w, timeAxis.y, {
    color: colors.grid,
    width: 1,
  });
  const midY = timeAxis.y + timeAxis.h / 2;
  for (const t of tTicks) {
    const halfW = p.measure(t.label, font) / 2;
    if (t.x - halfW < 0 || t.x + halfW > pane.w) continue;
    p.text(t.label, t.x, midY, {
      color: t.major ? colors.text : colors.textDim,
      font,
      align: "center",
      baseline: "middle",
    });
  }

  return { layout, priceRange, pTicks, tTicks };
}

/** Price chip text — same decimal count the axis ticks use. */
function formatChipPrice(price: number, pTicks: PriceTick[]): string {
  const decimals = pTicks[0]?.label.split(".")[1]?.length ?? 2;
  return price.toFixed(decimals);
}

export type CursorInput = {
  /** Logical px within the whole chart element. */
  x: number;
  y: number;
  bars: readonly EngineBar[];
  vp: Viewport;
  scene: Scene;
  colors: EngineColors;
  intraday: boolean;
  fontFamily: string;
};

export type CursorHit = { index: number; bar: EngineBar } | null;

/**
 * Paint the crosshair layer: vertical line snapped to the hovered bar, free
 * horizontal line, price + time chips. Returns the hovered bar (or null) so
 * the engine can feed the legend callback.
 */
export function drawCursor(p: Painter, input: CursorInput): CursorHit {
  const { x, y, bars, vp, scene, colors, intraday, fontFamily } = input;
  const { pane, price, priceAxis, timeAxis } = scene.layout;
  const font = axisFont(fontFamily);

  p.clear();
  if (x < pane.x || x > pane.x + pane.w || y < pane.y || y > pane.y + pane.h) {
    return null;
  }

  // Snap the vertical line to the nearest real bar under the cursor.
  const i = Math.round(indexAt(x, vp, pane.w));
  const hit: CursorHit =
    i >= 0 && i < bars.length ? { index: i, bar: bars[i] } : null;
  const vx = hit ? xOf(i, vp, pane.w) : x;

  const crossStyle = { color: colors.crosshair, width: 1, dash: [3, 3] };
  if (vx >= pane.x && vx <= pane.x + pane.w) {
    p.line(vx, pane.y, vx, pane.y + pane.h, crossStyle);
  }

  // Horizontal line + price chip only inside the price area — over the volume
  // strip the price scale doesn't apply and priceAt would extrapolate garbage.
  if (y >= price.y && y <= price.y + price.h) {
    p.line(pane.x, y, pane.x + pane.w, y, crossStyle);
    const hoverPrice = priceAt(y, scene.priceRange, price.y, price.h);
    const priceLabel = formatChipPrice(hoverPrice, scene.pTicks);
    p.fillRect(priceAxis.x, y - CHIP_H / 2, priceAxis.w, CHIP_H, colors.crosshairLabelBg);
    p.text(priceLabel, priceAxis.x + LABEL_PAD, y, {
      color: colors.text,
      font,
      align: "left",
      baseline: "middle",
    });
  }

  // Time chip under the snapped bar.
  if (hit) {
    const label = crosshairTimeLabel(hit.bar.time, intraday);
    const w = p.measure(label, font) + CHIP_PAD_X * 2;
    const cx = Math.min(Math.max(vx - w / 2, 0), timeAxis.w - w);
    const cy = timeAxis.y + timeAxis.h / 2;
    p.fillRect(cx, timeAxis.y, w, timeAxis.h, colors.crosshairLabelBg);
    p.text(label, cx + w / 2, cy, {
      color: colors.text,
      font,
      align: "center",
      baseline: "middle",
    });
  }

  return hit;
}
