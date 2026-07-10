/**
 * ChartEngine — the DOM orchestrator: pointer/wheel/resize events in, core
 * math + Painter calls out (CHART_ENGINE.md → Layers & repaint discipline).
 *
 * Repaint contract: event handlers only set dirty flags; ONE rAF callback
 * paints whichever layers are dirty. Mouse-move dirties only the cursor layer
 * — that is the 60fps mechanism. `?chartPerf=1` overlays measured frame times.
 */

import { CanvasLayers } from "./canvas2d";
import { applyLive, rolledOverBar } from "./merge";
import {
  autoPriceRange,
  clampViewport,
  defaultViewport,
  scaleRangeAround,
  zoomAt,
} from "./scales";
import { drawCursor, drawScene, type Scene } from "./series";
import {
  DEFAULT_BAR_SPACING,
  DEFAULT_RIGHT_OFFSET,
  PRICE_AXIS_WIDTH,
  TIME_AXIS_HEIGHT,
  type ChartStyle,
  type EngineBar,
  type EngineColors,
  type EngineOptions,
  type PriceRange,
  type Viewport,
} from "./types";

const WHEEL_ZOOM_RATE = 0.0018;
/** Logical px of axis drag per e-fold of scale change. */
const AXIS_SCALE_RATE = 0.005;

export class ChartEngine {
  private bars: EngineBar[] = [];
  private vp: Viewport = { barSpacing: DEFAULT_BAR_SPACING, rightEdgeIndex: 0 };
  private scene: Scene | null = null;
  private colors: EngineColors;
  private style: ChartStyle;
  private intraday: boolean;
  private readonly fontFamily: string;
  private readonly onCrosshair?: (bar: EngineBar | null) => void;

  private width = 0;
  private height = 0;
  private dpr = 1;
  private readonly layers: CanvasLayers;
  private readonly ro: ResizeObserver;
  private readonly aborter = new AbortController();

  private dirtyScene = true;
  private dirtyCursor = false;
  private raf: number | null = null;
  private cross: { x: number; y: number } | null = null;
  private drag: { startX: number; startRe: number } | null = null;
  /** Axis-drag scaling in progress (price or time axis). */
  private axisDrag: {
    kind: "price" | "time";
    start: number;
    startRange?: PriceRange;
    startSpacing?: number;
  } | null = null;
  /** Manual price scale from an axis drag — sticky until dblclick on the axis. */
  private priceOverride: PriceRange | null = null;
  private lastHoverIndex: number | null = null;
  // Live ticks mutate the hovered bar in place without moving the cursor —
  // the legend callback must re-fire on value change, not just index change.
  private lastHoverTime: number | null = null;
  private lastHoverClose: number | null = null;

  private readonly perfEnabled: boolean;
  private perfSceneMs = 0;
  private perfCursorMs = 0;

