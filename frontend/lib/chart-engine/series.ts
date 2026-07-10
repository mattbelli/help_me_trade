/**
 * Scene + cursor drawing — pure geometry to Painter calls. No DOM: everything
 * here runs identically under a future RN-Skia painter. The engine (DOM side)
 * computes nothing visual itself; it hands this module the pane size, data,
 * viewport, and colors and gets pixels.
 */

import type { Painter } from "./painter";
import {
  autoPriceRange,
  indexAt,
  indexOfTime,
  priceAt,
  visibleBarRange,
  xOf,
  yOf,
} from "./scales";
import { crosshairTimeLabel, priceTicks, timeTicks, type PriceTick, type TimeTick } from "./ticks";
import {
  PRICE_AXIS_WIDTH,
  TAG_GUTTER_WIDTH,
  TIME_AXIS_HEIGHT,
  VOLUME_FRACTION,
  type ChartStyle,
  type EngineBar,
  type EngineColors,
  type EngineOverlays,
  type OverlayLine,
  type OverlaySegment,
  type PriceRange,
  type Rect,
  type Viewport,
} from "./types";

export type Layout = {
  /** Full chart pane (price + volume), excluding the gutter and both axes. */
  pane: Rect;
  /** Candle/line area — pane minus the volume strip. */
  price: Rect;
  /** Volume strip along the pane bottom. */
  volume: Rect;
  /** Tag gutter between pane and price axis (level names / bells / ext). */
  gutter: Rect;
  priceAxis: Rect;
  timeAxis: Rect;
};

