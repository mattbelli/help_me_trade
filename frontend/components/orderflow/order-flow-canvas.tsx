"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BookData,
  CandleBar,
  FootprintData,
  FootprintBar,
  FootprintCell,
  OrderFlowMeta,
} from "@/lib/orderflow-api";

// ── Layout ────────────────────────────────────────────────────────────────────
const PRICE_W      = 68;             // left price axis width (px)
const FP_CANDLE_H  = 620;            // combined footprint+candle pane
const FOOTER_H     = 28;             // per-bar vol+delta strip at bottom
const FP_CHART_H   = FP_CANDLE_H - FOOTER_H; // price-mapped drawing area (592 px)
const DELTA_H      = 80;             // delta histogram pane (was 72)
const HEAT_H       = 200;            // liquidity heatmap pane (was 160 — increased for legibility)
const TIME_H       = 28;             // time axis height
const TICK         = 0.25;
const MAX_PRICE_RANGE    = 6.0;
const DEFAULT_BARS_BEFORE = 2;
const DEFAULT_BARS_AFTER  = 5;

// ── Colors — matched to CSS design tokens in globals.css ─────────────────────
const BG      = "#131519";  // --background
const GRID    = "rgba(255,255,255,0.055)";
const TEXT    = "#9aa1ab";  // --fg-dim
const UP_C    = "#5ad1be";  // --c-accent
const DN_C    = "#ef6f6c";  // --c-red
const LEVEL_C = "#e3b341";  // --c-yellow
const CALL_C  = "#6cb0ff";  // --c-blue
const ENTRY_C = "#5bc98a";  // --c-green
const EXIT_C  = "#ef6f6c";  // --c-red
const XHAIR   = "rgba(233,231,227,0.55)";  // --foreground at 55%
const POC_C   = "#b08cff";  // --c-violet

// HIGH-CONTRAST text: always white/near-white on any colored cell background.
// Convey side/delta via BAR color, not text color.
const CELL_TEXT  = "rgba(233,231,227,0.96)";     // --foreground, near-white
const CELL_TEXT_POS = "#86efac";  // muted green — readable on dark bg
const CELL_TEXT_NEG = "#fca5a5";  // muted red  — readable on dark bg

// ── Types ─────────────────────────────────────────────────────────────────────
type ViewMs    = { start: number; end: number };
type PriceZoom = { min: number; max: number } | null;
type CellMode  = "bidask" | "volume" | "delta" | "voldelta";

export interface Props {
  candles: CandleBar[];
  footprint: FootprintData;
  book?: BookData;
  meta: OrderFlowMeta;
  /** Master cursor timestamp (ms UTC) — drives crosshair + DOM ladder + tape. */
  cursorMs: number | null;
  /**
   * Called when the mouse hovers over the chart (readout only).
   * Does NOT drive the master cursor — use the timeline scrubber for that.
   */
  onHoverMove?: (ms: number | null, price: number | null) => void;
  /**
   * Called whenever the visible time or price range changes.
   * The parent uses this to update window.__ofState without a re-render.
   */
  onViewChange?: (timeMin: number, timeMax: number, priceMin: number, priceMax: number) => void;
}

// ── Module-level helpers ──────────────────────────────────────────────────────

function computeBarMs(fp: FootprintData): number {
  return fp.bars.length > 1 ? fp.bars[1].t - fp.bars[0].t : 30_000;
}

function computeDefaultView(meta: OrderFlowMeta, fp: FootprintData): ViewMs {
  if (fp.bars.length === 0) return { start: meta.window_start, end: meta.window_end };
  const bms = computeBarMs(fp);
  return {
    start: meta.post_ts - DEFAULT_BARS_BEFORE * bms,
    end:   meta.post_ts + DEFAULT_BARS_AFTER  * bms,
  };
}

// ── Coordinate helpers ────────────────────────────────────────────────────────

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

function yToPrice(
  y: number,
  pMin: number,
  pMax: number,
  h: number,
  pad = 0.06,
): number {
  const span = pMax - pMin || 1;
  const lo   = pMin - span * pad;
  const hi   = pMax + span * pad;
  return lo + (1 - y / h) * (hi - lo);
}

function getTickIntervalMs(spanMs: number): number {
  if (spanMs <= 2 * 60_000)       return 30_000;
  if (spanMs <= 15 * 60_000)      return 2 * 60_000;
  if (spanMs <= 60 * 60_000)      return 5 * 60_000;
  if (spanMs <= 3 * 3_600_000)    return 15 * 60_000;
  return 30 * 60_000;
}

function formatMs(ms: number): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour:     "2-digit",
    minute:   "2-digit",
    hour12:   false,
  }).format(new Date(ms));
}

// ── Combined footprint+candle price range ─────────────────────────────────────
function computeFootprintCandlePriceRange(
  visBars: FootprintBar[],
  visCandles: CandleBar[],
  meta: OrderFlowMeta,
  priceZoom: PriceZoom,
): { pMin: number; pMax: number } {
  if (priceZoom) {
    const { min: zMin, max: zMax } = priceZoom;
    if (zMin >= zMax) return { pMin: zMin - 2 * TICK, pMax: zMax + 2 * TICK };
    return { pMin: zMin, pMax: zMax };
  }
  let autoMin = meta.level, autoMax = meta.level;
  let hasData = false;
  for (const bar of visBars) {
    for (const c of bar.cells) {
      if (c.bidVol + c.askVol > 0) {
        autoMin = Math.min(autoMin, c.price);
        autoMax = Math.max(autoMax, c.price + TICK);
        hasData = true;
      }
    }
  }
  for (const c of visCandles) {
    if (c.l != null) autoMin = Math.min(autoMin, c.l);
    if (c.h != null) autoMax = Math.max(autoMax, c.h);
  }
  if (!hasData) { autoMin = meta.level - 4; autoMax = meta.level + 4; }
  autoMin -= TICK;
  autoMax += TICK;
  const mid  = (autoMin + autoMax) / 2;
  const half = Math.min((autoMax - autoMin) / 2, MAX_PRICE_RANGE / 2);
  const pMin = mid - half;
  const pMax = mid + half;
  if (pMin >= pMax) return { pMin: pMin - 2 * TICK, pMax: pMax + 2 * TICK };
  return { pMin, pMax };
}

// ── Cell color helpers ────────────────────────────────────────────────────────

