/**
 * Chart engine core types — pure data. No DOM, no React (see CHART_ENGINE.md;
 * the headless-core rule is what keeps the phone-sharing door open).
 */

export type EngineBar = {
  /** Epoch milliseconds (UTC instant of the bar open). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ChartStyle = "candle" | "line";

/** Resolved concrete colors — the React wrapper reads CSS vars and hands us hex. */
export type EngineColors = {
  background: string;
  text: string;
  textDim: string;
  grid: string;
  up: string;
  down: string;
  accent: string;
  crosshair: string;
  crosshairLabelBg: string;
  volumeUp: string;
  volumeDown: string;
};

/** Rect in logical px — panes, axes, and clip regions all use this. */
export type Rect = { x: number; y: number; w: number; h: number };

/**
 * Viewport over the bar-index domain (see CHART_ENGINE.md → Time scale).
 * Bar centers sit at integer indices; `rightEdgeIndex` is the fractional index
 * at the pane's right edge, so whitespace right of the last bar is just
 * `rightEdgeIndex > lastIndex`.
 */
export type Viewport = {
  /** Logical px per bar. */
  barSpacing: number;
  /** Fractional bar index at the pane's right edge. */
  rightEdgeIndex: number;
};

export type PriceRange = { min: number; max: number };

export type EngineOptions = {
  colors: EngineColors;
  style: ChartStyle;
  /** Intraday bars label time-of-day; daily+ label dates. */
  intraday: boolean;
  /** CSS font family (sizes are the engine's own). */
  fontFamily: string;
  onCrosshair?: (bar: EngineBar | null) => void;
};

// --- Layout constants (CHART_ENGINE.md → Panes & layout) ---------------------------
export const PRICE_AXIS_WIDTH = 64;
export const TIME_AXIS_HEIGHT = 22;
/** Fraction of the pane (above the time axis) given to the volume strip. */
export const VOLUME_FRACTION = 0.16;

// --- Viewport constants (CHART_ENGINE.md → Time scale) -----------------------------
export const MIN_BAR_SPACING = 0.5;
export const MAX_BAR_SPACING = 60;
export const DEFAULT_BAR_SPACING = 7;
/** Bars of whitespace kept right of the last bar in the default view. */
export const DEFAULT_RIGHT_OFFSET = 5;
/** At least this many bars stay on screen when panning/zooming. */
export const MIN_VISIBLE_BARS = 2;

// --- Scale constants ----------------------------------------------------------------
/** Autoscale pad above/below the visible high/low, as a fraction of the range. */
export const AUTOSCALE_PAD = 0.08;
/** Target logical px between price gridlines. */
export const PRICE_TICK_TARGET_PX = 50;
/** Target logical px between time labels. */
export const TIME_TICK_TARGET_PX = 90;
