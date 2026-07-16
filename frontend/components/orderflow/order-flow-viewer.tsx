"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart2,
  BookOpen,
  Loader2,
  Pause,
  Play,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchOrderFlowBookFine,
  fetchOrderFlowCalls,
  fetchOrderFlowTapeWindow,
  formatET,
  formatTimeET,
  formatTimeMsET,
  type BookSnapshot,
  type CandleBar,
  type FootprintData,
  type OrderFlowCall,
  type OrderFlowMeta,
  type TapeEvent,
} from "@/lib/orderflow-api";
import { useOrderFlowData } from "@/lib/use-orderflow-data";
import { OrderFlowCanvas } from "@/components/orderflow/order-flow-canvas";

// ── Constants ─────────────────────────────────────────────────────────────────
const TICK = 0.25;
// Playback: advance ~1500ms market per 50ms real (1×); ~15-min window in ~30s
const PLAY_REAL_TICK_MS   = 50;
const PLAY_MARKET_BASE_MS = 1500;

// Rolling tape window around cursor: ±2 minutes
const TAPE_WINDOW_MS = 2 * 60_000;
// Fine book window: ±6 minutes around cursor
const BOOK_FINE_WINDOW_MS = 6 * 60_000;
// Re-fetch fine book when cursor moves to a new 5-minute bucket
const BOOK_FINE_BUCKET_MS = 5 * 60_000;
// Re-fetch tape when cursor moves to a new 60-second bucket
const TAPE_BUCKET_MS = 60_000;

type PlaySpeed  = 1 | 2 | 4;
type CallFilter = "all" | "winners" | "losers" | "mbo";

// ── Helpers ───────────────────────────────────────────────────────────────────

function callOutcome(c: OrderFlowCall): "WIN" | "LOSS" | null {
  if (c.points_net == null) return null;
  return c.points_net >= 0 ? "WIN" : "LOSS";
}

function callLabel(c: OrderFlowCall): string {
  const dir     = c.direction.toLowerCase() === "long" ? "▲" : "▼";
  const outcome = callOutcome(c);
  const pnl =
    c.points_net != null
      ? c.points_net >= 0
        ? `+${c.points_net.toFixed(2)}`
        : c.points_net.toFixed(2)
      : "—";
  const reason  = c.exit_reason ? ` · ${c.exit_reason}` : "";
  const tag     = outcome ? ` [${outcome}]` : "";
  return `${c.date}  ${dir} ${c.direction}  @${c.level.toFixed(2)}  ${pnl} pts${tag}${reason}`;
}

// ── Crosshair readout ─────────────────────────────────────────────────────────

function CrosshairReadout({
  displayMs,
  displayPrice,
  candles,
  footprint,
}: {
  displayMs:    number | null;  // hoverMs ?? cursorMs
  displayPrice: number | null;
  candles:      CandleBar[];
  footprint:    FootprintData;
}) {
  const barMs = footprint.bars.length > 1
    ? footprint.bars[1].t - footprint.bars[0].t
    : 30_000;

  const bar = useMemo(() => {
    if (!displayMs || footprint.bars.length === 0) return null;
    let best = footprint.bars[0];
    let bestDist = Math.abs(best.t - displayMs);
    for (const b of footprint.bars) {
      const d = Math.abs(b.t - displayMs);
      if (d < bestDist) { bestDist = d; best = b; }
    }
    return best;
  }, [displayMs, footprint.bars]);

  const candle = useMemo(() => {
    if (!bar) return null;
    return candles.find((c) => c.t === bar.t) ?? null;
  }, [bar, candles]);

  const cell = useMemo(() => {
    if (!bar || displayPrice == null) return null;
    const snapped = Math.round(displayPrice / TICK) * TICK;
    return bar.cells.find((c) => Math.abs(c.price - snapped) < 0.001) ?? null;
  }, [bar, displayPrice]);

  if (!displayMs || !bar) {
    return (
      <div
        className="flex items-center px-3 py-1.5 font-mono text-[11px] text-muted-foreground/40"
        style={{ minHeight: 28, background: "#111214", borderTop: "1px solid rgba(255,255,255,0.06)" }}
      >
        hover chart or drag timeline to inspect
      </div>
    );
  }

  const deltaColor = bar.delta >= 0 ? "#34d399" : "#f87171";

  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-0.5 px-3 py-1.5 font-mono text-[11px]"
      style={{ minHeight: 28, background: "#111214", borderTop: "1px solid rgba(255,255,255,0.06)" }}
    >
      <span className="text-muted-foreground">{formatTimeET(displayMs)}</span>

      {candle && (
        <>
          <span>
            <span className="text-muted-foreground/60">O </span>
            <span className="text-foreground">{candle.o?.toFixed(2) ?? "—"}</span>
          </span>
          <span>
            <span className="text-muted-foreground/60">H </span>
            <span className="text-green-400">{candle.h?.toFixed(2) ?? "—"}</span>
          </span>
          <span>
            <span className="text-muted-foreground/60">L </span>
            <span className="text-red-400">{candle.l?.toFixed(2) ?? "—"}</span>
          </span>
          <span>
            <span className="text-muted-foreground/60">C </span>
            <span className="text-foreground">{candle.c?.toFixed(2) ?? "—"}</span>
          </span>
        </>
      )}

      <span style={{ color: deltaColor }}>
        Δ {bar.delta > 0 ? "+" : ""}{bar.delta}
      </span>

      {cell ? (
        <span className="text-blue-300">
          @{cell.price.toFixed(2)}: {cell.bidVol}×{cell.askVol}
        </span>
      ) : displayPrice != null ? (
        <span className="text-muted-foreground/40">
          @{displayPrice.toFixed(2)}: no volume
        </span>
      ) : null}

      <span className="text-muted-foreground/30 text-[9px]">
        bar:{Math.round(barMs / 1000)}s
      </span>
    </div>
  );
}