function imbalanceColor(bidVol: number, askVol: number): string {
  const total = bidVol + askVol;
  if (total === 0) return "transparent";
  const t = askVol / total;
  if (t >= 0.5) {
    const f = (t - 0.5) / 0.5;
    const r = Math.round(15  + f * 10);
    const g = Math.round(130 + f * 125);
    const b = Math.round(35  + f * 55);
    const a = (0.28 + f * 0.67).toFixed(2);
    return `rgba(${r},${g},${b},${a})`;
  } else {
    const f = (0.5 - t) / 0.5;
    const r = Math.round(130 + f * 105);
    const g = Math.round(15  + f * 15);
    const b = Math.round(15  + f * 15);
    const a = (0.28 + f * 0.67).toFixed(2);
    return `rgba(${r},${g},${b},${a})`;
  }
}

function volumeColor(bidVol: number, askVol: number, maxVol: number): string {
  const total = bidVol + askVol;
  if (total === 0) return "transparent";
  const f = Math.log1p(total) / Math.log1p(Math.max(maxVol, 1));
  const a = (0.18 + f * 0.72).toFixed(2);
  return `rgba(100,130,220,${a})`;
}

function deltaCellColor(bidVol: number, askVol: number): string {
  const total = bidVol + askVol;
  if (total === 0) return "transparent";
  const delta = askVol - bidVol;
  const f     = Math.min(1, Math.abs(delta) / total);
  if (delta >= 0) {
    const a = (0.22 + f * 0.68).toFixed(2);
    return `rgba(38,166,154,${a})`;
  } else {
    const a = (0.22 + f * 0.68).toFixed(2);
    return `rgba(239,83,80,${a})`;
  }
}

function volDeltaColor(bidVol: number, askVol: number): string {
  const total = bidVol + askVol;
  if (total === 0) return "transparent";
  const delta = askVol - bidVol;
  const f     = Math.min(1, Math.abs(delta) / total);
  if (delta >= 0) {
    const r = Math.round(15  + f * 10);
    const g = Math.round(90  + f * 140);
    const b = Math.round(35  + f * 30);
    const a = (0.25 + f * 0.68).toFixed(2);
    return `rgba(${r},${g},${b},${a})`;
  } else {
    const r = Math.round(120 + f * 110);
    const g = Math.round(15  + f * 10);
    const b = Math.round(15  + f * 10);
    const a = (0.25 + f * 0.68).toFixed(2);
    return `rgba(${r},${g},${b},${a})`;
  }
}

// ── Shared drawing utilities ──────────────────────────────────────────────────

function drawVertMarker(
  ctx: CanvasRenderingContext2D,
  x: number,
  h: number,
  color: string,
  label?: string,
) {
  if (x < PRICE_W || x > ctx.canvas.clientWidth) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth   = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, h);
  ctx.stroke();
  ctx.setLineDash([]);
  if (label) {
    ctx.fillStyle = color;
    ctx.font      = "bold 9px monospace";
    ctx.fillText(label, x + 3, 13);
  }
  ctx.restore();
}

function drawPriceGridAndAxis(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  pMin: number,
  pMax: number,
  step: number,
) {
  ctx.strokeStyle = GRID;
  ctx.lineWidth   = 1;
  ctx.fillStyle   = TEXT;
  ctx.font        = "10px monospace";
  let p = Math.ceil(pMin / step) * step;
  while (p <= pMax + step * 0.1) {
    const y = Math.round(priceToY(p, pMin, pMax, h)) + 0.5;
    if (y > 0 && y < h) {
      ctx.beginPath();
      ctx.moveTo(PRICE_W, y);
      ctx.lineTo(w, y);
      ctx.stroke();
      ctx.fillText(p.toFixed(2), 2, y + 3.5);
    }
    p = Math.round((p + step) * 10000) / 10000;
  }
}

// ── Main component ─────────────────────────────────────────────────────────────

