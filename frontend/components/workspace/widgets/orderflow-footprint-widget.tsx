"use client";

/**
 * orderflow-footprint-widget.tsx
 *
 * Workspace widget: Footprint+Candle+Delta canvas.
 *
 * Responsibilities:
 *   - Owns the call selector (compact dropdown inside the widget content)
 *   - Fetches core data (meta, candles, footprint, book, tape) and writes to session store
 *   - Renders OrderFlowCanvas (heatmap excluded — that's its own widget)
 *   - Renders a compact timeline scrubber + playback controls
 *   - Handles fine book + windowed tape loading for the other widgets
 *
 * All four order-flow widgets share state via useOrderFlowSession.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Loader2, Pause, Play } from "lucide-react";

import { OrderFlowCanvas } from "@/components/orderflow/order-flow-canvas";
import { buildLiveBars } from "@/lib/orderflow/build-live-bars";
import {
  fetchOrderFlowBookFine,
  fetchOrderFlowCalls,
  fetchOrderFlowTapeWindow,
  fetchOrderFlowTrades,
  formatTimeET,
  type OrderFlowCall,
} from "@/lib/orderflow-api";
import { useOrderFlowData } from "@/lib/use-orderflow-data";
import { useOrderFlowSession } from "@/lib/orderflow/use-orderflow-session";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

// ── Constants ─────────────────────────────────────────────────────────────────
const TAPE_WINDOW_MS = 2 * 60_000;
const TAPE_BUCKET_MS = 60_000;

// Playback speeds: 1× = real market time (live feel). Higher = fast-forward.
type PlaySpeed = 1 | 2 | 4 | 10 | 30;
const PLAY_SPEEDS: PlaySpeed[] = [1, 2, 4, 10, 30];

// ── Compact call selector ─────────────────────────────────────────────────────

function callLabel(c: OrderFlowCall): string {
  const dir = c.direction.toLowerCase() === "long" ? "▲" : "▼";
  const pnl =
    c.points_net != null
      ? c.points_net >= 0
        ? `+${c.points_net.toFixed(2)}`
        : c.points_net.toFixed(2)
      : "—";
  const tag =
    c.points_net != null ? (c.points_net >= 0 ? " [W]" : " [L]") : "";
  return `${c.date}  ${dir} @${c.level.toFixed(2)}  ${pnl}${tag}`;
}

// ── Compact scrubber ──────────────────────────────────────────────────────────

function MiniScrubber({
  windowStart,
  windowEnd,
  postTs,
  entryTs,
  exitTs,
  cursorMs,
  playing,
  playSpeed,
  onScrub,
  onTogglePlay,
  onSpeedChange,
}: {
  windowStart: number;
  windowEnd:   number;
  postTs:      number;
  entryTs:     number | null;
  exitTs:      number | null;
  cursorMs:    number | null;
  playing:     boolean;
  playSpeed:   PlaySpeed;
  onScrub:     (ms: number) => void;
  onTogglePlay: () => void;
  onSpeedChange: (s: PlaySpeed) => void;
}) {
  const span     = Math.max(1, windowEnd - windowStart);
  const value    = cursorMs ?? postTs;
  const pct      = ((value - windowStart) / span) * 100;
  const postPct  = ((postTs - windowStart) / span) * 100;
  const entryPct = entryTs != null ? ((entryTs - windowStart) / span) * 100 : null;
  const exitPct  = exitTs  != null ? ((exitTs  - windowStart) / span) * 100 : null;

  const trackRef  = useRef<HTMLDivElement>(null);
  const scrubbing = useRef(false);
  const onScrubRef  = useRef(onScrub);
  const windowRef   = useRef({ start: windowStart, end: windowEnd });
  useEffect(() => { onScrubRef.current = onScrub; }, [onScrub]);
  useEffect(() => { windowRef.current = { start: windowStart, end: windowEnd }; }, [windowStart, windowEnd]);

  const xToMs = useCallback((clientX: number): number => {
    const el = trackRef.current;
    if (!el) return windowRef.current.start;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    return Math.round(windowRef.current.start + frac * (windowRef.current.end - windowRef.current.start));
  }, []);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const onDown = (e: MouseEvent) => { e.preventDefault(); scrubbing.current = true; onScrubRef.current(xToMs(e.clientX)); };
    const onMove = (e: MouseEvent) => { if (scrubbing.current) onScrubRef.current(xToMs(e.clientX)); };
    const onUp   = () => { scrubbing.current = false; };
    track.addEventListener("mousedown", onDown);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      track.removeEventListener("mousedown", onDown);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="flex items-center gap-2 border-t border-[var(--border)] px-3 py-1.5"
      style={{ background: "var(--bg-inset)" }}
    >
      {/* Play/Pause */}
      <button
        onClick={onTogglePlay}
        className="flex size-6 shrink-0 items-center justify-center rounded border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--fg-dim)] transition hover:border-[var(--border-strong)] hover:text-[var(--foreground)]"
      >
        {playing ? <Pause className="size-3" /> : <Play className="size-3" />}
      </button>

      {/* Speed */}
      <div className="flex shrink-0 gap-0.5">
        {PLAY_SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            className={[
              "rounded px-1 py-0.5 font-mono text-[9px] transition",
              s === playSpeed
                ? "bg-[var(--c-blue)]/20 text-[var(--c-blue)]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            ].join(" ")}
          >
            {s}×
          </button>
        ))}
      </div>

      {/* Track */}
      <div
        ref={trackRef}
        data-testid="timeline-track"
        className="relative flex-1 cursor-col-resize select-none"
        style={{ height: 20 }}
      >
        <div
          className="absolute inset-x-0 rounded-full bg-[var(--bg-hover)]"
          style={{ top: "50%", transform: "translateY(-50%)", height: 4 }}
        />
        <div
          className="absolute left-0 rounded-full"
          style={{
            top: "50%",
            transform: "translateY(-50%)",
            height: 4,
            width: `${Math.max(0, Math.min(100, pct))}%`,
            background: "var(--c-blue)",
            opacity: 0.5,
          }}
        />
        <div className="pointer-events-none absolute inset-0">
          {postPct  != null && (
            <div className="absolute rounded" style={{ left: `${postPct}%`, top: "50%", transform: "translate(-50%,-50%)", width: 2, height: 14, background: "var(--c-blue)", opacity: 0.7 }} />
          )}
          {entryPct != null && (
            <div className="absolute rounded" style={{ left: `${entryPct}%`, top: "50%", transform: "translate(-50%,-50%)", width: 2, height: 14, background: "var(--c-green)", opacity: 0.7 }} />
          )}
          {exitPct  != null && (
            <div className="absolute rounded" style={{ left: `${exitPct}%`, top: "50%", transform: "translate(-50%,-50%)", width: 2, height: 14, background: "var(--c-red)", opacity: 0.7 }} />
          )}
        </div>
        {cursorMs != null && (
          <div
            className="pointer-events-none absolute rounded"
            style={{
              left:      `${Math.max(0, Math.min(100, pct))}%`,
              top:       "50%",
              transform: "translate(-50%, -50%)",
              width:     8,
              height:    14,
              background: "var(--c-blue)",
            }}
          />
        )}
      </div>

      {/* Timestamp */}
      <span className="w-16 shrink-0 text-right font-mono text-[10px] text-[var(--fg-dim)]">
        {formatTimeET(value)}
      </span>
    </div>
  );
}

