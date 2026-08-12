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
  indexAt,
  priceAt,
  scaleRangeAround,
  timeOfIndex,
  zoomAt,
} from "./scales";
import {
  drawCursor,
  drawScene,
  hitTestLine,
  hitTestSegment,
  LINE_HIT_PX,
  segmentXY,
  type Scene,
} from "./series";
import {
  DEFAULT_BAR_SPACING,
  DEFAULT_RIGHT_OFFSET,
  PRICE_AXIS_WIDTH,
  TAG_GUTTER_WIDTH,
  TIME_AXIS_HEIGHT,
  type ChartStyle,
  type DrawPoint,
  type EngineBar,
  type EngineColors,
  type EngineOptions,
  type EngineOverlays,
  type EngineStudies,
  type OverlayLine,
  type OverlaySegment,
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
  private readonly onCrosshair?: EngineOptions["onCrosshair"];
  private readonly onContextMenu?: EngineOptions["onContextMenu"];
  private readonly onLineDragEnd?: EngineOptions["onLineDragEnd"];
  private readonly onDrawComplete?: EngineOptions["onDrawComplete"];
  private readonly onSegmentDragEnd?: EngineOptions["onSegmentDragEnd"];

  private width = 0;
  private height = 0;
  /** Raw container box (fractional) — re-snapped to the grid whenever dpr moves. */
  private boxWidth = 0;
  private boxHeight = 0;
  private dpr = 1;
  private readonly layers: CanvasLayers;
  private readonly ro: ResizeObserver;
  private readonly aborter = new AbortController();

  private dirtyScene = true;
  private dirtyCursor = false;
  private raf: number | null = null;
  private cross: { x: number; y: number } | null = null;
  private drag: {
    startX: number;
    startRe: number;
    startY: number;
    /** Manual price scale at drag start — non-null ⇒ drag also pans vertically. */
    startRange: PriceRange | null;
  } | null = null;
  /** Axis-drag scaling in progress (price or time axis). */
  private axisDrag: {
    kind: "price" | "time";
    start: number;
    startRange?: PriceRange;
    startSpacing?: number;
  } | null = null;
  /** Drag-to-move on a draggable overlay line (`line` is mutated as preview). */
  private lineDrag: { id: string; line: OverlayLine; startPrice: number } | null =
    null;
  /** Endpoint / channel-width drag on a segment (`seg` is mutated as preview). */
  private segDrag: {
    id: string;
    seg: OverlaySegment;
    part: "a" | "b" | "width";
    startA: DrawPoint;
    startB: DrawPoint;
    startOffset?: number;
  } | null = null;
  /** Two-click draw in progress (setDrawMode). `a` set after the first click. */
  private drawMode: { kind: string; a: DrawPoint | null } | null = null;
  /** Two fast draw clicks also fire dblclick — this keeps it from resetView. */
  private lastDrawClickMs = 0;
  /** Manual price scale from an axis drag — sticky until dblclick on the axis. */
  private priceOverride: PriceRange | null = null;
  /** Ext-hours quote line (null = hidden) — the wrapper owns the session gating. */
  private extPrice: number | null = null;
  /** Tag gutter (level names / bells / ext) between pane and price axis. */
  private tagGutter = true;
  /** Alert/level/drawing primitives (wrapper-composed, colors resolved). */
  private overlays: EngineOverlays | null = null;
  /** Indicator series (wrapper-computed values + colors). */
  private studies: EngineStudies | null = null;
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
    this.onContextMenu = options.onContextMenu;
    this.onLineDragEnd = options.onLineDragEnd;
    this.onDrawComplete = options.onDrawComplete;
    this.onSegmentDragEnd = options.onSegmentDragEnd;
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
    container.addEventListener("contextmenu", this.onContextMenuEvent, opts);
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
    this.bars.push(rolledOverBar(last, intervalMs, price));
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

  /** Replace the overlay set (alerts/levels/drawings). Null clears. */
  setOverlays(overlays: EngineOverlays | null): void {
    // A poll-driven recompose mid-drag must not snap the dragged primitive
    // back to its stored shape — carry the preview onto the replacement.
    if (this.lineDrag) {
      const match = overlays?.lines.find((l) => l.id === this.lineDrag!.id);
      if (match) {
        match.price = this.lineDrag.line.price;
        this.lineDrag.line = match;
      } else {
        this.lineDrag = null; // deleted mid-drag
      }
    }
    if (this.segDrag) {
      const match = overlays?.segments.find((s) => s.id === this.segDrag!.id);
      if (match) {
        match.a = this.segDrag.seg.a;
        match.b = this.segDrag.seg.b;
        match.parallelOffset = this.segDrag.seg.parallelOffset;
        this.segDrag.seg = match;
      } else {
        this.segDrag = null;
      }
    }
    this.overlays = overlays;
    this.invalidate("scene");
  }

  /**
   * Enter (kind, e.g. "trendline"/"channel") or leave (null) two-click draw
   * mode. The kind is opaque — it rides back out on onDrawComplete.
   */
  setDrawMode(kind: string | null): void {
    this.drawMode = kind ? { kind, a: null } : null;
    this.container.style.cursor = "crosshair";
    this.invalidate("cursor"); // clear/show any pending preview
  }

  /** Replace the study set (MA overlays + oscillator panes). Null clears. */
  setStudies(studies: EngineStudies | null): void {
    this.studies = studies;
    this.invalidate("scene");
  }

  /** Show/hide the tag gutter — hiding gives the pane back to the candles. */
  setTagGutter(show: boolean): void {
    if (show === this.tagGutter) return;
    this.tagGutter = show;
    this.vp = clampViewport(this.vp, this.paneWidth(), this.bars.length);
    this.invalidate("both");
  }

  /** Show/refresh (number) or hide (null) the ext-hours price line. */
  setExtPrice(price: number | null): void {
    const next = price != null && Number.isFinite(price) && price > 0 ? price : null;
    if (next === this.extPrice) return;
    this.extPrice = next;
    this.invalidate("scene");
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

  /** Which interaction region a local point falls in (gutter counts as pane). */
  private regionAt(x: number, y: number): "pane" | "priceAxis" | "timeAxis" {
    if (y >= this.height - TIME_AXIS_HEIGHT) return "timeAxis";
    if (x >= this.width - PRICE_AXIS_WIDTH) return "priceAxis";
    return "pane";
  }

  private onPointerDown = (e: PointerEvent): void => {
    if (e.button !== 0 || this.bars.length === 0) return;
    this.container.setPointerCapture(e.pointerId);
    const { x, y } = this.localPoint(e);
    if (this.drawMode) {
      // Any click while drawing goes to the draw — never an axis-scale drag
      // (placing a point near "now" is right where the price axis begins).
      this.handleDrawClick(x, y);
      return;
    }
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
      // A draggable overlay line under the pointer beats a pan; then a
      // segment's grab handles (body strokes pan — endpoints/width move).
      const hit =
        this.scene && this.overlays
          ? hitTestLine(this.overlays.lines, y, this.scene, LINE_HIT_PX, true)
          : null;
      const segHit =
        !hit && this.scene && this.overlays
          ? hitTestSegment(
              this.overlays.segments,
              x,
              y,
              this.bars,
              this.vp,
              this.scene,
              LINE_HIT_PX,
              true,
            )
          : null;
      if (hit?.id) {
        this.lineDrag = { id: hit.id, line: hit, startPrice: hit.price };
      } else if (segHit && segHit.part !== "body" && segHit.seg.id) {
        this.segDrag = {
          id: segHit.seg.id,
          seg: segHit.seg,
          part: segHit.part,
          startA: { ...segHit.seg.a },
          startB: { ...segHit.seg.b },
          startOffset: segHit.seg.parallelOffset,
        };
      } else {
        this.drag = {
          startX: x,
          startRe: this.vp.rightEdgeIndex,
          startY: y,
          // Under a manual scale the drag pans BOTH axes (an axis-zoom can
          // push the candles out of frame — this is the way back). Autoscale
          // keeps vertical framing itself, so there the drag stays horizontal.
          startRange: this.priceOverride ? { ...this.priceOverride } : null,
        };
      }
    }
  };

  /** Two-click draw: first click anchors, second commits via onDrawComplete. */
  private handleDrawClick(x: number, y: number): void {
    if (!this.drawMode || !this.scene || this.bars.length === 0) return;
    this.lastDrawClickMs = performance.now();
    const pr = this.scene.layout.price;
    if (y < pr.y || y > pr.y + pr.h) return; // volume strip doesn't anchor
    const point: DrawPoint = {
      time: timeOfIndex(this.bars, indexAt(x, this.vp, this.paneWidth())),
      price: priceAt(y, this.scene.priceRange, pr.y, pr.h),
    };
    if (!this.drawMode.a) {
      this.drawMode.a = point;
      this.invalidate("cursor");
      return;
    }
    const { kind, a } = this.drawMode;
    this.drawMode = null;
    this.invalidate("cursor"); // drop the preview
    this.onDrawComplete?.(kind, a, point);
  }

  private onPointerMove = (e: PointerEvent): void => {
    const { x, y } = this.localPoint(e);
    if (this.lineDrag && this.scene) {
      // Preview: move the line to the pointer, clamped to the price area.
      const pr = this.scene.layout.price;
      const yc = Math.min(Math.max(y, pr.y), pr.y + pr.h);
      this.lineDrag.line.price = priceAt(yc, this.scene.priceRange, pr.y, pr.h);
      this.container.style.cursor = "ns-resize";
      this.cross = { x, y };
      this.invalidate("both");
      return;
    }
    if (this.segDrag && this.scene) {
      const pr = this.scene.layout.price;
      const yc = Math.min(Math.max(y, pr.y), pr.y + pr.h);
      const priceHere = priceAt(yc, this.scene.priceRange, pr.y, pr.h);
      const seg = this.segDrag.seg;
      if (this.segDrag.part === "width") {
        // TV convention: width = price at the handle minus the base midline.
        seg.parallelOffset = priceHere - (seg.a.price + seg.b.price) / 2;
      } else {
        const pt: DrawPoint = {
          time: timeOfIndex(this.bars, indexAt(x, this.vp, this.paneWidth())),
          price: priceHere,
        };
        if (this.segDrag.part === "a") seg.a = pt;
        else seg.b = pt;
      }
      this.container.style.cursor = "move";
      this.cross = { x, y };
      this.invalidate("both");
      return;
    }
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
      if (this.drag.startRange && this.scene) {
        const pr = this.scene.layout.price;
        if (pr.h > 0) {
          // Content follows the pointer: drag down → window shifts up-price.
          const span = this.drag.startRange.max - this.drag.startRange.min;
          const shift = ((y - this.drag.startY) * span) / pr.h;
          this.priceOverride = {
            min: this.drag.startRange.min + shift,
            max: this.drag.startRange.max + shift,
          };
        }
      }
      this.invalidate("scene");
    } else {
      const region = this.regionAt(x, y);
      let cursor =
        region === "priceAxis"
          ? "ns-resize"
          : region === "timeAxis"
            ? "ew-resize"
            : "crosshair";
      if (region === "pane" && !this.drawMode && this.scene && this.overlays) {
        if (hitTestLine(this.overlays.lines, y, this.scene, LINE_HIT_PX, true)) {
          cursor = "ns-resize"; // grabbable alert line
        } else {
          const segHit = hitTestSegment(
            this.overlays.segments,
            x,
            y,
            this.bars,
            this.vp,
            this.scene,
            LINE_HIT_PX,
            true,
          );
          if (segHit && segHit.part !== "body") cursor = "move"; // grab handle
        }
      }
      this.container.style.cursor = cursor;
    }
    this.cross = { x, y };
    this.invalidate("cursor");
  };

  private onPointerUp = (e: PointerEvent): void => {
    if (this.container.hasPointerCapture(e.pointerId)) {
      this.container.releasePointerCapture(e.pointerId);
    }
    if (this.lineDrag) {
      const { id, line, startPrice } = this.lineDrag;
      this.lineDrag = null;
      if (e.type === "pointercancel") {
        line.price = startPrice;
        this.invalidate("scene");
      } else if (line.price !== startPrice) {
        this.onLineDragEnd?.(id, line.price);
      }
    }
    if (this.segDrag) {
      const { id, seg, startA, startB, startOffset } = this.segDrag;
      this.segDrag = null;
      const moved =
        seg.a.time !== startA.time ||
        seg.a.price !== startA.price ||
        seg.b.time !== startB.time ||
        seg.b.price !== startB.price ||
        seg.parallelOffset !== startOffset;
      if (e.type === "pointercancel") {
        this.revertSegment(seg, startA, startB, startOffset);
      } else if (moved) {
        this.onSegmentDragEnd?.(id, {
          a: seg.a,
          b: seg.b,
          parallelOffset: seg.parallelOffset,
        });
      }
    }
    this.drag = null;
    this.axisDrag = null;
  };

  private revertSegment(
    seg: OverlaySegment,
    a: DrawPoint,
    b: DrawPoint,
    parallelOffset?: number,
  ): void {
    seg.a = a;
    seg.b = b;
    seg.parallelOffset = parallelOffset;
    this.invalidate("scene");
  }

  private onPointerLeave = (): void => {
    if (this.lineDrag) {
      // Left the chart mid-drag: revert the preview rather than half-commit.
      this.lineDrag.line.price = this.lineDrag.startPrice;
      this.lineDrag = null;
      this.invalidate("scene");
    }
    if (this.segDrag) {
      const { seg, startA, startB, startOffset } = this.segDrag;
      this.segDrag = null;
      this.revertSegment(seg, startA, startB, startOffset);
    }
    this.drag = null;
    this.axisDrag = null;
    this.cross = null;
    this.invalidate("cursor");
  };

  private onContextMenuEvent = (e: MouseEvent): void => {
    if (this.drawMode) {
      // Right-click backs out of draw mode instead of opening the menu.
      e.preventDefault();
      this.setDrawMode(null);
      return;
    }
    if (!this.onContextMenu || !this.scene || this.bars.length === 0) return;
    const { x, y } = this.localPoint(e);
    if (this.regionAt(x, y) !== "pane") return;
    const pr = this.scene.layout.price;
    const inPrice = y >= pr.y && y <= pr.y + pr.h;
    const hit = this.overlays
      ? hitTestLine(this.overlays.lines, y, this.scene, LINE_HIT_PX, false)
      : null;
    const segHit =
      !hit && this.overlays
        ? hitTestSegment(
            this.overlays.segments,
            x,
            y,
            this.bars,
            this.vp,
            this.scene,
            LINE_HIT_PX,
            false,
          )
        : null;
    // No actionable target here (an oscillator sub-pane or the volume strip,
    // no overlay line/segment under the cursor) — let the browser's own menu
    // through rather than eating the right-click into a dead zone.
    if (!inPrice && !hit && !segHit) return;
    e.preventDefault(); // the chart owns the right-click where it has an action
    this.onContextMenu({
      x,
      y,
      price: inPrice ? priceAt(y, this.scene.priceRange, pr.y, pr.h) : null,
      lineId: hit?.id ?? null,
      linePrice: hit?.price ?? null,
      segmentId: segHit?.seg.id ?? null,
    });
  };

  private onDblClick = (e: MouseEvent): void => {
    // Two rapid draw-mode clicks read as a dblclick — never resetView then.
    if (this.drawMode || performance.now() - this.lastDrawClickMs < 500) return;
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
    return Math.max(
      0,
      this.width - PRICE_AXIS_WIDTH - (this.tagGutter ? TAG_GUTTER_WIDTH : 0),
    );
  }

  private resize(width: number, height: number): void {
    if (width <= 0 || height <= 0) return;
    this.boxWidth = width;
    this.boxHeight = height;
    this.applySize();
  }

  /**
   * Adopt the container box at the current dpr, rounded so the logical size is
   * a WHOLE number of device pixels. ResizeObserver hands us fractional boxes;
   * left alone, the bitmap (rounded) and the CSS box (fractional) disagree and
   * the browser resamples the entire canvas — a position-dependent sub-pixel
   * blur that undoes every snap the painter does.
   */
  private applySize(): void {
    this.dpr = window.devicePixelRatio || 1;
    this.width = Math.round(this.boxWidth * this.dpr) / this.dpr;
    this.height = Math.round(this.boxHeight * this.dpr) / this.dpr;
    this.layers.resize(this.width, this.height, this.dpr);
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

    // A dpr change (browser zoom / monitor move) re-sizes bitmaps in place —
    // from the RAW box, since the old logical size was snapped to the old grid.
    if ((window.devicePixelRatio || 1) !== this.dpr) {
      this.applySize();
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
        extPrice: this.extPrice,
        overlays: this.overlays,
        tagGutter: this.tagGutter,
        studies: this.studies,
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
      // Two-click draw preview: rubber-band from the anchored point to the
      // pointer (cursor layer — it moves every frame, the scene doesn't).
      if (this.cross && this.drawMode?.a && this.scene) {
        const g = segmentXY(
          { a: this.drawMode.a, b: this.drawMode.a, color: "" },
          this.bars,
          this.vp,
          this.scene,
        );
        const pr = this.scene.layout.price;
        const cy = Math.min(Math.max(this.cross.y, pr.y), pr.y + pr.h);
        const p = this.layers.cursor;
        p.line(g.x1, g.y1, this.cross.x, cy, {
          color: this.colors.accent,
          width: 1,
          dash: [4, 3],
        });
        p.circle(g.x1, g.y1, 3, { fill: this.colors.accent });
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
