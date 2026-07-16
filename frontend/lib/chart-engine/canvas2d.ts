/**
 * DOM adapter: Canvas2DPainter + the two-layer canvas stack. The ONLY file
 * (with engine.ts) allowed to touch the DOM. Owns devicePixelRatio: core code
 * draws in logical px; this adapter sizes bitmaps at dpr and pixel-snaps
 * axis-aligned strokes so 1px lines land on physical pixel rows.
 */

import type { CircleStyle, Painter, StrokeStyle, TextStyle } from "./painter";

export class Canvas2DPainter implements Painter {
  private width = 0;
  private height = 0;
  private dpr = 1;
  private currentFont = "";

  constructor(private readonly ctx: CanvasRenderingContext2D) {}

  /** Called by the layer stack on resize — logical size + dpr. */
  setSize(width: number, height: number, dpr: number): void {
    this.width = width;
    this.height = height;
    this.dpr = dpr;
    this.currentFont = ""; // canvas resize resets context state
  }

  /**
   * Snap a coordinate so a stroke of `deviceWidth` physical px sits crisply on
   * the pixel grid: odd widths center on a half-pixel, even on a boundary.
   */
  private snap(v: number, deviceWidth: number): number {
    const d = v * this.dpr;
    return deviceWidth % 2 === 1
      ? (Math.floor(d) + 0.5) / this.dpr
      : Math.round(d) / this.dpr;
  }

  private setFont(font: string): void {
    if (font !== this.currentFont) {
      this.ctx.font = font;
      this.currentFont = font;
    }
  }

  clear(): void {
    this.ctx.clearRect(0, 0, this.width, this.height);
  }

  fillRect(x: number, y: number, w: number, h: number, color: string): void {
    const { ctx, dpr } = this;
    // Normalize negative extents (drag-built rects) so the ≥1-device-px floor
    // below applies to them too.
    if (w < 0) {
      x += w;
      w = -w;
    }
    if (h < 0) {
      y += h;
      h = -h;
    }
    ctx.fillStyle = color;
    // Snap edges to device pixels; keep at least 1 device px in each dimension.
    const x0 = Math.round(x * dpr) / dpr;
    const y0 = Math.round(y * dpr) / dpr;
    let x1 = Math.round((x + w) * dpr) / dpr;
    let y1 = Math.round((y + h) * dpr) / dpr;
    if (w > 0 && x1 <= x0) x1 = x0 + 1 / dpr;
    if (h > 0 && y1 <= y0) y1 = y0 + 1 / dpr;
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
  }

  line(x1: number, y1: number, x2: number, y2: number, style: StrokeStyle): void {
    const { ctx } = this;
    const deviceWidth = style.hairline
      ? 1
      : Math.max(1, Math.round(style.width * this.dpr));
    ctx.strokeStyle = style.color;
    ctx.lineWidth = deviceWidth / this.dpr;
    ctx.setLineDash(style.dash ?? []);
    let ax1 = x1;
    let ay1 = y1;
    let ax2 = x2;
    let ay2 = y2;
    if (x1 === x2) {
      ax1 = ax2 = this.snap(x1, deviceWidth);
    }
    if (y1 === y2) {
      ay1 = ay2 = this.snap(y1, deviceWidth);
    }
    ctx.beginPath();
    ctx.moveTo(ax1, ay1);
    ctx.lineTo(ax2, ay2);
    ctx.stroke();
    if (style.dash) ctx.setLineDash([]);
  }

  polyline(points: readonly { x: number; y: number }[], style: StrokeStyle): void {
    if (points.length < 2) return;
    const { ctx } = this;
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.width;
    ctx.lineJoin = "round";
    ctx.setLineDash(style.dash ?? []);
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
    ctx.stroke();
    if (style.dash) ctx.setLineDash([]);
  }

  roundRect(x: number, y: number, w: number, h: number, r: number, color: string): void {
    const { ctx } = this;
    ctx.fillStyle = color;
    ctx.beginPath();
    if (typeof ctx.roundRect === "function") {
      ctx.roundRect(x, y, w, h, r);
    } else {
      ctx.rect(x, y, w, h); // ancient-browser fallback: square corners
    }
    ctx.fill();
  }

  circle(x: number, y: number, r: number, style: CircleStyle): void {
    const { ctx } = this;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    if (style.fill) {
      ctx.fillStyle = style.fill;
      ctx.fill();
    }
    if (style.stroke) {
      ctx.strokeStyle = style.stroke;
      ctx.lineWidth = style.strokeWidth ?? 1;
      ctx.stroke();
    }
  }

  text(s: string, x: number, y: number, style: TextStyle): void {
    const { ctx } = this;
    this.setFont(style.font);
    ctx.fillStyle = style.color;
    ctx.textAlign = style.align ?? "left";
    ctx.textBaseline = style.baseline ?? "top";
    ctx.fillText(s, x, y);
  }

  measure(s: string, font: string): number {
    this.setFont(font);
    return this.ctx.measureText(s).width;
  }

  withClip(x: number, y: number, w: number, h: number, fn: () => void): void {
    const { ctx } = this;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    fn();
    ctx.restore();
    this.currentFont = ""; // restore() may have reverted the font
  }
}

/** The two stacked canvases (scene under, cursor over) inside a container. */
export class CanvasLayers {
  readonly scene: Canvas2DPainter;
  readonly cursor: Canvas2DPainter;
  private readonly canvases: HTMLCanvasElement[];

  constructor(container: HTMLElement) {
    const make = () => {
      const c = document.createElement("canvas");
      c.style.position = "absolute";
      c.style.inset = "0";
      c.style.width = "100%";
      c.style.height = "100%";
      container.appendChild(c);
      return c;
    };
    const sceneCanvas = make();
    const cursorCanvas = make();
    this.canvases = [sceneCanvas, cursorCanvas];
    this.scene = new Canvas2DPainter(sceneCanvas.getContext("2d")!);
    this.cursor = new Canvas2DPainter(cursorCanvas.getContext("2d")!);
  }

  /** Size both bitmaps at `dpr` and scale their contexts to logical px. */
  resize(width: number, height: number, dpr: number): void {
    for (const canvas of this.canvases) {
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      const ctx = canvas.getContext("2d")!;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    this.scene.setSize(width, height, dpr);
    this.cursor.setSize(width, height, dpr);
  }

  destroy(): void {
    for (const canvas of this.canvases) canvas.remove();
  }
}