// ── Main widget ────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function OrderflowFootprintWidget(_props: WidgetProps) {
  // ── Zustand selectors (stable refs — no full-store re-render on every update) ─
  const callId      = useOrderFlowSession((s) => s.callId);
  const cursorMs    = useOrderFlowSession((s) => s.cursorMs);
  const trades      = useOrderFlowSession((s) => s.trades);
  const setData     = useOrderFlowSession((s) => s.setData);
  const setCursorMs = useOrderFlowSession((s) => s.setCursorMs);
  const setViewMs   = useOrderFlowSession((s) => s.setViewMs);
  const setCallId   = useOrderFlowSession((s) => s.setCallId);

  // ── Call list ──────────────────────────────────────────────────────────────
  const [calls, setCalls]               = useState<OrderFlowCall[]>([]);
  const [callsLoading, setCallsLoading] = useState(false);

  useEffect(() => {
    setCallsLoading(true);
    fetchOrderFlowCalls()
      .then((data) => {
        setCalls(data);
        setCallsLoading(false);
        // Auto-select first MBO call if nothing is selected
        useOrderFlowSession.setState((s) => {
          if (s.callId) return s;
          const first = data.find((c) => c.has_mbo);
          return first ? { callId: first.post_id, dataLoading: true } : s;
        });
      })
      .catch(() => setCallsLoading(false));
  }, []);

  const mboOnlyCalls = useMemo(() => {
    // Dedupe by post_id — the calls CSV has a few duplicate post_ids, which
    // collide on the <option> React key and flood the console on every
    // playback frame. Keep the first occurrence of each.
    const seen = new Set<string>();
    return calls.filter((c) => {
      if (!c.has_mbo || seen.has(c.post_id)) return false;
      seen.add(c.post_id);
      return true;
    });
  }, [calls]);

  // ── Data loading ───────────────────────────────────────────────────────────
  const { meta, book, tape, candles, footprint, loading, error } =
    useOrderFlowData(callId);

  // Write fetched data to session store so other widgets can read it
  useEffect(() => {
    setData({
      meta:        meta ?? null,
      book:        book ?? null,
      candles:     candles ?? null,
      footprint:   footprint ?? null,
      tape:        tape ?? null,
      dataLoading: loading,
      dataError:   error,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, book, tape, candles, footprint, loading, error]);

  // ── Cursor / playback ──────────────────────────────────────────────────────
  const [playing, setPlaying]     = useState(false);
  const [playSpeed, setPlaySpeed] = useState<PlaySpeed>(1);

  useEffect(() => {
    setCursorMs(null);
    setPlaying(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId]);

  // Smooth real-time playback clock: market time advances at wall-clock rate ×
  // speed (1× = live). requestAnimationFrame for 60fps; performance.now() deltas
  // keep it frame-rate independent. The panes read cursorMs and redraw each frame.
  useEffect(() => {
    if (!playing || !meta) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt  = now - last;          // wall-clock ms since last frame
      last = now;
      const cur  = useOrderFlowSession.getState().cursorMs ?? meta.window_start;
      const next = cur + dt * playSpeed; // wall ms × speed = market ms advanced
      if (next >= meta.window_end) {
        setCursorMs(meta.window_end);
        setPlaying(false);
        return;
      }
      setCursorMs(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, playSpeed, meta, setCursorMs]);

  const handleScrub = useCallback((ms: number) => {
    setPlaying(false);
    setCursorMs(ms);
  }, [setCursorMs]);

  const handleTogglePlay = useCallback(() => {
    setPlaying((p) => {
      if (!p && meta) {
        const cur = useOrderFlowSession.getState().cursorMs;
        if (cur == null || cur >= meta.window_end) setCursorMs(meta.window_start);
      }
      return !p;
    });
  }, [meta, setCursorMs]);

  // ── Fine book (for DOM + heatmap widgets) ──────────────────────────────────
  const fineBookFetchIdRef = useRef(0);

  useEffect(() => {
    if (!callId || !meta) return;
    const fetchId = ++fineBookFetchIdRef.current;
    setData({ fineBookLoading: true, fineSnapshots: [] });
    fetchOrderFlowBookFine(callId, meta.window_start, meta.window_end, "1s")
      .then((d) => {
        if (fineBookFetchIdRef.current === fetchId)
          setData({ fineSnapshots: d.snapshots, fineBookLoading: false });
      })
      .catch(() => {
        if (fineBookFetchIdRef.current === fetchId)
          setData({ fineBookLoading: false });
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId, meta]);

  // ── Trades (for tick-by-tick footprint + candle fill) ─────────────────────
  const tradesFetchIdRef = useRef(0);

  useEffect(() => {
    if (!callId || !meta) return;
    const fetchId = ++tradesFetchIdRef.current;
    setData({ trades: [] });
    fetchOrderFlowTrades(callId, meta.window_start, meta.window_end)
      .then((data) => {
        if (tradesFetchIdRef.current === fetchId)
          setData({ trades: data });
      })
      .catch(() => {
        if (tradesFetchIdRef.current === fetchId)
          setData({ trades: [] });
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId, meta]);

  // ── Windowed tape (for tape widget) ───────────────────────────────────────
  const tapeFetchIdRef    = useRef(0);
  const lastTapeBucketRef = useRef<number | null>(null);

  const tapeBucket = cursorMs != null ? Math.floor(cursorMs / TAPE_BUCKET_MS) : null;
  useEffect(() => {
    if (!callId || cursorMs == null) return;
    const bucket = Math.floor(cursorMs / TAPE_BUCKET_MS);
    if (bucket === lastTapeBucketRef.current) return;
    lastTapeBucketRef.current = bucket;
    const fetchId = ++tapeFetchIdRef.current;
    setData({ windowedTapeLoading: true });
    fetchOrderFlowTapeWindow(callId, cursorMs - TAPE_WINDOW_MS, cursorMs + TAPE_WINDOW_MS, 300)
      .then((d) => {
        if (tapeFetchIdRef.current === fetchId)
          setData({ windowedTapeEvents: d.events, windowedTapeLoading: false });
      })
      .catch(() => {
        if (tapeFetchIdRef.current === fetchId)
          setData({ windowedTapeLoading: false });
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId, tapeBucket]);

  // ── View change → session store (heatmap follows) ─────────────────────────
  const handleViewChange = useCallback(
    (timeMin: number, timeMax: number) => {
      setViewMs({ start: timeMin, end: timeMax });
    },
    [setViewMs],
  );

  // ── Hover state (readout only) ─────────────────────────────────────────────
  const [hoverMs, setHoverMs]       = useState<number | null>(null);
  const [hoverPrice, setHoverPrice] = useState<number | null>(null);
  const handleHoverMove = useCallback((ms: number | null, price: number | null) => {
    setHoverMs(ms);
    setHoverPrice(price);
  }, []);

  // ── Live footprint + candles (completed bars pre-agg; current bar from trades) ─
  const liveBars = useMemo(() => {
    if (!footprint || !candles) return null;
    return buildLiveBars(
      footprint,
      candles.bars,
      trades,
      cursorMs,
    );
  }, [footprint, candles, trades, cursorMs, meta]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full flex-col" style={{ background: "var(--panel)" }}>

      {/* ── Call selector ─────────────────────────────────────────────────── */}
      <div
        className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-3 py-1.5"
        style={{ background: "var(--bg-inset)" }}
      >
        <span className="font-mono text-[9px] uppercase tracking-widest text-[var(--fg-faint)]">
          Call
        </span>
        {callsLoading && <Loader2 className="size-3 animate-spin text-[var(--fg-faint)]" />}
        <select
          value={callId ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            setCallId(v || null);
          }}
          className="flex-1 rounded border border-[var(--border)] bg-[var(--bg-hover)] px-2 py-0.5 font-mono text-[10px] text-[var(--fg-dim)] outline-none focus:border-[var(--c-accent)]"
        >
          <option value="">— select a call —</option>
          {mboOnlyCalls.map((c) => (
            <option key={c.post_id} value={c.post_id}>
              {callLabel(c)}
            </option>
          ))}
        </select>
        {loading && <Loader2 className="size-3 animate-spin text-[var(--fg-faint)]" />}
      </div>

      {/* ── Error ─────────────────────────────────────────────────────────── */}
      {error && (
        <div className="shrink-0 border-b border-[var(--c-red)]/20 bg-[var(--c-red)]/10 px-3 py-1.5 font-mono text-[10px] text-[var(--c-red)]">
          {error}
        </div>
      )}

      {/* ── Empty state ───────────────────────────────────────────────────── */}
      {!callId && !loading && (
        <div className="flex flex-1 items-center justify-center font-mono text-xs text-[var(--fg-faint)]">
          Select a call above
        </div>
      )}

      {/* ── Loading ───────────────────────────────────────────────────────── */}
      {loading && (
        <div className="flex flex-1 items-center justify-center gap-2 font-mono text-xs text-[var(--fg-dim)]">
          <Loader2 className="size-4 animate-spin" />
          Loading order flow data…
        </div>
      )}

      {/* ── Canvas area ───────────────────────────────────────────────────── */}
      {meta && candles && footprint && liveBars && !loading && !error && (
        <>
          {/* Crosshair readout */}
          <div
            className="shrink-0 border-b border-[var(--border)]/40 px-3 py-1 font-mono text-[10px] text-[var(--fg-dim)]"
            style={{ background: "var(--bg-inset)", minHeight: 26 }}
          >
            {(hoverMs ?? cursorMs) != null ? (
              <span>{formatTimeET(hoverMs ?? cursorMs)} ET · {hoverPrice != null ? `@${hoverPrice.toFixed(2)}` : ""}</span>
            ) : (
              <span className="text-[var(--fg-faint)]">hover to inspect · drag scrubber for DOM+tape</span>
            )}
          </div>

          {/* Canvas */}
          <div className="flex-1 overflow-auto">
            <OrderFlowCanvas
              candles={liveBars.candles}
              footprint={liveBars.footprint}
              book={undefined}   /* heatmap is a separate widget */
              meta={meta}
              cursorMs={cursorMs}
              onHoverMove={handleHoverMove}
              onViewChange={handleViewChange}
            />
          </div>

          {/* Scrubber */}
          <MiniScrubber
            windowStart={meta.window_start}
            windowEnd={meta.window_end}
            postTs={meta.post_ts}
            entryTs={meta.entry_ts}
            exitTs={meta.exit_ts}
            cursorMs={cursorMs}
            playing={playing}
            playSpeed={playSpeed}
            onScrub={handleScrub}
            onTogglePlay={handleTogglePlay}
            onSpeedChange={setPlaySpeed}
          />
        </>
      )}
    </div>
  );
}
