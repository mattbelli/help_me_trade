/**
 * Painter — the ONLY way the chart engine core puts pixels anywhere.
 *
 * Pure interface: no DOM, no react-native. Adapters implement it per platform
 * (canvas2d.ts for web now; an RN-Skia painter is the parked Phase-4 path that
 * lets the phone charts share this core). If the core needs something this
 * can't express, the interface grows — core code never reaches around it.
 *
 * Coordinates are logical (CSS) pixels; the adapter owns devicePixelRatio and
 * pixel-snapping of strokes (see CHART_ENGINE.md → Coordinate system).
 */

export type StrokeStyle = {
  color: string;
  width: number;
  /** Dash pattern in logical px, e.g. [2, 2]. Omit for solid. */
  dash?: number[];
  /** Exactly 1 device px regardless of dpr — TV-fine dotted level lines. */
  hairline?: boolean;
};

export type TextStyle = {
  color: string;
  /** Full CSS font shorthand, e.g. `10px Inter, sans-serif`. */
  font: string;
  align?: "left" | "center" | "right";
  /** `alphabetic` + a cap-height offset = optical centering ("middle" centers
      the em box, which sits uppercase labels visibly low in small chips). */
  baseline?: "top" | "middle" | "bottom" | "alphabetic";
};

export type CircleStyle = {
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
};

/**
 * One bar slot snapped to the device-pixel grid. Body and wick share ONE
 * centre by construction and the body carries the wick's parity, so the wick
 * always has a middle column to sit in. Both rects are logical px whose edges
 * land exactly on device pixels.
 */
export type BarColumn = {
  bodyX: number;
  bodyW: number;
  wickX: number;
  /** Also the engine's minimum stroke thickness (1 logical px in whole device px). */
  wickW: number;
};

export interface Painter {
  /** Clear the whole layer to transparent. */
  clear(): void;
  fillRect(x: number, y: number, w: number, h: number, color: string): void;
  /**
   * Body + wick rects for a bar centred at logical `x` in a `barSpacing`-wide
   * slot. Every per-bar column (candle, volume, MACD histogram) must come from
   * here: rounding each rect independently is what made wicks lean left on one
   * candle and right on the next, and body widths alternate within one frame.
   * The adapter owns the width policy — it is a device-pixel decision.
   */
  barColumn(x: number, barSpacing: number): BarColumn;
  /** fillRect with rounded corners (no pixel snapping — antialiasing is the point). */
  roundRect(x: number, y: number, w: number, h: number, r: number, color: string): void;
  line(x1: number, y1: number, x2: number, y2: number, style: StrokeStyle): void;
  polyline(points: readonly { x: number; y: number }[], style: StrokeStyle): void;
  circle(x: number, y: number, r: number, style: CircleStyle): void;
  text(s: string, x: number, y: number, style: TextStyle): void;
  /** Logical-px width of `s` in `font`. */
  measure(s: string, font: string): number;
  /** Run `fn` with drawing clipped to the rect. */
  withClip(x: number, y: number, w: number, h: number, fn: () => void): void;
}
