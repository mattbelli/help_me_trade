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

// --- Overlays (Phase 2) --------------------------------------------------------------
// The engine renders two generic primitives; the wrapper composes price
// alerts, session-node levels, and saved drawings into them with resolved
// colors. Anchors are data-space {time (epoch ms), price} per the
// cross-client contract (RN_SKIA_CHART_RELAY.md §5).

/** Horizontal line across the pane at a price. */
export type OverlayLine = {
  price: number;
  color: string;
  /** Dash pattern in logical px; omit for solid. */
  dash?: number[];
  /** Small tag drawn above the line at the pane's left edge. */
  label?: string;
  /** Opaque wrapper id — present ⇒ the line is hit-testable (context menu). */
  id?: string;
  /** Drag-to-move: the engine previews the move and fires onLineDragEnd. */
  draggable?: boolean;
};

/** Data-space anchor: epoch ms + price. */
export type DrawPoint = { time: number; price: number };

/** Segment between two data-space anchors (trendline / channel leg). */
export type OverlaySegment = {
  a: DrawPoint;
  b: DrawPoint;
  color: string;
  /** Channel: draws a second leg offset by this many price units. */
  parallelOffset?: number;
  /** Opaque wrapper id — present ⇒ hit-testable (context menu). */
  id?: string;
  /** Endpoint (+ channel width-handle) drag-editing; handles are drawn. */
  draggable?: boolean;
};

export type EngineOverlays = {
  lines: OverlayLine[];
  segments: OverlaySegment[];
};

/** Right-click hit info. `price` is null outside the price area (volume strip). */
export type ContextMenuHit = {
  x: number;
  y: number;
  price: number | null;
  /** Id of the overlay line under the cursor, if any. */
  lineId: string | null;
  /** That line's exact price — the hit tolerance means it can differ from `price`. */
  linePrice: number | null;
  /** Id of the overlay segment under the cursor (lines win when both hit). */
  segmentId: string | null;
};

export type EngineOptions = {
  colors: EngineColors;
  style: ChartStyle;
  /** Intraday bars label time-of-day; daily+ label dates. */
  intraday: boolean;
  /** CSS font family (sizes are the engine's own). */
  fontFamily: string;
  onCrosshair?: (bar: EngineBar | null) => void;
  /** Right-click in the pane (the engine suppresses the browser menu). */
  onContextMenu?: (hit: ContextMenuHit) => void;
  /** A draggable overlay line was dropped at a new price. */
  onLineDragEnd?: (id: string, price: number) => void;
  /** Two-click draw finished (mode set via setDrawMode); anchors in data-space. */
  onDrawComplete?: (kind: string, a: DrawPoint, b: DrawPoint) => void;
  /** A draggable segment was reshaped (endpoint or channel width-handle drag). */
  onSegmentDragEnd?: (
    id: string,
    seg: { a: DrawPoint; b: DrawPoint; parallelOffset?: number },
  ) => void;
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