export function computeLayout(
  width: number,
  height: number,
  gutterW: number = TAG_GUTTER_WIDTH,
): Layout {
  const paneW = Math.max(0, width - PRICE_AXIS_WIDTH - gutterW);
  const paneH = Math.max(0, height - TIME_AXIS_HEIGHT);
  const volumeH = Math.round(paneH * VOLUME_FRACTION);
  return {
    pane: { x: 0, y: 0, w: paneW, h: paneH },
    price: { x: 0, y: 0, w: paneW, h: paneH - volumeH },
    volume: { x: 0, y: paneH - volumeH, w: paneW, h: volumeH },
    gutter: { x: paneW, y: 0, w: gutterW, h: paneH },
    priceAxis: { x: paneW + gutterW, y: 0, w: PRICE_AXIS_WIDTH, h: paneH },
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
/** Gutter tag containers — full axis font (the smaller pt "wasn't helping"). */
const LEVEL_TAG_H = 14;
const LEVEL_TAG_FONT_PX = 10;

function axisFont(fontFamily: string): string {
  return `${AXIS_FONT_PX}px ${fontFamily}`;
}

/**
 * Price-axis chip: full axis width with centered text — and the tick labels
 * are centered on the same axis midline, so chip digits and prices align
 * (founder call: liked the full-width container; centering everything is what
 * keeps it aligned across wide and narrow numbers). The y is snapped to a
 * whole logical px so box and text move in lockstep.
 */
function drawAxisChip(
  p: Painter,
  axis: Rect,
  y: number,
  label: string,
  bg: string,
  textColor: string,
  font: string,
): void {
  const cy = Math.round(y);
  p.fillRect(axis.x, cy - CHIP_H / 2, axis.w, CHIP_H, bg);
  // Right-aligned to the shared price column (ticks + card prices match).
  p.text(label, axis.x + axis.w - 6, cy, {
    color: textColor,
    font,
    align: "right",
    baseline: "middle",
  });
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
  /** Manual price scale (axis drag). Null/undefined = autoscale. */
  priceOverride?: PriceRange | null;
  /** Ext-hours quote to mark with a dashed line + axis chip. Null = hidden. */
  extPrice?: number | null;
  /** Alert/level/drawing primitives, colors pre-resolved by the wrapper. */
  overlays?: EngineOverlays | null;
  /** False collapses the tag gutter — pane runs straight to the price axis. */
  tagGutter?: boolean;
};

/** Paint the full scene (grid, series, volume, axes, last-price line). */
export function drawScene(p: Painter, input: SceneInput): Scene {
  const { width, height, bars, vp, colors, style, intraday, fontFamily } = input;
  const layout = computeLayout(
    width,
    height,
    input.tagGutter === false ? 0 : TAG_GUTTER_WIDTH,
  );
  const { pane, price, volume, gutter, priceAxis, timeAxis } = layout;
  const font = axisFont(fontFamily);
  // Price-anchored lines run through the gutter to the axis; candles don't.
  const plotEnd = priceAxis.x;

  p.clear();
  p.fillRect(0, 0, width, height, colors.background);

  const priceRange =
    input.priceOverride ?? autoPriceRange(bars, vp, pane.w, style);
  const pTicks = priceTicks(priceRange, price.h);
  const tTicks = timeTicks(bars, vp, pane.w, intraday);

  // Grid under everything else.
  for (const t of pTicks) {
    const y = yOf(t.price, priceRange, price.y, price.h);
    p.line(pane.x, y, plotEnd, y, { color: colors.grid, width: 1 });
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

  // Overlays: alert/level lines + drawing segments, over the series but under
  // the last-price line. Out-of-range lines simply don't draw (hide, never
  // clamp — cross-client convention, RN_SKIA_CHART_RELAY.md §2).
  // Line tags render TV-style: name+price cards anchored to the axis edge,
  // floating over the pane's right edge, de-overlapped vertically. Collected
  // here, drawn near the end so lines flow underneath.
  const cards: {
    y: number;
    label: string;
    price: number | null;
    bg: string;
    text: string;
  }[] = [];

  if (input.overlays) {
    const { lines, segments } = input.overlays;
    p.withClip(price.x, price.y, plotEnd - price.x, price.h, () => {
      const collapsible: LinePlacement[] = [];
      const drawLine = (l: OverlayLine, y: number): void => {
        p.line(pane.x, y, plotEnd, y, {
          color: l.color,
          width: 1,
          dash: l.dash,
          hairline: true,
        });
        if (l.label) {
          cards.push({
            y,
            label: l.label,
            // Alert bells stay price-less (founder killed TV's alert pills);
            // level cards carry the price like TV's.
            price: l.collapsible ? l.price : null,
            bg: l.color,
            text: "#ffffff",
          });
        }
      };
      for (const l of lines) {
        const y = yOf(l.price, priceRange, price.y, price.h);
        if (y < price.y || y > price.y + price.h) continue;
        if (l.collapsible) {
          collapsible.push({ line: l, y });
          continue;
        }
        drawLine(l, y);
      }
      // Crowded auto-levels merge into one representative "node" line —
      // founder call: 5-6 smooshed vol nodes on a daily chart are noise, not
      // information. Zooming in separates them again (pixel-space clustering).
      for (const group of clusterLines(collapsible, LEVEL_CLUSTER_PX)) {
        if (group.length === 1) {
          drawLine(group[0].line, group[0].y);
          continue;
        }
        const y = (group[0].y + group[group.length - 1].y) / 2;
        const mid =
          group.reduce((s, g) => s + g.line.price, 0) / group.length;
        p.line(pane.x, y, plotEnd, y, {
          color: colors.textDim,
          width: 1,
          dash: [2, 3],
          hairline: true,
        });
        cards.push({
          y,
          label: `×${group.length}`,
          price: mid,
          bg: colors.crosshairLabelBg,
          text: colors.text,
        });
      }
      for (const s of segments) {
        const x1 = xOf(indexOfTime(bars, s.a.time), vp, pane.w);
        const x2 = xOf(indexOfTime(bars, s.b.time), vp, pane.w);
        const y1 = yOf(s.a.price, priceRange, price.y, price.h);
        const y2 = yOf(s.b.price, priceRange, price.y, price.h);
        p.polyline(
          [
            { x: x1, y: y1 },
            { x: x2, y: y2 },
          ],
          { color: s.color, width: 1 },
        );
        if (s.parallelOffset != null) {
          const o1 = yOf(s.a.price + s.parallelOffset, priceRange, price.y, price.h);
          const o2 = yOf(s.b.price + s.parallelOffset, priceRange, price.y, price.h);
          p.polyline(
            [
              { x: x1, y: o1 },
              { x: x2, y: o2 },
            ],
            { color: s.color, width: 1 },
          );
          if (s.draggable) {
            // Channel width handle at the parallel leg's midpoint.
            p.circle((x1 + x2) / 2, (o1 + o2) / 2, HANDLE_R, { fill: s.color });
          }
        }
        if (s.draggable) {
          p.circle(x1, y1, HANDLE_R, { fill: s.color });
          p.circle(x2, y2, HANDLE_R, { fill: s.color });
        }
      }
    });
  }

  // Last-price line + axis chip — colored by the forming bar's OWN open→close
  // direction (matches the candle and TV's convention; close-vs-prev-close
  // disagreed with both and read as "chip green, candle red").
  const last = bars[bars.length - 1];
  const lastColor = last
    ? last.close >= last.open
      ? colors.up
      : colors.down
    : colors.accent;
  if (last) {
    const y = yOf(last.close, priceRange, price.y, price.h);
    if (y >= price.y && y <= price.y + price.h) {
      p.withClip(pane.x, pane.y, plotEnd - pane.x, pane.h, () => {
        p.line(pane.x, y, plotEnd, y, {
          color: lastColor,
          width: 1,
          dash: [2, 2],
          hairline: true,
        });
      });
    }
  }

  // Price axis: border, tick labels, then the last-price chip on top.
  p.fillRect(priceAxis.x, 0, priceAxis.w, height, colors.background);
  p.line(priceAxis.x, 0, priceAxis.x, priceAxis.y + priceAxis.h, {
    color: colors.grid,
    width: 1,
  });
  // All prices right-align to one column — ticks, card prices, chip — so the
  // digits line up exactly (founder call).
  for (const t of pTicks) {
    const y = yOf(t.price, priceRange, price.y, price.h);
    p.text(t.label, priceAxis.x + priceAxis.w - 6, y, {
      color: colors.textDim,
      font,
      align: "right",
      baseline: "middle",
    });
  }
  // Extended-hours price (pre/post/overnight quote): dashed accent line with
  // an "ext" tag and its own axis chip — parity with TV, which keeps showing
  // where the name trades after the (daily or RTH-only) candles stop.
  // Skipped when it duplicates the last price — on ETH charts the forming bar
  // already tracks the ext quote, so the card was a permanent double (founder:
  // "ext needs to come out"). Daily/RTH charts still show it (close ≠ ext).
  const extDistinct =
    input.extPrice != null &&
    (!last || Math.abs(input.extPrice - last.close) > 0.005);
  if (input.extPrice != null && extDistinct) {
    const y = yOf(input.extPrice, priceRange, price.y, price.h);
    if (y >= price.y && y <= price.y + price.h) {
      p.withClip(pane.x, pane.y, plotEnd - pane.x, pane.h, () => {
        p.line(pane.x, y, plotEnd, y, {
          color: colors.accent,
          width: 1,
          dash: [4, 3],
          hairline: true,
        });
      });
      // Ext rides in the SAME card stack as the levels (TV behavior) — its
      // old standalone axis chip collided with level prices around the close.
      cards.push({
        y,
        label: "ext",
        price: input.extPrice,
        bg: colors.crosshairLabelBg,
        text: colors.text,
      });
    }
  }

  // Level cards LAST (only the live-price chip outranks them): TV-exact —
  // name left, price right, anchored to the chart's right edge spanning the
  // axis, nudged apart vertically when they'd collide, over an opaque wipe so
  // the lines flow underneath.
  if (input.tagGutter !== false && cards.length > 0) {
    const tagFont = `${LEVEL_TAG_FONT_PX}px ${fontFamily}`;
    const step = LEVEL_TAG_H + 1;
    const minY = pane.y + LEVEL_TAG_H / 2;
    const maxY = pane.y + pane.h - LEVEL_TAG_H / 2;
    // Forward pass pushes colliding cards down, backward pass pulls the
    // overflow back up — minimal displacement, price order preserved.
    const sweep = (group: typeof cards, lo: number, hi: number): number[] => {
      const ys: number[] = [];
      for (let i = 0; i < group.length; i++) {
        const desired = Math.max(group[i].y, lo);
        ys.push(i === 0 ? desired : Math.max(desired, ys[i - 1] + step));
      }
      for (let i = group.length - 1; i >= 0; i--) {
        const bound = i === group.length - 1 ? hi : ys[i + 1] - step;
        if (ys[i] > bound) ys[i] = bound;
      }
      return ys;
    };
    // The live-price chip is a fixed obstacle (TV behavior): cards split
    // around it instead of sliding underneath. Whole-px so a sub-pixel tick
    // can't reflow the stack (the "articulating" cards).
    const chipY =
      last != null
        ? Math.round(yOf(last.close, priceRange, price.y, price.h))
        : null;
    const chipVisible =
      chipY != null && chipY >= price.y && chipY <= price.y + price.h;
    const all = [...cards].sort((a, b) => a.y - b.y);
    let sorted: typeof cards;
    let ys: number[];
    if (chipVisible) {
      const above = all.filter((c) => c.y <= chipY);
      const below = all.filter((c) => c.y > chipY);
      const gap = CHIP_H / 2 + LEVEL_TAG_H / 2 + 1;
      sorted = [...above, ...below];
      ys = [
        ...sweep(above, minY, chipY - gap),
        ...sweep(below, chipY + gap, maxY),
      ];
    } else {
      sorted = all;
      ys = sweep(all, minY, maxY);
    }
    // TV pill anatomy: the price part lives INSIDE the column (right-aligned
    // with the ticks), the name slides OUT to the left of the border attached
    // to it, and the column's border line subtly splits the pill at the seam.
    const borderX = priceAxis.x;
    for (let i = 0; i < sorted.length; i++) {
      const card = sorted[i];
      const priceLabel =
        card.price != null ? formatChipPrice(card.price, pTicks) : null;
      const nameW = p.measure(card.label, tagFont);
      const leftX = borderX - nameW - 10;
      const rightX = priceLabel ? width - 1 : borderX;
      const cy = Math.round(ys[i]);
      const ty = cy - LEVEL_TAG_H / 2;
      const textY = cy + LEVEL_TAG_FONT_PX * 0.34; // optical centering
      p.fillRect(leftX, ty, rightX - leftX, LEVEL_TAG_H, colors.background);
      p.roundRect(leftX, ty, rightX - leftX, LEVEL_TAG_H, 2, card.bg);
      p.text(card.label, borderX - 5, textY, {
        color: card.text,
        font: tagFont,
        align: "right",
        baseline: "alphabetic",
      });
      if (priceLabel) {
        p.text(priceLabel, priceAxis.x + priceAxis.w - 6, textY, {
          color: card.text,
          font: tagFont,
          align: "right",
          baseline: "alphabetic",
        });
        // The column border cuts through the pill — TV's subtle name|price split.
        p.line(borderX, ty, borderX, ty + LEVEL_TAG_H, {
          color: colors.background,
          width: 1,
          hairline: true,
        });
      }
    }
  }

  // Live-price chip absolutely last — it outranks every card.
  if (last) {
    const y = yOf(last.close, priceRange, price.y, price.h);
    if (y >= price.y - CHIP_H && y <= priceAxis.y + priceAxis.h + CHIP_H) {
      // Always white text: the chip bg is a saturated up/down color on every
      // theme, and theme-background text disappeared on light themes.
      drawAxisChip(
        p,
        priceAxis,
        y,
        formatChipPrice(last.close, pTicks),
        lastColor,
        "#ffffff",
        font,
      );
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

/** Hit tolerance around an overlay line, logical px (matches TV's LEVEL_HIT_PX). */
export const LINE_HIT_PX = 6;
/** Collapsible levels closer than this (px) merge into one "node" line. */
export const LEVEL_CLUSTER_PX = 12;

export type LinePlacement = { line: OverlayLine; y: number };

/** Group placements into runs where each adjacent pixel gap is under `gapPx`. */
export function clusterLines(
  placed: readonly LinePlacement[],
  gapPx: number,
): LinePlacement[][] {
  const sorted = [...placed].sort((a, b) => a.y - b.y);
  const groups: LinePlacement[][] = [];
  for (const item of sorted) {
    const current = groups[groups.length - 1];
    if (current && item.y - current[current.length - 1].y < gapPx) {
      current.push(item);
    } else {
      groups.push([item]);
    }
  }
  return groups;
}

/**
 * Nearest identifiable overlay line within `tolerance` px of y. Only lines
 * inside the price area count — an out-of-range (hidden) line isn't hittable.
 */
export function hitTestLine(
  lines: readonly OverlayLine[],
  y: number,
  scene: Scene,
  tolerance: number,
  draggableOnly: boolean,
): OverlayLine | null {
  const pr = scene.layout.price;
  if (y < pr.y || y > pr.y + pr.h) return null;
  let best: OverlayLine | null = null;
  let bestDist = tolerance + 1;
  for (const l of lines) {
    if (!l.id || (draggableOnly && !l.draggable)) continue;
    const ly = yOf(l.price, scene.priceRange, pr.y, pr.h);
    if (ly < pr.y || ly > pr.y + pr.h) continue;
    const d = Math.abs(ly - y);
    if (d <= tolerance && d < bestDist) {
      bestDist = d;
      best = l;
    }
  }
  return best;
}

/** Hit tolerance around a segment's grab handles (matches TV's HANDLE_HIT_PX). */
export const HANDLE_HIT_PX = 9;
/** Drawn radius of a segment's grab handles. */
const HANDLE_R = 3;

function distToSegment(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

/** A segment's base-leg pixel endpoints under the current view. */
export function segmentXY(
  seg: OverlaySegment,
  bars: readonly EngineBar[],
  vp: Viewport,
  scene: Scene,
): { x1: number; y1: number; x2: number; y2: number } {
  const { pane, price } = scene.layout;
  return {
    x1: xOf(indexOfTime(bars, seg.a.time), vp, pane.w),
    y1: yOf(seg.a.price, scene.priceRange, price.y, price.h),
    x2: xOf(indexOfTime(bars, seg.b.time), vp, pane.w),
    y2: yOf(seg.b.price, scene.priceRange, price.y, price.h),
  };
}

export type SegmentHit = {
  seg: OverlaySegment;
  /** a/b = endpoints; width = channel parallel-leg handle; body = the stroke. */
  part: "a" | "b" | "width" | "body";
};

/**
 * Segment under the point: endpoint / width handles first (HANDLE_HIT_PX),
 * then the stroke (tolerance). Only identifiable segments count.
 */
export function hitTestSegment(
  segments: readonly OverlaySegment[],
  x: number,
  y: number,
  bars: readonly EngineBar[],
  vp: Viewport,
  scene: Scene,
  tolerance: number,
  draggableOnly: boolean,
): SegmentHit | null {
  // Segments are clipped to the price area — pixels that aren't drawn
  // (volume strip) aren't hittable.
  const pr = scene.layout.price;
  if (y < pr.y || y > pr.y + pr.h) return null;
  let best: SegmentHit | null = null;
  let bestDist = Infinity;
  const consider = (seg: OverlaySegment, part: SegmentHit["part"], d: number, tol: number) => {
    // Handles get a HANDLE_HIT_PX head start over body grabs — endpoints sit
    // ON the stroke, and a grab that lands "on the line near the endpoint"
    // should move the endpoint, not pan-block on the body.
    const rank = part === "body" ? d + HANDLE_HIT_PX : d;
    if (d <= tol && rank < bestDist) {
      bestDist = rank;
      best = { seg, part };
    }
  };
  for (const seg of segments) {
    if (!seg.id || (draggableOnly && !seg.draggable)) continue;
    const g = segmentXY(seg, bars, vp, scene);
    if (seg.draggable) {
      consider(seg, "a", Math.hypot(x - g.x1, y - g.y1), HANDLE_HIT_PX);
      consider(seg, "b", Math.hypot(x - g.x2, y - g.y2), HANDLE_HIT_PX);
    }
    consider(seg, "body", distToSegment(x, y, g.x1, g.y1, g.x2, g.y2), tolerance);
    // != null: a zero-width channel must keep its (midline) width handle, or
    // there's no way to drag the width back open.
    if (seg.parallelOffset != null) {
      const { price } = scene.layout;
      const o1 = yOf(seg.a.price + seg.parallelOffset, scene.priceRange, price.y, price.h);
      const o2 = yOf(seg.b.price + seg.parallelOffset, scene.priceRange, price.y, price.h);
      if (seg.draggable) {
        const mx = (g.x1 + g.x2) / 2;
        const my = (o1 + o2) / 2;
        consider(seg, "width", Math.hypot(x - mx, y - my), HANDLE_HIT_PX);
      }
      consider(seg, "body", distToSegment(x, y, g.x1, o1, g.x2, o2), tolerance);
    }
  }
  return best;
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
  // The crosshair lives over the whole plot area, tag gutter included.
  const plotEnd = priceAxis.x;

  p.clear();
  if (x < pane.x || x > plotEnd || y < pane.y || y > pane.y + pane.h) {
    return null;
  }

  // Snap the vertical line to the nearest real bar under the cursor.
  const i = Math.round(indexAt(x, vp, pane.w));
  const hit: CursorHit =
    i >= 0 && i < bars.length ? { index: i, bar: bars[i] } : null;
  const vx = hit ? xOf(i, vp, pane.w) : x;

  const crossStyle = { color: colors.crosshair, width: 1, dash: [3, 3] };
  if (vx >= pane.x && vx <= plotEnd) {
    p.line(vx, pane.y, vx, pane.y + pane.h, crossStyle);
  }

  // Horizontal line + price chip only inside the price area — over the volume
  // strip the price scale doesn't apply and priceAt would extrapolate garbage.
  if (y >= price.y && y <= price.y + price.h) {
    p.line(pane.x, y, plotEnd, y, crossStyle);
    const hoverPrice = priceAt(y, scene.priceRange, price.y, price.h);
    drawAxisChip(
      p,
      priceAxis,
      y,
      formatChipPrice(hoverPrice, scene.pTicks),
      colors.crosshairLabelBg,
      colors.text,
      font,
    );
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
