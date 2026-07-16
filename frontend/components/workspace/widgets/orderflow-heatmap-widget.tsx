"use client";

/**
 * orderflow-heatmap-widget.tsx
 *
 * Workspace widget: Liquidity Heatmap.
 *
 * Reads from the session store: callId, book, meta, viewMs (synced from the
 * footprint widget's pan/zoom so the heatmap stays temporally aligned).
 *
 * Canvas drawing uses colors sourced from the design tokens (read once via
 * getComputedStyle at draw time so themes work). The React chrome uses only
 * CSS custom property references — no hardcoded hex.
 */

import { useCallback, useEffect, useRef } from "react";
import type { BookSnapshot, OrderFlowMeta } from "@/lib/orderflow-api";
import { useOrderFlowSession } from "@/lib/orderflow/use-orderflow-session";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

// ── Canvas constants ──────────────────────────────────────────────────────────

const PRICE_W = 60;   // price axis width (px) — slightly narrower than footprint
const TICK    = 0.25;

// ── Coordinate helpers (mirror footprint canvas) ──────────────────────────────

type ViewMs = { start: number; end: number };

function msToX(ms: number, v: ViewMs, w: number): number {
  const span = v.end - v.start || 1;
  return PRICE_W + ((ms - v.start) / span) * (w - PRICE_W);
}

function barPxWidth(v: ViewMs, w: number, barMs: number): number {
  if (v.end <= v.start) return 2;
  return Math.max(1.5, (barMs / (v.end - v.start)) * (w - PRICE_W));
}

function priceToY(
  price: number,
  pMin: number,
  pMax: number,
  h: number,
  pad = 0.06,
): number {
  const span = pMax - pMin || 1;
  const lo   = pMin - span * pad;
  const hi   = pMax + span * pad;
  return h - ((price - lo) / (hi - lo)) * h;
}

// ── Design-token color reader (cached per draw call) ─────────────────────────

function readToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ── Heatmap draw function ─────────────────────────────────────────────────────

function drawHeatmapCanvas(
  canvas: HTMLCanvasElement,
  w: number,
  h: number,
  snapshots: BookSnapshot[],
  meta: OrderFlowMeta,
  view: ViewMs,
  barMs: number,
) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width  = `${w}px`;
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(dpr, dpr);

  // Read design token colors at draw time
  const BG     = readToken("--background") || "#131519";
  const GRID   = "rgba(255,255,255,0.055)";
  const TEXT   = readToken("--fg-dim")     || "#9aa1ab";
  const LEVEL  = readToken("--c-yellow")   || "#e3b341";
  const CALLC  = readToken("--c-blue")     || "#6cb0ff";
  const ENTRYC = readToken("--c-green")    || "#5bc98a";
  const EXITC  = readToken("--c-red")      || "#ef6f6c";

  // Background
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, w, h);

  const LABEL_H = 18;  // top label row height
  const drawH   = h - LABEL_H;

  // Pane label
  ctx.fillStyle = "rgba(200,100,50,0.55)";
  ctx.font      = "bold 9px monospace";
  ctx.fillText("LIQUIDITY HEATMAP", PRICE_W + 6, 12);

  const visSnaps = snapshots.filter(
    (s) => s.t >= view.start - barMs * 2 && s.t <= view.end + barMs * 2,
  );

  if (visSnaps.length === 0) {
    ctx.fillStyle = TEXT;
    ctx.font      = "11px sans-serif";
    ctx.fillText("No book data in view", PRICE_W + 12, h / 2 + 4);
    return;
  }

  // Compute price range from visible snapshots
  let pMin = meta.level - 3, pMax = meta.level + 3;
  for (const s of visSnaps) {
    for (const b of s.bids) { pMin = Math.min(pMin, b.price); pMax = Math.max(pMax, b.price); }
    for (const a of s.asks) { pMin = Math.min(pMin, a.price); pMax = Math.max(pMax, a.price); }
  }

  // 95th-percentile log intensity scale
  const allSizes: number[] = [];
  for (const s of visSnaps) {
    for (const b of s.bids) allSizes.push(b.size);
    for (const a of s.asks) allSizes.push(a.size);
  }
  allSizes.sort((a, b) => a - b);
  const p95    = allSizes.length > 0 ? allSizes[Math.floor(allSizes.length * 0.95)] : 1;
  const logMax = Math.log1p(Math.max(p95, 1));

  // Price grid
  const step = (pMax - pMin) > 10 ? 2 : 1;
  ctx.strokeStyle = GRID;
  ctx.lineWidth   = 1;
  ctx.fillStyle   = TEXT;
  ctx.font        = "10px monospace";
  const yFn = (p: number) => LABEL_H + priceToY(p, pMin, pMax, drawH);
  let p = Math.ceil(pMin / step) * step;
  while (p <= pMax + step * 0.1) {
    const y = Math.round(yFn(p)) + 0.5;
    if (y > LABEL_H && y < h) {
      ctx.beginPath();
      ctx.moveTo(PRICE_W, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillText(p.toFixed(2), 2, y + 3.5);
    }
    p = Math.round((p + step) * 10000) / 10000;
  }

  const bpx   = barPxWidth(view, w, barMs);
  const tickH = Math.max(1, (drawH / ((pMax - pMin) / TICK + 1)) * (1 - 0.12));

  for (const snap of visSnaps) {
    const x = msToX(snap.t, view, w);
    if (x + bpx < PRICE_W || x > w) continue;

    for (const b of snap.bids) {
      const intensity = Math.log1p(b.size) / logMax;
      const a         = (0.08 + intensity * 0.82).toFixed(2);
      ctx.fillStyle   = `rgba(90,160,255,${a})`;   // --c-blue tinted
      ctx.fillRect(x + 0.5, yFn(b.price + TICK) + 0.5, bpx - 1, Math.max(1, tickH - 0.5));
    }
    for (const ask of snap.asks) {
      const intensity = Math.log1p(ask.size) / logMax;
      const a         = (0.08 + intensity * 0.82).toFixed(2);
      ctx.fillStyle   = `rgba(239,111,108,${a})`;  // --c-red tinted
      ctx.fillRect(x + 0.5, yFn(ask.price + TICK) + 0.5, bpx - 1, Math.max(1, tickH - 0.5));
    }
  }

  // Level line (--c-yellow)
  const ly = Math.round(yFn(meta.level)) + 0.5;
  ctx.save();
  ctx.strokeStyle = LEVEL;
  ctx.lineWidth   = 1.5;
  ctx.setLineDash([7, 4]);
  ctx.beginPath();
  ctx.moveTo(PRICE_W, ly);
  ctx.lineTo(w, ly);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();

  // Vertical event markers
  const drawVert = (ts: number, color: string, label?: string) => {
    const x = msToX(ts, view, w);
    if (x < PRICE_W || x > w) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, LABEL_H);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.setLineDash([]);
    if (label) {
      ctx.fillStyle = color;
      ctx.font      = "bold 9px monospace";
      ctx.fillText(label, x + 3, LABEL_H + 13);
    }
    ctx.restore();
  };

  drawVert(meta.post_ts,        CALLC,  "CALL");
  if (meta.entry_ts != null) drawVert(meta.entry_ts, ENTRYC, "IN");
  if (meta.exit_ts  != null) drawVert(meta.exit_ts,  EXITC,  "OUT");

  // Legend
  ctx.fillStyle = "rgba(120,120,120,0.45)";
  ctx.font      = "9px sans-serif";
  ctx.fillText("blue=bids  red=asks  amber=level (log-scaled)", PRICE_W + 6, h - 5);
}