export function OrderFlowCanvas({
  candles,
  footprint,
  book,
  meta,
  cursorMs,
  onHoverMove,
  onViewChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fpRef        = useRef<HTMLCanvasElement>(null);
  const deltaRef     = useRef<HTMLCanvasElement>(null);
  const heatRef      = useRef<HTMLCanvasElement>(null);
  const timeRef      = useRef<HTMLCanvasElement>(null);
  const overlayRef   = useRef<HTMLCanvasElement>(null); // crosshair-only overlay

  // ── Refs for rAF loop (avoid React re-renders on every mouse move) ─────────
  const hoverMsRef    = useRef<number | null>(null);
  const viewRef       = useRef<ViewMs>({ start: 0, end: 0 });
  const cursorMsRef   = useRef<number | null>(cursorMs);
  const bookRef       = useRef(book);

  useEffect(() => { cursorMsRef.current = cursorMs; }, [cursorMs]);
  useEffect(() => { bookRef.current = book; }, [book]);

  // ── View state (lives in canvas — not lifted to avoid drag-frame re-renders) ─
  const [view, setView]           = useState<ViewMs>(() => computeDefaultView(meta, footprint));
  const [priceZoom, setPriceZoom] = useState<PriceZoom>(null);
  const [cellMode, setCellMode]   = useState<CellMode>("voldelta");

  // Keep viewRef in sync for rAF overlay
  useEffect(() => { viewRef.current = view; }, [view]);

  // Last computed auto price range — used by drag start + onViewChange
  const fpPriceRangeRef = useRef<{ pMin: number; pMax: number }>({
    pMin: meta.level - 4,
    pMax: meta.level + 4,
  });

  // Drag state: tracks BOTH axes for time pan (X) and price pan (Y)
  const drag = useRef<{
    on:  boolean;
    x0:  number;
    y0:  number;
    v0:  ViewMs;
    pz0: { min: number; max: number }; // effective price range at drag start
  }>({ on: false, x0: 0, y0: 0, v0: { start: 0, end: 0 }, pz0: { min: 0, max: 0 } });

  // Reset on call change
  useEffect(() => {
    setView(computeDefaultView(meta, footprint));
    setPriceZoom(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.post_id]);

  const barMs = computeBarMs(footprint);

  // ── COMBINED FOOTPRINT + CANDLESTICK PANE ────────────────────────────────
  // Redraws on cursorMs change to reveal bars progressively up to the playhead.
  // The crosshair itself stays on the separate overlay canvas (rAF, no re-render).
  const drawFootprintCandle = useCallback(
    (w: number) => {
      const cv = fpRef.current;
      if (!cv) return;
      const dpr = window.devicePixelRatio || 1;
      cv.width  = w * dpr;
      cv.height = FP_CANDLE_H * dpr;
      cv.style.width  = w + "px";
      cv.style.height = FP_CANDLE_H + "px";
      const ctx = cv.getContext("2d")!;
      ctx.scale(dpr, dpr);

      ctx.fillStyle = BG;
      ctx.fillRect(0, 0, w, FP_CANDLE_H);

      // Footer separator line
      ctx.strokeStyle = "rgba(255,255,255,0.07)";
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(PRICE_W, FP_CHART_H);
      ctx.lineTo(w, FP_CHART_H);
      ctx.stroke();

      // Progressive reveal: only show bars/candles up to the playhead (cursorMs),
      // so playback builds the chart bar-by-bar and scrubbing back hides future
      // bars. null cursor → reveal everything (Infinity) = the default full view.
      const revealMs = cursorMs ?? Infinity;
      const visBars = footprint.bars.filter(
        (b) => b.t >= view.start - barMs * 2 && b.t <= view.end + barMs * 2 && b.t <= revealMs,
      );
      const visCandles = candles.filter(
        (b) => b.t >= view.start - barMs * 2 && b.t <= view.end + barMs * 2 && b.o != null && b.t <= revealMs,
      );

      const candleMap = new Map<number, CandleBar>();
      for (const c of candles) candleMap.set(c.t, c);

      const { pMin, pMax } = computeFootprintCandlePriceRange(visBars, visCandles, meta, priceZoom);
      fpPriceRangeRef.current = { pMin, pMax }; // cache for drag start + onViewChange

      const visRange = pMax - pMin;
      const step     = visRange > 12 ? 2 : visRange > 6 ? 1 : 0.5;
      drawPriceGridAndAxis(ctx, w, FP_CHART_H, pMin, pMax, step);

      const bpx   = barPxWidth(view, w, barMs);
      const yFn   = (p: number) => priceToY(p, pMin, pMax, FP_CHART_H);
      const tickH = Math.max(1, yFn(pMin) - yFn(pMin + TICK));

      let maxVol = 1;
      for (const bar of visBars) {
        for (const cell of bar.cells) {
          const vol = cell.bidVol + cell.askVol;
          if (vol > maxVol) maxVol = vol;
        }
      }

      for (const bar of visBars) {
        const x = msToX(bar.t, view, w);
        if (x + bpx < PRICE_W || x > w) continue;

        const CANDLE_ZONE_FRAC = 0.13;
        const candleZoneW  = bpx * CANDLE_ZONE_FRAC;
        const halfColW     = (bpx - candleZoneW) / 2;
        const leftColX     = x;
        const candleColX   = x + halfColW;
        const rightColX    = x + halfColW + candleZoneW;
        const useTwoCol    = (cellMode === "voldelta" || cellMode === "bidask") && bpx >= 72;

        let pocCell: FootprintCell | null = null;
        let pocVol = 0;
        let maxBarAbsDelta = 1;
        let maxBarBid = 1;
        let maxBarAsk = 1;
        for (const cell of bar.cells) {
          const vol = cell.bidVol + cell.askVol;
          if (vol > pocVol) { pocVol = vol; pocCell = cell; }
          maxBarAbsDelta = Math.max(maxBarAbsDelta, Math.abs(cell.askVol - cell.bidVol));
          maxBarBid      = Math.max(maxBarBid, cell.bidVol);
          maxBarAsk      = Math.max(maxBarAsk, cell.askVol);
        }

        for (const cell of bar.cells) {
          const yTop  = yFn(cell.price + TICK);
          const cellH = Math.max(1, tickH);
          if (yTop + cellH < 0 || yTop > FP_CHART_H) continue;

          const vol   = cell.bidVol + cell.askVol;
          const delta = cell.askVol - cell.bidVol;

          if (!useTwoCol) {
            let bgColor: string;
            switch (cellMode) {
              case "bidask":   bgColor = imbalanceColor(cell.bidVol, cell.askVol);        break;
              case "volume":   bgColor = volumeColor(cell.bidVol, cell.askVol, maxVol);   break;
              case "delta":    bgColor = deltaCellColor(cell.bidVol, cell.askVol);         break;
              case "voldelta": bgColor = volDeltaColor(cell.bidVol, cell.askVol);          break;
              default:         bgColor = "transparent";
            }
            ctx.fillStyle = bgColor;
            ctx.fillRect(x + 0.5, yTop + 0.5, bpx - 1.5, cellH - 1);
            ctx.strokeStyle = "rgba(255,255,255,0.06)";
            ctx.lineWidth   = 0.5;
            ctx.strokeRect(x + 0.5, yTop + 0.5, bpx - 1.5, cellH - 1);

            if (bpx >= 36 && cellH >= 9 && vol > 0) {
              let label: string;
              switch (cellMode) {
                case "bidask":  label = `${cell.bidVol}×${cell.askVol}`; break;
                case "volume":  label = String(vol);                      break;
                case "delta":   label = delta > 0 ? `+${delta}` : String(delta); break;
                case "voldelta": label = String(vol);                     break;
                default:        label = "";
              }
              const fontSize = Math.min(12, Math.max(9, Math.floor(cellH * 0.65)));
              ctx.font       = `${fontSize}px monospace`;
              const tw       = ctx.measureText(label).width;
              if (tw < bpx - 4) {
                const tx = x + (bpx - tw) / 2;
                const ty = yTop + cellH * 0.72;
                // HIGH-CONTRAST: dark backdrop + near-white text on any background
                ctx.fillStyle = "rgba(0,0,0,0.45)";
                ctx.fillRect(tx - 1.5, ty - fontSize + 1, tw + 3, fontSize + 2);
                ctx.fillStyle = CELL_TEXT;
                ctx.fillText(label, tx, ty);
              }
            }
            continue;
          }

          // TWO-COLUMN LAYOUT
          ctx.strokeStyle = "rgba(255,255,255,0.05)";
          ctx.lineWidth   = 0.5;
          ctx.strokeRect(x + 0.5, yTop + 0.5, bpx - 1, cellH - 1);

          if (cellMode === "voldelta") {
            ctx.fillStyle = "rgba(18,20,26,0.65)";
            ctx.fillRect(leftColX + 0.5, yTop + 0.5, halfColW - 0.5, cellH - 1);

            const deltaFrac = Math.abs(delta) / maxBarAbsDelta;
            ctx.fillStyle   = "rgba(12,14,20,0.60)";
            ctx.fillRect(rightColX + 0.5, yTop + 0.5, halfColW - 0.5, cellH - 1);
            if (vol > 0) {
              ctx.fillStyle = delta >= 0
                ? "rgba(38,166,154,0.85)"
                : "rgba(239,83,80,0.85)";
              ctx.fillRect(rightColX + 0.5, yTop + 0.5, (halfColW - 1) * deltaFrac, cellH - 1);
            }

            if (halfColW >= 28 && cellH >= 8 && vol > 0) {
              const fontSize = Math.min(11, Math.max(8, Math.floor(cellH * 0.65)));
              ctx.font       = `${fontSize}px monospace`;
              const ty       = yTop + cellH * 0.73;

              // Vol text — HIGH-CONTRAST: white on dark backdrop
              const volStr = String(vol);
              const volW   = ctx.measureText(volStr).width;
              if (volW < halfColW - 2) {
                const tx = leftColX + (halfColW - volW) / 2;
                ctx.fillStyle = "rgba(0,0,0,0.45)";
                ctx.fillRect(tx - 1.5, ty - fontSize + 1, volW + 3, fontSize + 2);
                ctx.fillStyle = CELL_TEXT;
                ctx.fillText(volStr, tx, ty);
              }

              // Delta text — muted green/red (legible) on dark backdrop
              const dltStr = delta > 0 ? `+${delta}` : String(delta);
              const dltW   = ctx.measureText(dltStr).width;
              if (dltW < halfColW - 2) {
                const tx = rightColX + 3;
                ctx.fillStyle = "rgba(0,0,0,0.45)";
                ctx.fillRect(tx - 1, ty - fontSize + 1, dltW + 2, fontSize + 2);
                ctx.fillStyle = delta >= 0 ? CELL_TEXT_POS : CELL_TEXT_NEG;
                ctx.fillText(dltStr, tx, ty);
              }
            }
          } else {
            // BID×ASK diverging from the candle spine:
            // bid (red) grows LEFTWARD from center; ask (green) grows RIGHTWARD.
            const bidFrac  = maxBarBid > 0 ? cell.bidVol / maxBarBid : 0;
            const askFrac  = maxBarAsk > 0 ? cell.askVol / maxBarAsk : 0;
            // Center of the candle zone = midpoint of the entire bar
            const midX     = x + bpx / 2;
            const halfAvail = bpx / 2 - 1; // max pixels per side

            // Full-row dark background
            ctx.fillStyle = "rgba(12,14,20,0.60)";
            ctx.fillRect(x + 0.5, yTop + 0.5, bpx - 1, cellH - 1);

            // Bid bar: starts at midX, extends LEFTWARD
            if (cell.bidVol > 0) {
              const barW = Math.max(1, halfAvail * bidFrac);
              ctx.fillStyle = "rgba(239,83,80,0.80)";
              ctx.fillRect(midX - barW, yTop + 0.5, barW, cellH - 1);
            }

            // Ask bar: starts at midX, extends RIGHTWARD
            if (cell.askVol > 0) {
              const barW = Math.max(1, halfAvail * askFrac);
              ctx.fillStyle = "rgba(38,166,154,0.80)";
              ctx.fillRect(midX, yTop + 0.5, barW, cellH - 1);
            }

            // Numbers: bid on left half, ask on right half
            // HIGH-CONTRAST: always use near-white text with dark backdrop.
            // The bar color (red/green) already conveys the dominant side.
            if (bpx >= 72 && cellH >= 8 && vol > 0) {
              const fontSize   = Math.min(11, Math.max(8, Math.floor(cellH * 0.65)));
              ctx.font         = `${fontSize}px monospace`;
              const ty         = yTop + cellH * 0.73;
              const halfForTxt = midX - x - 2; // usable width in each half

              const bidStr = String(cell.bidVol);
              const bidW   = ctx.measureText(bidStr).width;
              if (bidW < halfForTxt) {
                const tx = x + (halfForTxt - bidW) / 2;
                ctx.fillStyle = "rgba(0,0,0,0.45)";
                ctx.fillRect(tx - 1.5, ty - fontSize + 1, bidW + 3, fontSize + 2);
                ctx.fillStyle = CELL_TEXT;
                ctx.fillText(bidStr, tx, ty);
              }

              const askStr = String(cell.askVol);
              const askW   = ctx.measureText(askStr).width;
              if (askW < halfForTxt) {
                const tx = midX + (halfForTxt - askW) / 2;
                ctx.fillStyle = "rgba(0,0,0,0.45)";
                ctx.fillRect(tx - 1.5, ty - fontSize + 1, askW + 3, fontSize + 2);
                ctx.fillStyle = CELL_TEXT;
                ctx.fillText(askStr, tx, ty);
              }
            }
          }
        }

        if (pocCell && pocVol > 0) {
          const yTop  = yFn(pocCell.price + TICK);
          const cellH = Math.max(1, tickH);
          if (!(yTop + cellH < 0 || yTop > FP_CHART_H)) {
            ctx.fillStyle   = "rgba(168,85,247,0.28)";
            ctx.fillRect(x + 1, yTop + 1, bpx - 2, cellH - 2);
            ctx.strokeStyle = POC_C;
            ctx.lineWidth   = 1.5;
            ctx.strokeRect(x + 1, yTop + 1, bpx - 2, cellH - 2);
          }
        }

        const candle = candleMap.get(bar.t);
        if (candle && candle.o != null && candle.h != null && candle.l != null) {
          const isUp  = candle.c >= candle.o;
          const color = isUp ? UP_C : DN_C;
          const cx: number    = useTwoCol ? candleColX + candleZoneW / 2 : x + bpx / 2;
          const halfW: number = useTwoCol
            ? Math.max(1, candleZoneW * 0.42)
            : Math.max(1, bpx * 0.35);

          ctx.save();
          ctx.globalAlpha = 0.90;
          ctx.strokeStyle = color;
          ctx.lineWidth   = 1.5;
          ctx.beginPath();
          ctx.moveTo(cx, yFn(candle.h));
          ctx.lineTo(cx, yFn(candle.l));
          ctx.stroke();

          const bodyTop = Math.min(yFn(candle.o), yFn(candle.c));
          const bodyH   = Math.max(2, Math.abs(yFn(candle.c) - yFn(candle.o)));
          ctx.globalAlpha = 0.22;
          ctx.fillStyle   = color;
          ctx.fillRect(cx - halfW, bodyTop, halfW * 2, bodyH);

          ctx.globalAlpha = 0.90;
          ctx.strokeStyle = color;
          ctx.lineWidth   = 1.5;
          ctx.strokeRect(cx - halfW, bodyTop, halfW * 2, bodyH);
          ctx.restore();
        }

        // Per-bar totals: visible at any zoom level that can fit them.
        // Wide bars (≥36px): full 12px font. Narrow (≥16px): 9px font — allows
        // delta+vol labels to survive zoom-out without crowding per-cell numbers.
        if (bpx >= 16) {
          const barVol   = bar.cells.reduce((s, c) => s + c.bidVol + c.askVol, 0);
          const fontSize = bpx >= 36 ? 12 : 9;

          // Per-bar delta label (top of bar) — muted green/red for readability
          const dltLbl = bar.delta > 0 ? `+${bar.delta}` : String(bar.delta);
          ctx.font      = `bold ${fontSize}px monospace`;
          const dw      = ctx.measureText(dltLbl).width;
          if (dw < bpx - 2) {
            ctx.fillStyle = bar.delta >= 0 ? CELL_TEXT_POS : CELL_TEXT_NEG;
            ctx.fillText(dltLbl, x + (bpx - dw) / 2, fontSize + 2);
          }

          if (barVol > 0) {
            const volLbl = String(barVol);
            ctx.font     = `${fontSize}px monospace`;
            const vw     = ctx.measureText(volLbl).width;
            if (vw < bpx - 2) {
              ctx.fillStyle = "rgba(154,161,171,0.85)";  // --fg-dim at 85%
              ctx.fillText(volLbl, x + (bpx - vw) / 2, FP_CHART_H + fontSize + 4);
            }
          }
        }
      }

      // Level line
      const ly = Math.round(yFn(meta.level)) + 0.5;
      ctx.save();
      ctx.strokeStyle = LEVEL_C;
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([7, 4]);
      ctx.beginPath();
      ctx.moveTo(PRICE_W, ly);
      ctx.lineTo(w, ly);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      const xFn = (ms: number) => msToX(ms, view, w);
      drawVertMarker(ctx, xFn(meta.post_ts),  FP_CANDLE_H, CALL_C,  "CALL");
      if (meta.entry_ts != null) drawVertMarker(ctx, xFn(meta.entry_ts), FP_CANDLE_H, ENTRY_C, "IN");
      if (meta.exit_ts  != null) drawVertMarker(ctx, xFn(meta.exit_ts),  FP_CANDLE_H, EXIT_C,  "OUT");

      // Pane label (bottom-left of chart area)
      const modeLabel =
        cellMode === "bidask"
          ? "Bid×Ask · bid(red)◄ from center ►ask(green) · longer side = dominant aggressor"
          : cellMode === "volume"
          ? "Volume · intensity=log(vol)"
          : cellMode === "delta"
          ? "Delta · green=+delta  red=−delta"
          : "Vol×Delta · LEFT=total vol  RIGHT=delta bar · purple=POC · top=Δtotal  bot=vol";
      ctx.fillStyle = "rgba(100,100,100,0.45)";
      ctx.font      = "9px sans-serif";
      ctx.fillText(
        `FOOTPRINT+CANDLE · ${modeLabel}  |  Shift+scroll=price zoom  |  drag=pan`,
        PRICE_W + 6,
        FP_CHART_H - 5,
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [candles, footprint.bars, meta, view, barMs, priceZoom, cellMode, cursorMs],
    // cursorMs IS a dep now: the panes reveal bars progressively up to the playhead
  );

  // ── DELTA STRIP ───────────────────────────────────────────────────────────
  const drawDelta = useCallback(
    (w: number) => {
      const cv = deltaRef.current;
      if (!cv) return;
      const dpr = window.devicePixelRatio || 1;
      cv.width  = w * dpr;
      cv.height = DELTA_H * dpr;
      cv.style.width  = w + "px";
      cv.style.height = DELTA_H + "px";
      const ctx = cv.getContext("2d")!;
      ctx.scale(dpr, dpr);

      ctx.fillStyle = BG;
      ctx.fillRect(0, 0, w, DELTA_H);

      // Top separator + pane label
      ctx.fillStyle = "rgba(255,255,255,0.07)";
      ctx.fillRect(0, 0, w, 1);
      ctx.fillStyle = "rgba(80,130,200,0.6)";
      ctx.font      = "bold 9px monospace";
      ctx.fillText("DELTA STRIP", PRICE_W + 6, 12);

      const revealMs = cursorMs ?? Infinity;
      const visBars = footprint.bars.filter(
        (b) => b.t >= view.start - barMs * 2 && b.t <= view.end + barMs * 2 && b.t <= revealMs,
      );

      const maxAbs  = visBars.length ? Math.max(1, ...visBars.map((b) => Math.abs(b.delta))) : 1;
      const bpx     = barPxWidth(view, w, barMs);
      const zeroY   = DELTA_H - 16;
      const barArea = zeroY - 16; // leave room for top label

      ctx.strokeStyle = GRID;
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(PRICE_W, zeroY);
      ctx.lineTo(w, zeroY);
      ctx.stroke();

      for (const bar of visBars) {
        const x = msToX(bar.t, view, w);
        if (x + bpx < PRICE_W || x > w) continue;
        const h    = (Math.abs(bar.delta) / maxAbs) * barArea;
        const isPos = bar.delta >= 0;
        ctx.fillStyle = isPos ? "rgba(46,204,113,0.65)" : "rgba(231,76,60,0.65)";
        ctx.fillRect(x + 0.5, isPos ? zeroY - h : zeroY, bpx - 1, h);

        if (bpx >= 22) {
          ctx.fillStyle = "rgba(200,200,200,0.7)";
          ctx.font      = "8px monospace";
          const lbl     = bar.delta > 0 ? `+${bar.delta}` : String(bar.delta);
          const tw      = ctx.measureText(lbl).width;
          if (tw < bpx - 2) ctx.fillText(lbl, x + (bpx - tw) / 2, zeroY - 2);
        }
      }

      // cumDelta line
      const cumVals = visBars.map((b) => b.cumDelta);
      const cumMin  = Math.min(...cumVals, 0);
      const cumMax  = Math.max(...cumVals, 0);
      if (cumMax !== cumMin) {
        const cyFn = (v: number) => 16 + ((1 - (v - cumMin) / (cumMax - cumMin)) * barArea);
        ctx.strokeStyle = "rgba(250,200,50,0.55)";
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        let first = true;
        for (const bar of visBars) {
          const x = msToX(bar.t, view, w) + bpx / 2;
          const y = cyFn(bar.cumDelta);
          if (first) { ctx.moveTo(x, y); first = false; }
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      ctx.fillStyle = "rgba(120,120,120,0.5)";
      ctx.font      = "9px sans-serif";
      ctx.fillText("bars=per-bar delta  line=cumDelta (gold)", PRICE_W + 6, DELTA_H - 4);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [footprint.bars, view, barMs, cursorMs],
  );

  // ── HEATMAP PANE ─────────────────────────────────────────────────────────
  const drawHeatmap = useCallback(
    (w: number) => {
      const cv = heatRef.current;
      if (!cv) return;
      const dpr = window.devicePixelRatio || 1;
      cv.width  = w * dpr;
      cv.height = HEAT_H * dpr;
      cv.style.width  = w + "px";
      cv.style.height = HEAT_H + "px";
      const ctx = cv.getContext("2d")!;
      ctx.scale(dpr, dpr);

      ctx.fillStyle = BG;
      ctx.fillRect(0, 0, w, HEAT_H);

      // Top separator + pane label
      ctx.fillStyle = "rgba(255,255,255,0.07)";
      ctx.fillRect(0, 0, w, 1);
      ctx.fillStyle = "rgba(200,100,50,0.6)";
      ctx.font      = "bold 9px monospace";
      ctx.fillText("LIQUIDITY HEATMAP", PRICE_W + 6, 12);

      if (!book || book.snapshots.length === 0) {
        ctx.fillStyle = TEXT;
        ctx.font      = "11px sans-serif";
        ctx.fillText("No book data", PRICE_W + 12, HEAT_H / 2 + 4);
        return;
      }

      const revealMs = cursorMs ?? Infinity;
      const visSnaps = book.snapshots.filter(
        (s) => s.t >= view.start - barMs * 2 && s.t <= view.end + barMs * 2 && s.t <= revealMs,
      );

      if (visSnaps.length === 0) {
        ctx.fillStyle = TEXT;
        ctx.font      = "11px sans-serif";
        ctx.fillText("No book data in view", PRICE_W + 12, HEAT_H / 2 + 4);
        return;
      }

      let pMin = meta.level - 3, pMax = meta.level + 3;
      for (const s of visSnaps) {
        for (const b of s.bids) { pMin = Math.min(pMin, b.price); pMax = Math.max(pMax, b.price); }
        for (const a of s.asks) { pMin = Math.min(pMin, a.price); pMax = Math.max(pMax, a.price); }
      }

      const allSizes: number[] = [];
      for (const s of visSnaps) {
        for (const b of s.bids) allSizes.push(b.size);
        for (const a of s.asks) allSizes.push(a.size);
      }
      allSizes.sort((x, y) => x - y);
      const p95    = allSizes.length > 0 ? allSizes[Math.floor(allSizes.length * 0.95)] : 1;
      const logMax = Math.log1p(Math.max(p95, 1));

      const drawH  = HEAT_H - 16; // leave room for pane label at top
      const yFn   = (p: number) => 16 + priceToY(p, pMin, pMax, drawH);
      const bpx   = barPxWidth(view, w, barMs);
      const tickH = Math.max(1, (drawH / ((pMax - pMin) / TICK + 1)) * (1 - 0.12));

      const step = (pMax - pMin) > 10 ? 2 : 1;
      // Draw price grid in the heatmap area (below label)
      ctx.strokeStyle = GRID;
      ctx.lineWidth   = 1;
      ctx.fillStyle   = TEXT;
      ctx.font        = "10px monospace";
      let p = Math.ceil(pMin / step) * step;
      while (p <= pMax + step * 0.1) {
        const y = Math.round(yFn(p)) + 0.5;
        if (y > 16 && y < HEAT_H) {
          ctx.beginPath();
          ctx.moveTo(PRICE_W, y);
          ctx.lineTo(w, y);
          ctx.stroke();
          ctx.fillText(p.toFixed(2), 2, y + 3.5);
        }
        p = Math.round((p + step) * 10000) / 10000;
      }

      for (const snap of visSnaps) {
        const x = msToX(snap.t, view, w);
        if (x + bpx < PRICE_W || x > w) continue;

        for (const b of snap.bids) {
          const intensity = Math.log1p(b.size) / logMax;
          const a         = (0.1 + intensity * 0.8).toFixed(2);
          ctx.fillStyle   = `rgba(20,100,255,${a})`;
          ctx.fillRect(x + 0.5, yFn(b.price + TICK) + 0.5, bpx - 1, Math.max(1, tickH - 0.5));
        }
        for (const a of snap.asks) {
          const intensity = Math.log1p(a.size) / logMax;
          const al        = (0.1 + intensity * 0.8).toFixed(2);
          ctx.fillStyle   = `rgba(255,100,20,${al})`;
          ctx.fillRect(x + 0.5, yFn(a.price + TICK) + 0.5, bpx - 1, Math.max(1, tickH - 0.5));
        }
      }

      const ly = Math.round(yFn(meta.level)) + 0.5;
      ctx.save();
      ctx.strokeStyle = LEVEL_C;
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([7, 4]);
      ctx.beginPath();
      ctx.moveTo(PRICE_W, ly);
      ctx.lineTo(w, ly);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      const xFn = (ms: number) => msToX(ms, view, w);
      drawVertMarker(ctx, xFn(meta.post_ts),  HEAT_H, CALL_C);
      if (meta.entry_ts != null) drawVertMarker(ctx, xFn(meta.entry_ts), HEAT_H, ENTRY_C);
      if (meta.exit_ts  != null) drawVertMarker(ctx, xFn(meta.exit_ts),  HEAT_H, EXIT_C);

      ctx.fillStyle = "rgba(120,120,120,0.5)";
      ctx.font      = "9px sans-serif";
      ctx.fillText("blue=bids  orange=asks  amber=level (log-scaled)", PRICE_W + 6, HEAT_H - 5);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [book, meta, view, barMs, cursorMs],
  );

  // ── TIME AXIS ─────────────────────────────────────────────────────────────
  const drawTime = useCallback(
    (w: number) => {
      const cv = timeRef.current;
      if (!cv) return;
      const dpr = window.devicePixelRatio || 1;
      cv.width  = w * dpr;
      cv.height = TIME_H * dpr;
      cv.style.width  = w + "px";
      cv.style.height = TIME_H + "px";
      const ctx = cv.getContext("2d")!;
      ctx.scale(dpr, dpr);

      ctx.fillStyle = BG;
      ctx.fillRect(0, 0, w, TIME_H);

      // Top separator
      ctx.fillStyle = "rgba(255,255,255,0.07)";
      ctx.fillRect(0, 0, w, 1);

      const spanMs = view.end - view.start;
      const tickMs = getTickIntervalMs(spanMs);
      let t = Math.ceil(view.start / tickMs) * tickMs;

      while (t <= view.end) {
        const x = Math.round(msToX(t, view, w)) + 0.5;
        if (x >= PRICE_W && x <= w) {
          ctx.strokeStyle = GRID;
          ctx.lineWidth   = 1;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, TIME_H);
          ctx.stroke();
          ctx.fillStyle = TEXT;
          ctx.font      = "10px monospace";
          ctx.fillText(formatMs(t), x - 18, 16);
        }
        t += tickMs;
      }

      ctx.fillStyle = "rgba(100,100,100,0.6)";
      ctx.font      = "9px monospace";
      ctx.fillText("ET", 4, 16);
    },
    [view],
  );

  // ── Redraw all main canvases ──────────────────────────────────────────────
  const redrawAll = useCallback(() => {
    const w = containerRef.current?.clientWidth;
    if (!w) return;
    drawFootprintCandle(w);
    drawDelta(w);
    drawHeatmap(w);
    drawTime(w);
  }, [drawFootprintCandle, drawDelta, drawHeatmap, drawTime]);

  useEffect(() => { redrawAll(); }, [redrawAll]);

  // Resize observer
  useEffect(() => {
    const ro = new ResizeObserver(() => { redrawAll(); });
    if (containerRef.current) ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [redrawAll]);

  // ── Notify parent of view changes (for window.__ofState) ─────────────────
  // Runs AFTER the draw effect (same-render effects run in declaration order),
  // so fpPriceRangeRef.current is up to date when priceZoom is null.
  useEffect(() => {
    const { pMin, pMax } = priceZoom
      ? { pMin: priceZoom.min, pMax: priceZoom.max }
      : fpPriceRangeRef.current;
    onViewChange?.(view.start, view.end, pMin, pMax);
  }, [view, priceZoom, onViewChange]);

  // ── rAF loop for overlay canvas (crosshair only — no React re-renders) ───
  useEffect(() => {
    let rafId: number;

    function drawOverlay() {
      const cv        = overlayRef.current;
      const container = containerRef.current;
      if (!cv || !container) return;

      const w = container.clientWidth;
      if (w <= 0) return;

      const dpr     = window.devicePixelRatio || 1;
      const hasBook = !!bookRef.current;
      const totalH  = FP_CANDLE_H + DELTA_H + (hasBook ? HEAT_H : 0) + TIME_H;
      const chartH  = totalH - TIME_H; // don't draw into time axis

      // Resize overlay canvas if needed
      const wPx = Math.round(w * dpr);
      const hPx = Math.round(totalH * dpr);
      if (cv.width !== wPx || cv.height !== hPx) {
        cv.width        = wPx;
        cv.height       = hPx;
        cv.style.width  = w + "px";
        cv.style.height = totalH + "px";
      }

      const ctx = cv.getContext("2d")!;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, totalH);

      const v        = viewRef.current;
      const masterMs = cursorMsRef.current;
      const hoverMs  = hoverMsRef.current;

      // Master cursor — solid crosshair (driven by timeline/play)
      if (masterMs != null) {
        const x = msToX(masterMs, v, w);
        if (x >= PRICE_W && x <= w) {
          ctx.save();
          ctx.strokeStyle = XHAIR;
          ctx.lineWidth   = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, chartH);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
        }
      }

      // Hover indicator — faint dashed (readout only, does NOT drive ladder/tape)
      if (hoverMs != null && hoverMs !== masterMs) {
        const x = msToX(hoverMs, v, w);
        if (x >= PRICE_W && x <= w) {
          ctx.save();
          ctx.strokeStyle = "rgba(255,255,255,0.18)";
          ctx.lineWidth   = 1;
          ctx.setLineDash([2, 5]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, chartH);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
        }
      }
    }

    function frame() {
      drawOverlay();
      rafId = requestAnimationFrame(frame);
    }

    rafId = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafId);
  }, []); // No React deps — reads only from refs

  // ── Mouse interaction ──────────────────────────────────────────────────────

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      // Capture effective price range at drag start (use priceZoom if set, else auto)
      const startPz = priceZoom ?? {
        min: fpPriceRangeRef.current.pMin,
        max: fpPriceRangeRef.current.pMax,
      };
      drag.current = {
        on:  true,
        x0:  e.clientX,
        y0:  e.clientY,
        v0:  { ...view },
        pz0: startPz,
      };
    },
    [view, priceZoom],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const w = containerRef.current?.clientWidth;
      if (!w) return;
      const rect   = containerRef.current!.getBoundingClientRect();
      const localX = e.clientX - rect.left;
      const localY = e.clientY - rect.top;

      // Compute hover position (for readout strip and faint crosshair only)
      const ms = localX < PRICE_W
        ? null
        : view.start + ((localX - PRICE_W) / (w - PRICE_W)) * (view.end - view.start);

      let price: number | null = null;
      if (localY >= 0 && localY < FP_CHART_H) {
        const { pMin, pMax } = fpPriceRangeRef.current;
        price = Math.round(yToPrice(localY, pMin, pMax, FP_CHART_H) / TICK) * TICK;
      }

      // Store in refs — the rAF loop reads these to draw the faint hover crosshair
      hoverMsRef.current = ms;
      // Notify parent for the readout strip (does NOT drive master cursor)
      onHoverMove?.(ms, price);

      if (!drag.current.on) return;

      const dx = e.clientX - drag.current.x0;
      const dy = e.clientY - drag.current.y0;

      // ── Horizontal drag → time pan ─────────────────────────────────────
      const span  = drag.current.v0.end - drag.current.v0.start;
      const dtMs  = -(dx / (w - PRICE_W)) * span;
      setView({ start: drag.current.v0.start + dtMs, end: drag.current.v0.end + dtMs });

      // ── Vertical drag → price pan ──────────────────────────────────────
      // drag UP (dy < 0) → see higher prices; drag DOWN (dy > 0) → see lower prices
      const pz0       = drag.current.pz0;
      const priceRange = pz0.max - pz0.min;
      // Include padding (0.06) so pixel↔price ratio matches priceToY
      const visualRange  = priceRange * (1 + 2 * 0.06);
      const pricePerPx   = visualRange / FP_CHART_H;
      // dy < 0 (drag up) → dprice > 0 → range shifts up → higher prices
      const dprice = -dy * pricePerPx;
      setPriceZoom({ min: pz0.min + dprice, max: pz0.max + dprice });
    },
    [view, onHoverMove],
  );

  const endDrag = useCallback(() => { drag.current.on = false; }, []);

  const handleMouseLeave = useCallback(() => {
    drag.current.on = false;
    hoverMsRef.current = null;
    onHoverMove?.(null, null);
  }, [onHoverMove]);

  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const w = containerRef.current?.clientWidth;
      if (!w) return;

      if (e.shiftKey) {
        // Shift+wheel → price zoom, centered on the cursor's current price position.
        // Uses priceToY/yToPrice math so the price under the cursor stays put.
        const rect    = containerRef.current!.getBoundingClientRect();
        const localY  = e.clientY - rect.top;

        const currentMin   = priceZoom ? priceZoom.min : fpPriceRangeRef.current.pMin;
        const currentMax   = priceZoom ? priceZoom.max : fpPriceRangeRef.current.pMax;
        const currentRange = currentMax - currentMin;

        // Price under the cursor (fall back to midpoint if cursor is outside the FP pane)
        const pivotPrice = (localY >= 0 && localY < FP_CHART_H)
          ? yToPrice(localY, currentMin, currentMax, FP_CHART_H)
          : (currentMin + currentMax) / 2;

        const factor   = e.deltaY > 0 ? 1.18 : 0.82;
        const newRange = Math.max(1.0, Math.min(24.0, currentRange * factor));

        // Re-solve for newMin/newMax so pivotPrice stays at the same pixel Y.
        // priceToY uses pad=0.06: lo = pMin - range*pad, hi = pMax + range*pad
        const pad         = 0.06;
        const lo          = currentMin - currentRange * pad;
        const visRange    = currentRange * (1 + 2 * pad);
        const frac        = (pivotPrice - lo) / visRange; // cursor's fractional Y in visual range
        const newVisRange = newRange * (1 + 2 * pad);
        const newLo       = pivotPrice - frac * newVisRange;
        const newHi       = newLo + newVisRange;
        const newMin      = newLo + newRange * pad;
        const newMax      = newHi - newRange * pad;
        setPriceZoom({ min: newMin, max: newMax });
      } else {
        // Plain wheel → time zoom, pivoted at mouse X
        const rect    = containerRef.current!.getBoundingClientRect();
        const frac    = Math.max(0, Math.min(1, (e.clientX - rect.left - PRICE_W) / (w - PRICE_W)));
        const span    = view.end - view.start;
        const factor  = e.deltaY > 0 ? 1.12 : 0.88;
        const newSpan = Math.max(30_000, Math.min(
          Math.max(1, meta.window_end - meta.window_start),
          span * factor,
        ));
        const pivot    = view.start + frac * span;
        const newStart = pivot - frac * newSpan;
        setView({ start: newStart, end: newStart + newSpan });
      }
    },
    [view, meta, priceZoom],
  );

  const resetView = useCallback(() => {
    setView(computeDefaultView(meta, footprint));
    setPriceZoom(null);
  }, [meta, footprint]);

  const totalH = FP_CANDLE_H + DELTA_H + (book ? HEAT_H : 0) + TIME_H;

  const MODE_LABELS: Record<CellMode, string> = {
    bidask:   "Bid×Ask",
    volume:   "Volume",
    delta:    "Delta",
    voldelta: "Vol×Delta",
  };

  return (
    <div style={{ position: "relative" }}>
      {/* ── Cell mode toggle ─────────────────────────────────────────────── */}
      <div
        style={{
          display:      "flex",
          alignItems:   "center",
          gap:          4,
          padding:      "4px 10px",
          background:   "var(--bg-inset)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span
          style={{
            fontSize:      9,
            color:         "var(--fg-faint)",
            fontFamily:    "monospace",
            marginRight:   4,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
          }}
        >
          Cell Mode
        </span>
        {(["bidask", "volume", "delta", "voldelta"] as CellMode[]).map((mode) => (
          <button
            key={mode}
            onClick={() => setCellMode(mode)}
            style={{
              background:    cellMode === mode ? "color-mix(in srgb, var(--c-violet) 25%, var(--bg-hover))" : "var(--bg-hover)",
              border:        `1px solid ${cellMode === mode ? "color-mix(in srgb, var(--c-violet) 50%, transparent)" : "var(--border)"}`,
              color:         cellMode === mode ? "var(--foreground)" : "var(--fg-faint)",
              fontSize:      "10px",
              fontFamily:    "monospace",
              padding:       "2px 9px",
              borderRadius:  "3px",
              cursor:        "pointer",
              letterSpacing: "0.03em",
            }}
          >
            {MODE_LABELS[mode]}
            {mode === "voldelta" && (
              <span style={{ marginLeft: 4, fontSize: 8, opacity: 0.60 }}>★</span>
            )}
          </button>
        ))}
        <span
          style={{
            marginLeft:    "auto",
            fontSize:      9,
            color:         "var(--fg-faint)",
            fontFamily:    "monospace",
            letterSpacing: "0.04em",
          }}
        >
          drag=pan both axes · wheel=zoom time · shift+wheel=zoom price
        </span>
      </div>

      {/* ── Chart container (all canvas panes stacked) ───────────────────── */}
      <div
        ref={containerRef}
        data-testid="chart-container"
        className="w-full select-none overflow-hidden rounded border border-border/30"
        style={{ cursor: "crosshair", height: totalH, position: "relative" }}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={handleMouseLeave}
      >
        {/* Pane 1: Footprint + Candle */}
        <canvas ref={fpRef}    style={{ display: "block", width: "100%", height: FP_CANDLE_H }} />
        {/* Pane 2: Delta Strip */}
        <canvas ref={deltaRef} style={{ display: "block", width: "100%", height: DELTA_H }} />
        {/* Pane 3: Liquidity Heatmap */}
        {book && <canvas ref={heatRef} style={{ display: "block", width: "100%", height: HEAT_H }} />}
        {/* Pane 4: Time Axis */}
        <canvas ref={timeRef}  style={{ display: "block", width: "100%", height: TIME_H }} />

        {/* Overlay canvas — crosshair ONLY (pointer-events: none, drawn via rAF) */}
        <canvas
          ref={overlayRef}
          style={{
            position:      "absolute",
            top:           0,
            left:          0,
            pointerEvents: "none",
            zIndex:        5,
          }}
        />
      </div>

      {/* ── Reset view button ─────────────────────────────────────────────── */}
      <button
        onClick={resetView}
        style={{
          position:      "absolute",
          top:           34,
          right:         10,
          zIndex:        10,
          background:    "var(--bg-inset)",
          border:        "1px solid var(--border-strong)",
          color:         "var(--fg-dim)",
          fontSize:      "9px",
          padding:       "2px 7px",
          borderRadius:  "3px",
          cursor:        "pointer",
          letterSpacing: "0.05em",
          userSelect:    "none",
        }}
      >
        reset view
      </button>
    </div>
  );
}