// ── Timeline scrubber + playback ──────────────────────────────────────────────
//
// Interaction model:
//   - Click-and-hold-drag on the TRACK → scrub (sets cursorMs)
//   - Play button → animates cursorMs via rAF
//   - Hover on chart → does NOT change cursorMs (handled in OrderFlowCanvas)

function TimeScrubber({
  meta,
  cursorMs,
  playing,
  playSpeed,
  onScrub,
  onTogglePlay,
  onSpeedChange,
}: {
  meta:          OrderFlowMeta;
  cursorMs:      number | null;
  playing:       boolean;
  playSpeed:     PlaySpeed;
  onScrub:       (ms: number) => void;
  onTogglePlay:  () => void;
  onSpeedChange: (s: PlaySpeed) => void;
}) {
  const value = cursorMs ?? meta.post_ts;
  const pct   = ((value - meta.window_start) / Math.max(1, meta.window_end - meta.window_start)) * 100;

  const trackRef  = useRef<HTMLDivElement>(null);
  const scrubbing = useRef(false);
  // Store stable refs to callbacks so the native listener closure never goes stale
  const onScrubRef  = useRef(onScrub);
  const windowRef   = useRef({ start: meta.window_start, end: meta.window_end });
  useEffect(() => { onScrubRef.current = onScrub; }, [onScrub]);
  useEffect(() => {
    windowRef.current = { start: meta.window_start, end: meta.window_end };
  }, [meta.window_start, meta.window_end]);

  const markerPct = (ts: number | null) =>
    ts == null
      ? null
      : ((ts - meta.window_start) / Math.max(1, meta.window_end - meta.window_start)) * 100;

  const postPct  = markerPct(meta.post_ts);
  const entryPct = markerPct(meta.entry_ts);
  const exitPct  = markerPct(meta.exit_ts);

  // Convert a client X position to a timeline ms value (reads from refs — stable in native listeners)
  const xToMsNative = useCallback((clientX: number): number => {
    const track = trackRef.current;
    if (!track) return windowRef.current.start;
    const rect = track.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    const { start, end } = windowRef.current;
    // Round to integer ms so URL query params are clean integers (backend rejects floats)
    return Math.round(start + frac * (end - start));
  }, []);

  // Attach ALL timeline interaction via NATIVE DOM listeners (not React synthetic events).
  // Playwright fires native mousedown/mousemove/mouseup, so this guarantees testability.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const onDown = (e: MouseEvent) => {
      e.preventDefault();
      scrubbing.current = true;
      onScrubRef.current(xToMsNative(e.clientX));
    };
    const onMove = (e: MouseEvent) => {
      if (!scrubbing.current) return;
      onScrubRef.current(xToMsNative(e.clientX));
    };
    const onUp = () => { scrubbing.current = false; };

    track.addEventListener("mousedown", onDown);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      track.removeEventListener("mousedown", onDown);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only runs once — reads from stable refs

  return (
    <div
      className="flex flex-col gap-1.5 rounded border border-border/30 px-3 py-2"
      style={{ background: "#0e1012" }}
    >
      {/* Label */}
      <div className="flex items-center justify-between">
        <span className="text-[9px] uppercase tracking-wider text-muted-foreground/40 font-mono">
          Timeline scrubber — click-and-drag to set cursor
        </span>
        <span className="font-mono text-[10px] text-muted-foreground/50">
          {pct.toFixed(0)}%
        </span>
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-3">
        {/* Play / Pause */}
        <button
          onClick={onTogglePlay}
          className="flex size-7 shrink-0 items-center justify-center rounded border border-border/40 bg-muted/20 text-muted-foreground transition hover:border-border hover:text-foreground"
        >
          {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
        </button>

        {/* Speed */}
        <div className="flex shrink-0 gap-0.5">
          {([1, 2, 4] as PlaySpeed[]).map((s) => (
            <button
              key={s}
              onClick={() => onSpeedChange(s)}
              className={[
                "rounded px-1.5 py-0.5 font-mono text-[10px] transition",
                s === playSpeed
                  ? "bg-blue-600/60 text-blue-200"
                  : "text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              {s}×
            </button>
          ))}
        </div>

        {/* Scrubber track — click-and-hold-drag here to set cursor */}
        <div
          ref={trackRef}
          data-testid="timeline-track"
          className="relative flex-1 cursor-col-resize select-none"
          style={{ height: 24, userSelect: "none" }}
        >
          {/* Track background */}
          <div
            className="absolute inset-x-0 rounded-full bg-muted/30"
            style={{ top: "50%", transform: "translateY(-50%)", height: 6 }}
          />

          {/* Filled portion */}
          <div
            className="absolute left-0 rounded-full bg-blue-600/50"
            style={{
              top:    "50%",
              transform: "translateY(-50%)",
              height: 6,
              width:  `${Math.max(0, Math.min(100, pct))}%`,
            }}
          />

          {/* Marker pips */}
          <div className="pointer-events-none absolute inset-0">
            {postPct  != null && (
              <div
                className="absolute h-4 w-0.5 -translate-x-1/2 rounded bg-blue-400/70"
                style={{ left: `${postPct}%`, top: "50%", transform: "translate(-50%,-50%)" }}
                title="Call"
              />
            )}
            {entryPct != null && (
              <div
                className="absolute h-4 w-0.5 -translate-x-1/2 rounded bg-green-400/70"
                style={{ left: `${entryPct}%`, top: "50%", transform: "translate(-50%,-50%)" }}
                title="Entry"
              />
            )}
            {exitPct  != null && (
              <div
                className="absolute h-4 w-0.5 -translate-x-1/2 rounded bg-red-400/70"
                style={{ left: `${exitPct}%`, top: "50%", transform: "translate(-50%,-50%)" }}
                title="Exit"
              />
            )}
          </div>

          {/* Cursor thumb */}
          {cursorMs != null && (
            <div
              className="pointer-events-none absolute h-4 w-2.5 -translate-x-1/2 rounded bg-blue-400 shadow"
              style={{
                left:      `${Math.max(0, Math.min(100, pct))}%`,
                top:       "50%",
                transform: "translate(-50%, -50%)",
              }}
            />
          )}
        </div>

        {/* Timestamp display */}
        <span className="w-20 shrink-0 text-right font-mono text-xs text-muted-foreground">
          {formatTimeET(value)}
        </span>
      </div>
    </div>
  );
}

// ── Meta Header ───────────────────────────────────────────────────────────────

function MetaHeader({
  meta,
  call,
}: {
  meta: OrderFlowMeta;
  call: OrderFlowCall;
}) {
  const pnl = meta.points_net;
  const pnlColor =
    pnl == null
      ? "text-muted-foreground"
      : pnl >= 0
        ? "text-green-400"
        : "text-red-400";
  const isLong = call.direction.toLowerCase() === "long";

  return (
    <Card>
      <CardContent className="pt-4 pb-4">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <span className="text-xl font-semibold tracking-tight text-foreground">
              {meta.symbol}
            </span>
            <Badge
              variant="outline"
              className={
                isLong
                  ? "border-green-500/40 bg-green-950/40 text-green-400"
                  : "border-red-500/40 bg-red-950/40 text-red-400"
              }
            >
              {isLong ? (
                <ArrowUp className="mr-1 size-3" />
              ) : (
                <ArrowDown className="mr-1 size-3" />
              )}
              {call.direction}
            </Badge>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Level</span>
            <span className="font-mono text-sm text-foreground">{meta.level.toFixed(2)}</span>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Call (ET)</span>
            <span className="font-mono text-sm text-foreground">{formatET(meta.post_ts)}</span>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Entry</span>
            <span className="font-mono text-sm text-foreground">{formatTimeET(meta.entry_ts)}</span>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Exit</span>
            <span className="font-mono text-sm text-foreground">{formatTimeET(meta.exit_ts)}</span>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">P&amp;L</span>
            <span className={`font-mono text-sm font-semibold ${pnlColor}`}>
              {pnl != null ? `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} pts` : "—"}
            </span>
          </div>

          {meta.exit_reason && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Reason</span>
              <span className="text-sm capitalize text-muted-foreground">{meta.exit_reason}</span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── DOM Ladder ────────────────────────────────────────────────────────────────

type LadderRow = {
  price:     number;
  bid:       number;
  ask:       number;
  isBestBid: boolean;
  isBestAsk: boolean;
};

function buildLadderRows(snapshot: BookSnapshot): LadderRow[] {
  const priceMap = new Map<number, { bid: number; ask: number }>();
  for (const b of snapshot.bids) priceMap.set(b.price, { bid: b.size, ask: 0 });
  for (const a of snapshot.asks) {
    const existing = priceMap.get(a.price);
    if (existing) existing.ask = a.size;
    else priceMap.set(a.price, { bid: 0, ask: a.size });
  }
  const bestBid = snapshot.bids[0]?.price ?? -1;
  const bestAsk = snapshot.asks[0]?.price ?? Infinity;
  const prices  = [...priceMap.keys()].sort((a, b) => b - a);
  return prices.map((p) => ({
    price: p,
    bid:   priceMap.get(p)!.bid,
    ask:   priceMap.get(p)!.ask,
    isBestBid: p === bestBid,
    isBestAsk: p === bestAsk,
  }));
}

/**
 * DOM Ladder — driven by cursorMs.
 * Uses fine-grained (1s resolution) book snapshots when available so the
 * displayed snapshot time advances in ~1s steps while scrubbing.
 * Falls back to the coarse (30s) book snapshots otherwise.
 */
function DomLadder({
  coarseSnapshots,
  fineSnapshots,
  cursorMs,
  fineBookLoading,
}: {
  coarseSnapshots: BookSnapshot[];
  fineSnapshots:   BookSnapshot[];
  cursorMs:        number | null;
  fineBookLoading: boolean;
}) {
  // Prefer fine snapshots (1s) over coarse (30s) when available
  const snapshots = fineSnapshots.length > 0 ? fineSnapshots : coarseSnapshots;

  const snapIdx = useMemo(() => {
    const base = Math.floor(snapshots.length * 0.5);
    if (cursorMs == null || snapshots.length === 0) return base;
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < snapshots.length; i++) {
      const d = Math.abs(snapshots[i].t - cursorMs);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
  }, [snapshots, cursorMs]);

  const snapshot = snapshots[snapIdx];
  const rows = useMemo(() => (snapshot ? buildLadderRows(snapshot) : []), [snapshot]);
  const maxSize = useMemo(
    () => Math.max(...rows.flatMap((r) => [r.bid, r.ask]), 1),
    [rows],
  );

  if (coarseSnapshots.length === 0 && !fineBookLoading) {
    return <p className="text-sm text-muted-foreground">No book data available.</p>;
  }

  const resLabel = fineSnapshots.length > 0 ? "1s" : "30s";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Book snapshot
          {fineBookLoading && (
            <span className="ml-2 text-blue-400/60">loading 1s…</span>
          )}
        </span>
        <span
          className="font-mono text-xs text-muted-foreground"
          data-testid="ladder-snapshot-time"
        >
          {formatTimeET(snapshot?.t)} ET
        </span>
      </div>

      <div className="flex items-center justify-between text-[9px] text-muted-foreground/50 font-mono">
        <span>res={resLabel} · {snapshots.length} snapshots</span>
        {fineSnapshots.length > 0 && (
          <span data-testid="fine-book-ready" className="text-green-500/60">1s ✓</span>
        )}
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[1fr_auto_1fr] text-[10px] uppercase tracking-wider text-muted-foreground">
        <span className="pr-2 text-right">Bid</span>
        <span className="px-3 text-center">Price</span>
        <span className="pl-2">Ask</span>
      </div>

      {/* Ladder rows */}
      <div
        className="overflow-y-auto rounded border border-border/30 font-mono text-xs"
        style={{ maxHeight: 380 }}
      >
        {rows.map((row) => (
          <div
            key={row.price}
            className={[
              "grid grid-cols-[1fr_auto_1fr]",
              row.isBestBid
                ? "bg-emerald-900/25"
                : row.isBestAsk
                  ? "bg-red-900/25"
                  : "",
            ].join(" ")}
          >
            {/* Bid side */}
            <div className="relative flex items-center justify-end py-[3px] pr-2">
              {row.bid > 0 && (
                <>
                  <div
                    className="absolute inset-y-0 right-0 bg-emerald-600/25"
                    style={{ width: `${(row.bid / maxSize) * 100}%` }}
                  />
                  <span
                    className={`relative z-10 ${
                      row.isBestBid
                        ? "font-semibold text-emerald-300"
                        : "text-emerald-600"
                    }`}
                  >
                    {row.bid.toLocaleString()}
                  </span>
                </>
              )}
            </div>

            {/* Price */}
            <div
              className={`border-x border-border/20 px-3 py-[3px] text-center ${
                row.isBestBid || row.isBestAsk
                  ? "font-semibold text-foreground"
                  : "text-muted-foreground/70"
              }`}
            >
              {row.price.toFixed(2)}
            </div>

            {/* Ask side */}
            <div className="relative flex items-center py-[3px] pl-2">
              {row.ask > 0 && (
                <>
                  <div
                    className="absolute inset-y-0 left-0 bg-red-600/25"
                    style={{ width: `${(row.ask / maxSize) * 100}%` }}
                  />
                  <span
                    className={`relative z-10 ${
                      row.isBestAsk
                        ? "font-semibold text-red-300"
                        : "text-red-500"
                    }`}
                  >
                    {row.ask.toLocaleString()}
                  </span>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      <p className="text-right text-[10px] text-muted-foreground">
        driven by scrubber · {resLabel} resolution
      </p>
    </div>
  );
}

// ── Order Tape ────────────────────────────────────────────────────────────────

const ACTION_LABELS: Record<string, string> = {
  A: "Add",
  C: "Cancel",
  M: "Modify",
  T: "Trade",
  F: "Fill",
};

const SIDE_STYLE: Record<string, { label: string; cls: string }> = {
  B: { label: "Bid", cls: "text-green-400" },
  A: { label: "Ask", cls: "text-red-400" },
  N: { label: "—",   cls: "text-muted-foreground" },
};

function OrderTape({
  events,
  cursorMs,
  windowMs,
  loading,
}: {
  events:   TapeEvent[];
  cursorMs: number | null;
  windowMs: number;
  loading:  boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [events]);

  if (events.length === 0 && !loading) {
    return <p className="text-sm text-muted-foreground">No tape events in this window.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {loading && (
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground/50">
          <Loader2 className="size-3 animate-spin" />
          Loading tape…
        </div>
      )}

      <div className="grid grid-cols-[7rem_4rem_3rem_5rem_4rem] gap-x-2 px-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <span>Time (ET)</span>
        <span>Action</span>
        <span>Side</span>
        <span className="text-right">Price</span>
        <span className="text-right">Size</span>
      </div>

      <div
        ref={scrollRef}
        className="overflow-y-auto rounded border border-border/30 font-mono text-xs"
        style={{ maxHeight: 360 }}
      >
        {events.slice(0, 500).map((e, i) => {
          const sideStyle = SIDE_STYLE[e.side] ?? { label: e.side, cls: "text-muted-foreground" };
          return (
            <div
              key={`${e.t}-${e.order_id}-${i}`}
              className={[
                "grid grid-cols-[7rem_4rem_3rem_5rem_4rem] gap-x-2 px-2 py-[3px]",
                i % 2 === 0 ? "bg-card/20" : "",
                "hover:bg-muted/20",
              ].join(" ")}
            >
              <span
                className="text-muted-foreground/70"
                data-testid="tape-event-time"
              >
                {formatTimeMsET(e.t)}
              </span>
              <span className="text-muted-foreground">
                {ACTION_LABELS[e.action] ?? e.action}
              </span>
              <span className={`font-medium ${sideStyle.cls}`}>
                {sideStyle.label}
              </span>
              <span className="text-right text-foreground">
                {e.price.toFixed(2)}
              </span>
              <span className="text-right text-muted-foreground">
                {e.size.toLocaleString()}
              </span>
            </div>
          );
        })}
      </div>

      <p className="text-right text-[10px] text-muted-foreground">
        {events.length.toLocaleString()} events
        {cursorMs != null && ` · ±${Math.round(windowMs / 60_000)}min around cursor`}
      </p>
    </div>
  );
}

// ── Main viewer ───────────────────────────────────────────────────────────────

export interface OrderFlowViewerProps {
  /** When provided, overrides the URL ?call= param. Use for dashboard widget mounting. */
  callId?: string;
}

export function OrderFlowViewer({ callId: propCallId }: OrderFlowViewerProps = {}) {
  const searchParams = useSearchParams();
  const router       = useRouter();
  const initialCall  = propCallId ?? searchParams.get("call");

  // ── Call list state ────────────────────────────────────────────────────
  const [calls, setCalls]               = useState<OrderFlowCall[]>([]);
  const [callsLoading, setCallsLoading] = useState(true);
  const [callsError, setCallsError]     = useState<string | null>(null);
  const [selectedPostId, setSelectedPostId] = useState<string | null>(initialCall);
  const [callFilter, setCallFilter]         = useState<CallFilter>("all");

  useEffect(() => {
    setCallsLoading(true);
    fetchOrderFlowCalls()
      .then((data) => {
        setCalls(data);
        setCallsLoading(false);
        setSelectedPostId((current) => {
          if (current) return current;
          return data.find((c) => c.has_mbo)?.post_id ?? null;
        });
      })
      .catch((err: unknown) => {
        setCallsError(err instanceof Error ? err.message : "Failed to load calls");
        setCallsLoading(false);
      });
  }, []);

  const filteredCalls = useMemo(() => {
    return calls.filter((c) => {
      if (callFilter === "mbo")     return c.has_mbo;
      if (callFilter === "winners") return c.has_mbo && callOutcome(c) === "WIN";
      if (callFilter === "losers")  return c.has_mbo && callOutcome(c) === "LOSS";
      return true;
    });
  }, [calls, callFilter]);

  const handleSelectCall = useCallback(
    (postId: string | null) => {
      setSelectedPostId(postId || null);
      setCursorMs(null);
      setPlaying(false);
      if (!propCallId) {
        const params = new URLSearchParams(searchParams.toString());
        if (postId) params.set("call", postId);
        else params.delete("call");
        router.replace(`?${params.toString()}`, { scroll: false });
      }
    },
    [searchParams, router, propCallId],
  );

  // ── Static data (meta + candles + footprint + coarse book + initial tape) ──
  const { meta, book, tape, candles, footprint, loading, error } =
    useOrderFlowData(selectedPostId);
  const selectedCall = calls.find((c) => c.post_id === selectedPostId);

  // ── Master cursor state ────────────────────────────────────────────────
  // ONLY updated by: timeline scrub, play button. Chart hover does NOT touch this.
  const [cursorMs, setCursorMs]   = useState<number | null>(null);
  const [playing, setPlaying]     = useState(false);
  const [playSpeed, setPlaySpeed] = useState<PlaySpeed>(1);

  // ── Hover state (chart hover only — readout, not master cursor) ────────
  const [hoverMs, setHoverMs]       = useState<number | null>(null);
  const [hoverPrice, setHoverPrice] = useState<number | null>(null);

  // Reset on call change
  useEffect(() => {
    setCursorMs(null);
    setHoverMs(null);
    setHoverPrice(null);
    setPlaying(false);
  }, [selectedPostId]);

  // ── window.__ofState — readable by Playwright tests ───────────────────
  const viewStateRef = useRef({ timeMin: 0, timeMax: 0, priceMin: 0, priceMax: 0 });

  const handleViewChange = useCallback(
    (timeMin: number, timeMax: number, priceMin: number, priceMax: number) => {
      viewStateRef.current = { timeMin, timeMax, priceMin, priceMax };
      if (typeof window !== "undefined") {
        (window as Window & typeof globalThis & { __ofState?: unknown }).__ofState = {
          timeMin,
          timeMax,
          priceMin,
          priceMax,
          cursorMs,
        };
      }
    },
    [cursorMs],
  );

  // Also update __ofState when cursorMs changes (view stays the same)
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as Window & typeof globalThis & { __ofState?: unknown }).__ofState = {
      ...viewStateRef.current,
      cursorMs,
    };
  }, [cursorMs]);

  // Expose a helper for tests to set cursor programmatically
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as Window & typeof globalThis & { __ofSetCursor?: (ms: number) => void }).__ofSetCursor =
      (ms: number) => {
        setCursorMs(ms);
        setPlaying(false);
      };
    return () => {
      delete (window as Window & typeof globalThis & { __ofSetCursor?: (ms: number) => void })
        .__ofSetCursor;
    };
  }, []);

  // ── Fine book state (1s resolution for DOM ladder) ────────────────────
  const [fineBook, setFineBook]           = useState<{ snapshots: BookSnapshot[] } | null>(null);
  const [fineBookLoading, setFineBookLoading] = useState(false);
  const fineBookFetchIdRef                = useRef(0);
  const lastFineBookBucketRef             = useRef<number | null>(null);

  // Fetch fine book on initial load (around post_ts) when meta becomes available
  useEffect(() => {
    if (!selectedPostId || !meta) return;
    const bucket = Math.floor(meta.post_ts / BOOK_FINE_BUCKET_MS);
    if (bucket === lastFineBookBucketRef.current) return;
    lastFineBookBucketRef.current = bucket;

    const fetchId = ++fineBookFetchIdRef.current;
    setFineBookLoading(true);
    setFineBook(null);

    const fromMs = meta.post_ts - BOOK_FINE_WINDOW_MS;
    const toMs   = meta.post_ts + BOOK_FINE_WINDOW_MS;

    fetchOrderFlowBookFine(selectedPostId, fromMs, toMs, "1s")
      .then((data) => {
        if (fineBookFetchIdRef.current === fetchId) {
          setFineBook(data);
          setFineBookLoading(false);
        }
      })
      .catch(() => {
        if (fineBookFetchIdRef.current === fetchId) {
          setFineBookLoading(false);
        }
      });
  }, [selectedPostId, meta]);

  // Re-fetch fine book when cursor moves to a new 5-minute bucket
  useEffect(() => {
    if (!selectedPostId || cursorMs == null) return;
    const bucket = Math.floor(cursorMs / BOOK_FINE_BUCKET_MS);
    if (bucket === lastFineBookBucketRef.current) return;
    lastFineBookBucketRef.current = bucket;

    const fetchId = ++fineBookFetchIdRef.current;
    setFineBookLoading(true);

    const fromMs = cursorMs - BOOK_FINE_WINDOW_MS;
    const toMs   = cursorMs + BOOK_FINE_WINDOW_MS;

    fetchOrderFlowBookFine(selectedPostId, fromMs, toMs, "1s")
      .then((data) => {
        if (fineBookFetchIdRef.current === fetchId) {
          setFineBook(data);
          setFineBookLoading(false);
        }
      })
      .catch(() => {
        if (fineBookFetchIdRef.current === fetchId) {
          setFineBookLoading(false);
        }
      });
  }, [selectedPostId, cursorMs != null ? Math.floor(cursorMs / BOOK_FINE_BUCKET_MS) : null]);

  // ── Windowed tape state (±2min around cursor) ──────────────────────────
  const [windowedTape, setWindowedTape]       = useState<TapeEvent[] | null>(null);
  const [windowedTapeLoading, setWindowedTapeLoading] = useState(false);
  const tapeFetchIdRef                        = useRef(0);
  const lastTapeBucketRef                     = useRef<number | null>(null);

  useEffect(() => {
    if (!selectedPostId || cursorMs == null) return;
    const bucket = Math.floor(cursorMs / TAPE_BUCKET_MS);
    if (bucket === lastTapeBucketRef.current) return;
    lastTapeBucketRef.current = bucket;

    const fetchId = ++tapeFetchIdRef.current;
    setWindowedTapeLoading(true);

    const fromMs = cursorMs - TAPE_WINDOW_MS;
    const toMs   = cursorMs + TAPE_WINDOW_MS;

    fetchOrderFlowTapeWindow(selectedPostId, fromMs, toMs, 300)
      .then((data) => {
        if (tapeFetchIdRef.current === fetchId) {
          setWindowedTape(data.events);
          setWindowedTapeLoading(false);
        }
      })
      .catch(() => {
        if (tapeFetchIdRef.current === fetchId) {
          setWindowedTapeLoading(false);
        }
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPostId, cursorMs != null ? Math.floor(cursorMs / TAPE_BUCKET_MS) : null]);

  // Displayed tape: windowed (when cursor active) or initial full tape
  const displayedTapeEvents: TapeEvent[] =
    windowedTape ?? tape?.events ?? [];

  // ── Playback loop ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing || !meta) return;
    const id = setInterval(() => {
      setCursorMs((prev) => {
        const cur  = prev ?? meta.window_start;
        const next = cur + PLAY_MARKET_BASE_MS * playSpeed;
        if (next >= meta.window_end) {
          setPlaying(false);
          return meta.window_end;
        }
        return next;
      });
    }, PLAY_REAL_TICK_MS);
    return () => clearInterval(id);
  }, [playing, playSpeed, meta]);

  // ── Canvas hover handler (readout only — does NOT drive master cursor) ──
  const handleHoverMove = useCallback(
    (ms: number | null, price: number | null) => {
      setHoverMs(ms);
      setHoverPrice(price);
    },
    [],
  );

  const handleTogglePlay = useCallback(() => {
    setPlaying((p) => {
      if (!p && meta && (cursorMs == null || cursorMs >= meta.window_end)) {
        setCursorMs(meta.window_start);
      }
      return !p;
    });
  }, [meta, cursorMs]);

  const handleScrub = useCallback((ms: number) => {
    setPlaying(false);
    setCursorMs(ms);
  }, []);

  // The CrosshairReadout shows hover position if active, else cursor position
  const displayMs    = hoverMs ?? cursorMs;
  const displayPrice = hoverMs != null ? hoverPrice : null;

  return (
    <div className="space-y-4">
      {/* Header + call selector */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-medium tracking-tight text-foreground">
              Order Flow
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              DOM ladder · order tape · /ES MBO data
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {callsLoading && (
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            )}

            {/* Filter buttons */}
            <div className="flex gap-0.5 rounded border border-border/30 p-0.5">
              {(["all", "winners", "losers", "mbo"] as CallFilter[]).map((f) => (
                <button
                  key={f}
                  onClick={() => setCallFilter(f)}
                  className={[
                    "rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider transition",
                    callFilter === f
                      ? f === "winners"
                        ? "bg-green-700/60 text-green-200"
                        : f === "losers"
                          ? "bg-red-700/60 text-red-200"
                          : "bg-muted/60 text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  ].join(" ")}
                >
                  {f === "mbo" ? "MBO only" : f}
                  {f !== "all" && (
                    <span className="ml-1 opacity-60">
                      ({f === "winners"
                        ? calls.filter((c) => c.has_mbo && callOutcome(c) === "WIN").length
                        : f === "losers"
                          ? calls.filter((c) => c.has_mbo && callOutcome(c) === "LOSS").length
                          : calls.filter((c) => c.has_mbo).length})
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Call dropdown */}
            <Select
              value={selectedPostId ?? ""}
              onValueChange={handleSelectCall}
            >
              <SelectTrigger className="w-[420px]">
                <SelectValue placeholder="Select a call…" />
              </SelectTrigger>
              <SelectContent className="max-h-96">
                {filteredCalls.map((c) => {
                  const outcome = callOutcome(c);
                  return (
                    <SelectItem
                      key={c.post_id}
                      value={c.post_id}
                      disabled={!c.has_mbo}
                    >
                      <span className={c.has_mbo ? "" : "opacity-40"}>
                        {outcome && (
                          <span
                            className={`mr-2 inline-block rounded px-1 text-[8px] font-bold uppercase ${
                              outcome === "WIN"
                                ? "bg-green-900/60 text-green-300"
                                : "bg-red-900/60 text-red-300"
                            }`}
                          >
                            {outcome}
                          </span>
                        )}
                        {callLabel(c)}
                        {c.has_mbo && (
                          <span className="ml-2 rounded bg-blue-900/50 px-1 text-[9px] font-medium uppercase tracking-wider text-blue-400">
                            MBO
                          </span>
                        )}
                      </span>
                    </SelectItem>
                  );
                })}
                {filteredCalls.length === 0 && (
                  <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                    No calls match the filter.
                  </div>
                )}
              </SelectContent>
            </Select>
          </div>
        </div>

        {!callsLoading && calls.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {calls.filter((c) => c.has_mbo).length} MBO calls ·{" "}
            {calls.filter((c) => c.has_mbo && callOutcome(c) === "WIN").length} winners ·{" "}
            {calls.filter((c) => c.has_mbo && callOutcome(c) === "LOSS").length} losers ·{" "}
            {calls.length} total
          </p>
        )}
      </div>

      {callsError && (
        <div className="rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {callsError}
        </div>
      )}

      {!selectedPostId && !callsLoading && !callsError && (
        <div className="rounded border border-border/30 bg-muted/10 p-8 text-center text-sm text-muted-foreground">
          Select a call above to view its order flow.
        </div>
      )}

      {selectedCall && !selectedCall.has_mbo && (
        <div className="rounded border border-amber-500/30 bg-amber-950/20 p-4 text-sm text-amber-400">
          No MBO parquet data for this call.
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading order flow data…
        </div>
      )}

      {error && !loading && (
        <div className="rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {meta && selectedCall && !loading && (
        <MetaHeader meta={meta} call={selectedCall} />
      )}

      {/* ── Chart panes + crosshair readout + timeline ─────────────────── */}
      {meta && candles && footprint && !loading && !error && (
        <>
          {/* Chart card */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <BarChart2 className="size-4 text-muted-foreground" />
                Chart — candles · footprint · delta · heatmap
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <OrderFlowCanvas
                candles={candles.bars}
                footprint={footprint}
                book={book ?? undefined}
                meta={meta}
                cursorMs={cursorMs}
                onHoverMove={handleHoverMove}
                onViewChange={handleViewChange}
              />
              {/* Crosshair readout strip — shows hover data (not master cursor) */}
              <CrosshairReadout
                displayMs={displayMs}
                displayPrice={displayPrice}
                candles={candles.bars}
                footprint={footprint}
              />
            </CardContent>
          </Card>

          {/* Timeline scrubber + playback (click-and-hold-drag to set cursor) */}
          <TimeScrubber
            meta={meta}
            cursorMs={cursorMs}
            playing={playing}
            playSpeed={playSpeed}
            onScrub={handleScrub}
            onTogglePlay={handleTogglePlay}
            onSpeedChange={setPlaySpeed}
          />
        </>
      )}

      {/* ── Tape + Ladder ──────────────────────────────────────────────── */}
      {meta && !loading && !error && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
          {/* Order Tape */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Activity className="size-4 text-muted-foreground" />
                Order Tape
                {cursorMs != null && (
                  <span className="ml-auto text-[10px] font-normal text-muted-foreground/50">
                    ±{Math.round(TAPE_WINDOW_MS / 60_000)}min around cursor
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <OrderTape
                events={displayedTapeEvents}
                cursorMs={cursorMs}
                windowMs={TAPE_WINDOW_MS}
                loading={windowedTapeLoading}
              />
            </CardContent>
          </Card>

          {/* DOM Ladder */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <BookOpen className="size-4 text-muted-foreground" />
                DOM Ladder
              </CardTitle>
            </CardHeader>
            <CardContent>
              <DomLadder
                key={selectedPostId ?? ""}
                coarseSnapshots={book?.snapshots ?? []}
                fineSnapshots={fineBook?.snapshots ?? []}
                cursorMs={cursorMs}
                fineBookLoading={fineBookLoading}
              />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