  constructor(private readonly container: HTMLElement, options: EngineOptions) {
    this.colors = options.colors;
    this.style = options.style;
    this.intraday = options.intraday;
    this.fontFamily = options.fontFamily;
    this.onCrosshair = options.onCrosshair;
    this.perfEnabled =
      typeof location !== "undefined" && /[?&]chartPerf=1/.test(location.search);

    container.style.cursor = "crosshair";
    container.style.touchAction = "none";
    this.layers = new CanvasLayers(container);

    this.ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      this.resize(rect.width, rect.height);
    });
    this.ro.observe(container);
    const rect = container.getBoundingClientRect();
    this.resize(rect.width, rect.height);

    const opts = { signal: this.aborter.signal };
    container.addEventListener("pointerdown", this.onPointerDown, opts);
    container.addEventListener("pointermove", this.onPointerMove, opts);
    container.addEventListener("pointerup", this.onPointerUp, opts);
    container.addEventListener("pointercancel", this.onPointerUp, opts);
    container.addEventListener("pointerleave", this.onPointerLeave, opts);
    container.addEventListener("dblclick", this.onDblClick, opts);
    container.addEventListener("wheel", this.onWheel, {
      ...opts,
      passive: false,
    });
  }

  // --- public API --------------------------------------------------------------------

  /**
   * Replace history. `anchorRight` (symbol/timeframe switch) keeps the zoom
   * but re-anchors to the latest bar; otherwise a view sitting at the right
   * edge follows new data and a panned-back view stays put.
   */
  setBars(bars: EngineBar[], opts?: { anchorRight?: boolean }): void {
    const prevLast = this.bars.length - 1;
    const wasAtRightEdge =
      this.bars.length === 0 || this.vp.rightEdgeIndex >= prevLast;
    const hadBars = this.bars.length > 0;
    this.bars = bars;
    const lastIndex = bars.length - 1;

    if (bars.length === 0) {
      this.invalidate("scene");
      return;
    }
    if (!hadBars) {
      this.vp = defaultViewport(bars.length, this.paneWidth());
      this.priceOverride = null;
    } else if (opts?.anchorRight) {
      // Symbol/timeframe switch: a manual price scale from the old series
      // would frame the new one absurdly — return to autoscale.
      this.priceOverride = null;
      this.vp = clampViewport(
        {
          barSpacing: this.vp.barSpacing,
          rightEdgeIndex: lastIndex + DEFAULT_RIGHT_OFFSET,
        },
        this.paneWidth(),
        bars.length,
      );
    } else if (wasAtRightEdge) {
      const offset = Math.max(0, this.vp.rightEdgeIndex - prevLast);
      this.vp = clampViewport(
        { barSpacing: this.vp.barSpacing, rightEdgeIndex: lastIndex + offset },
        this.paneWidth(),
        bars.length,
      );
    } else {
      this.vp = clampViewport(this.vp, this.paneWidth(), bars.length);
    }
    this.invalidate("scene");
  }

  /** Merge a live price into the forming bar (mutates only the last bar). */
  applyLive(price: number): void {
    if (applyLive(this.bars, price)) this.invalidate("scene");
  }

  /**
   * Single-step bar rollover: open the next interval's forming bar at `price`.
   * A right-edge view follows the new bar; gaps are the wrapper's refetch
   * problem — this never fabricates skipped buckets.
   */
  rollover(intervalMs: number, price: number): void {
    const last = this.bars[this.bars.length - 1];
    if (!last || !Number.isFinite(price) || price <= 0) return;
    const atRightEdge = this.vp.rightEdgeIndex >= this.bars.length - 1;
    this.bars.push(rolledOverBar(last.time, intervalMs, price));
    if (atRightEdge) {
      this.vp = clampViewport(
        { barSpacing: this.vp.barSpacing, rightEdgeIndex: this.vp.rightEdgeIndex + 1 },
        this.paneWidth(),
        this.bars.length,
      );
    }
    this.invalidate("scene");
  }

  setColors(colors: EngineColors): void {
    this.colors = colors;
    this.invalidate("both");
  }

  setStyle(style: ChartStyle): void {
    if (style === this.style) return;
    this.style = style;
    this.invalidate("scene");
  }

  resetView(): void {
    this.vp = defaultViewport(this.bars.length, this.paneWidth());
    this.priceOverride = null;
    this.invalidate("scene");
  }

  destroy(): void {
    this.aborter.abort();
    this.ro.disconnect();
    if (this.raf != null) cancelAnimationFrame(this.raf);
    this.layers.destroy();
  }

  // --- events ------------------------------------------------------------------------

  private localPoint(e: MouseEvent): { x: number; y: number } {
    const rect = this.container.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  /** Which interaction region a local point falls in. */
  private regionAt(x: number, y: number): "pane" | "priceAxis" | "timeAxis" {
    if (y >= this.height - TIME_AXIS_HEIGHT) return "timeAxis";
    if (x >= this.paneWidth()) return "priceAxis";
    return "pane";
  }

  private onPointerDown = (e: PointerEvent): void => {
    if (e.button !== 0 || this.bars.length === 0) return;
    this.container.setPointerCapture(e.pointerId);
    const { x, y } = this.localPoint(e);
    const region = this.regionAt(x, y);
    if (region === "priceAxis") {
      // Grab the scale as it currently reads (auto or manual) and go manual.
      const start =
        this.priceOverride ??
        autoPriceRange(this.bars, this.vp, this.paneWidth(), this.style);
      this.axisDrag = { kind: "price", start: y, startRange: start };
    } else if (region === "timeAxis") {
      this.axisDrag = { kind: "time", start: x, startSpacing: this.vp.barSpacing };
    } else {
      this.drag = { startX: x, startRe: this.vp.rightEdgeIndex };
    }
  };

  private onPointerMove = (e: PointerEvent): void => {
    const { x, y } = this.localPoint(e);
    if (this.axisDrag && this.bars.length > 0) {
      if (this.axisDrag.kind === "price") {
        // Drag down expands the range (zoom out), up contracts — TV feel.
        const factor = Math.exp((y - this.axisDrag.start) * AXIS_SCALE_RATE);
        this.priceOverride = scaleRangeAround(this.axisDrag.startRange!, factor);
      } else {
        // Drag right widens bars (zoom in), anchored at the pane's right edge.
        const factor = Math.exp((x - this.axisDrag.start) * AXIS_SCALE_RATE);
        this.vp = clampViewport(
          {
            barSpacing: this.axisDrag.startSpacing! * factor,
            rightEdgeIndex: this.vp.rightEdgeIndex,
          },
          this.paneWidth(),
          this.bars.length,
        );
      }
      this.invalidate("scene");
      return; // no crosshair while scaling an axis
    }
    if (this.drag && this.bars.length > 0) {
      // Recompute from the drag origin (not incrementally) — no drift.
      this.vp = clampViewport(
        {
          barSpacing: this.vp.barSpacing,
          rightEdgeIndex:
            this.drag.startRe - (x - this.drag.startX) / this.vp.barSpacing,
        },
        this.paneWidth(),
        this.bars.length,
      );
      this.invalidate("scene");
    } else {
      const region = this.regionAt(x, y);
      this.container.style.cursor =
        region === "priceAxis"
          ? "ns-resize"
          : region === "timeAxis"
            ? "ew-resize"
            : "crosshair";
    }
    this.cross = { x, y };
    this.invalidate("cursor");
  };

  private onPointerUp = (e: PointerEvent): void => {
    if (this.container.hasPointerCapture(e.pointerId)) {
      this.container.releasePointerCapture(e.pointerId);
    }
    this.drag = null;
    this.axisDrag = null;
  };

  private onPointerLeave = (): void => {
    this.drag = null;
    this.axisDrag = null;
    this.cross = null;
    this.invalidate("cursor");
  };

  private onDblClick = (e: MouseEvent): void => {
    const { x, y } = this.localPoint(e);
    if (this.regionAt(x, y) === "priceAxis") {
      // Manual scale → back to autoscale; the view itself stays put.
      this.priceOverride = null;
      this.invalidate("scene");
      return;
    }
    this.resetView();
  };

  private onWheel = (e: WheelEvent): void => {
    if (this.bars.length === 0) return;
    e.preventDefault();
    const { x } = this.localPoint(e);
    const factor = Math.exp(-e.deltaY * WHEEL_ZOOM_RATE);
    this.vp = zoomAt(this.vp, x, factor, this.paneWidth(), this.bars.length);
    this.invalidate("scene");
  };

  // --- repaint -----------------------------------------------------------------------

  private paneWidth(): number {
    return Math.max(0, this.width - PRICE_AXIS_WIDTH);
  }

  private resize(width: number, height: number): void {
    if (width <= 0 || height <= 0) return;
    this.width = width;
    this.height = height;
    this.dpr = window.devicePixelRatio || 1;
    this.layers.resize(width, height, this.dpr);
    this.vp = clampViewport(this.vp, this.paneWidth(), this.bars.length);
    this.invalidate("both");
  }

  private invalidate(layer: "scene" | "cursor" | "both"): void {
    if (layer !== "cursor") this.dirtyScene = true;
    if (layer !== "scene") this.dirtyCursor = true;
    if (this.raf == null) {
      this.raf = requestAnimationFrame(this.paint);
    }
  }

  private paint = (): void => {
    this.raf = null;
    if (this.width <= 0 || this.height <= 0) return;

    // A dpr change (browser zoom / monitor move) re-sizes bitmaps in place.
    const dpr = window.devicePixelRatio || 1;
    if (dpr !== this.dpr) {
      this.dpr = dpr;
      this.layers.resize(this.width, this.height, dpr);
      this.dirtyScene = true;
      this.dirtyCursor = true;
    }

    if (this.dirtyScene) {
      const t0 = performance.now();
      this.scene = drawScene(this.layers.scene, {
        width: this.width,
        height: this.height,
        bars: this.bars,
        vp: this.vp,
        colors: this.colors,
        style: this.style,
        intraday: this.intraday,
        fontFamily: this.fontFamily,
        priceOverride: this.priceOverride,
      });
      this.perfSceneMs = performance.now() - t0;
      // The crosshair sits over repositioned content — keep it in sync.
      this.dirtyCursor = true;
      this.dirtyScene = false;
    }

    if (this.dirtyCursor) {
      const t0 = performance.now();
      let hoverIndex: number | null = null;
      if (this.cross && this.scene) {
        const hit = drawCursor(this.layers.cursor, {
          x: this.cross.x,
          y: this.cross.y,
          bars: this.bars,
          vp: this.vp,
          scene: this.scene,
          colors: this.colors,
          intraday: this.intraday,
          fontFamily: this.fontFamily,
        });
        hoverIndex = hit?.index ?? null;
      } else {
        this.layers.cursor.clear();
      }
      this.perfCursorMs = performance.now() - t0;
      const hoverBar = hoverIndex == null ? null : this.bars[hoverIndex];
      if (
        hoverIndex !== this.lastHoverIndex ||
        (hoverBar?.time ?? null) !== this.lastHoverTime ||
        (hoverBar?.close ?? null) !== this.lastHoverClose
      ) {
        this.lastHoverIndex = hoverIndex;
        this.lastHoverTime = hoverBar?.time ?? null;
        this.lastHoverClose = hoverBar?.close ?? null;
        this.onCrosshair?.(hoverBar);
      }
      if (this.perfEnabled) this.drawPerfOverlay();
      this.dirtyCursor = false;
    }
  };

  private drawPerfOverlay(): void {
    const p = this.layers.cursor;
    const label = `scene ${this.perfSceneMs.toFixed(2)}ms · cursor ${this.perfCursorMs.toFixed(2)}ms`;
    const font = `10px ${this.fontFamily}`;
    const w = p.measure(label, font) + 12;
    p.fillRect(4, 4, w, 16, this.colors.crosshairLabelBg);
    p.text(label, 10, 12, {
      color: this.colors.text,
      font,
      align: "left",
      baseline: "middle",
    });
  }
}