// ── Main widget ────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function OrderflowHeatmapWidget(_props: WidgetProps) {
  const callId          = useOrderFlowSession((s) => s.callId);
  const book            = useOrderFlowSession((s) => s.book);
  const fineSnapshots   = useOrderFlowSession((s) => s.fineSnapshots);
  const fineBookLoading = useOrderFlowSession((s) => s.fineBookLoading);
  const meta            = useOrderFlowSession((s) => s.meta);
  const viewMs          = useOrderFlowSession((s) => s.viewMs);
  const footprint       = useOrderFlowSession((s) => s.footprint);
  const dataLoading     = useOrderFlowSession((s) => s.dataLoading);
  const dataError       = useOrderFlowSession((s) => s.dataError);

  const allSnaps: BookSnapshot[] = fineSnapshots.length > 0
    ? fineSnapshots
    : (book?.snapshots ?? []);

  const canvasRef    = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Compute barMs from footprint when available
  const barMs = footprint && footprint.bars.length > 1
    ? footprint.bars[1].t - footprint.bars[0].t
    : 30_000;

  // Default view: full window around meta
  const effectiveView: ViewMs | null = viewMs ?? (meta
    ? { start: meta.window_start, end: meta.window_end }
    : null);

  const draw = useCallback(() => {
    const cv  = canvasRef.current;
    const el  = containerRef.current;
    if (!cv || !el || allSnaps.length === 0 || !meta || !effectiveView) return;
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w <= 0 || h <= 0) return;
    drawHeatmapCanvas(cv, w, h, allSnaps, meta, effectiveView, barMs);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allSnaps, meta, effectiveView, barMs]);

  // Redraw on data/view change
  useEffect(() => { draw(); }, [draw]);

  // Resize observer
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(el);
    return () => ro.disconnect();
  }, [draw]);

  if (!callId) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
        No call selected
      </div>
    );
  }

  if (dataLoading) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
        Loading…
      </div>
    );
  }

  if (dataError) {
    return (
      <div className="p-3 font-mono text-xs text-[var(--c-red)]">{dataError}</div>
    );
  }

  if (allSnaps.length === 0 && !fineBookLoading) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
        No book data for this call
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="h-full w-full select-none overflow-hidden"
      style={{ cursor: "crosshair" }}
    >
      <canvas ref={canvasRef} style={{ display: "block" }} />
    </div>
  );
}
