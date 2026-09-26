"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createDrawing,
  createPriceAlert,
  deleteDrawing,
  deletePriceAlert,
  fetchDrawings,
  fetchPriceAlerts,
  fetchGammaWalls,
  fetchSessionLevels,
  updateDrawing,
  updatePriceAlert,
  type ChartDrawing,
  type ChartDrawingKind,
  type PriceAlert,
  type GammaWallsResponse,
  type SessionLevel,
  type StockBar,
} from "@/lib/api";
import { subscribePriceAlertRefresh } from "@/lib/price-alert-sync";
import {
  AlertCreatePopover,
  ChartContextMenu,
  defaultAlertDirection,
  type AlertDirection,
  type ChartMenuItem,
} from "@/components/trading/chart-alert-popover";
import { fetchBarsCached, invalidateBars } from "@/lib/bars-cache";
import {
  resolveCssColor,
  resolveDrawingColor,
  withAlpha,
} from "@/lib/chart-colors";
import { ChartEngine } from "@/lib/chart-engine/engine";
import {
  mergeFetchedBars,
  prependFetchedBars,
  type VolumeMemoryRef,
} from "@/lib/chart-engine/merge";
import type { BarEvent } from "@/lib/use-symbol-quote";
import type { BarFrame } from "@/lib/chart-engine/merge";
import { crosshairTimeLabel } from "@/lib/chart-engine/ticks";
import type {
  EngineBar,
  EngineColors,
  EngineOverlays,
  EngineStudies,
  OverlayLine,
  OverlaySegment,
  PaneStudySeries,
} from "@/lib/chart-engine/types";
import {
  cancelManualOrder,
  closeManualPosition,
  placeManualOrder,
  replaceManualOrder,
  snapPrice,
  verbText,
} from "@/lib/manual-trading-api";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { useManualQtyStore } from "@/lib/workspace/use-manual-qty-store";
import { OVERLAY_STUDIES } from "@/lib/chart-studies";
import { macdSeries, rsiSeries } from "@/lib/indicators";
import {
  CHART_TIMEFRAMES,
  formatVolume,
  timeframeSeconds,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";
import { usePageActive } from "@/lib/workspace/page-active-context";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * NativeChart — the chart-engine React wrapper (CHART_ENGINE.md). Owns data
 * (bars cache + refetch), theme (CSS vars → EngineColors), live-tick gating,
 * and the DOM legend; the engine owns every pixel. A/B swapped with
 * TvLightChart per widget via the eye menu's "Native engine (beta)".
 */

type Props = {
  symbol: string;
  timeframe?: ChartTimeframe;
  style?: "candle" | "line";
  livePrice?: number;
  extPrice?: number | null;
  marketSession?: string | null;
  /** Live bars from the server's minute-bar authority (card #325): the forming
      minute (rides each quote frame) and sealed/vendor/daily bar events. */
  formingBar?: BarFrame;
  barEvent?: BarEvent;
  compact?: boolean;
  /** ETH candles — defaults ON for intraday; ignored on daily+. */
  extended?: boolean;
  /** Session-node levels (frame's ☰ menu). */
  showLevels?: boolean;
  /** Index dealer-gamma walls + flip (Studies menu; SPX/NDX-tracking symbols only). */
  showGammaWalls?: boolean;
  /** Eye-menu visibility toggles. Undefined = shown. */
  showAlerts?: boolean;
  showDrawings?: boolean;
  /** Tag column (level names / bells / ext) — hiding it also hides every
      overlay line with it: the one-click clean chart. */
  showTagColumn?: boolean;
  /** Study ids from the frame toolbar's Studies dropdown (chart-studies.ts). */
  studies?: string[];
};

// Refetch cadence: the bars cache TTL is the real gate — this just polls it
// (a cache hit is free). Live candles come from `bar` frames, not from this.
const INTRADAY_REFRESH_MS = 30_000;
const DAILY_REFRESH_MS = 300_000;
// Progressive load (card #326): the newest window first — a couple of screens
// at the default zoom, one upstream round trip — then this many more per page
// as the view nears the oldest bar.
const BARS_PAGE = 300;
function toEngineBars(bars: StockBar[]): EngineBar[] {
  return bars.map((b) => ({
    time: Date.parse(b.time),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
  }));
}

// Overlay polls (match TvLightChart's alert cadence; drawings change rarely).
const ALERTS_POLL_MS = 20_000;
const DRAWINGS_POLL_MS = 60_000;
// Walls move with the option board (OI daily, quotes ~2 min server cache).
const GAMMA_WALLS_POLL_MS = 5 * 60_000;
// Symbols quoted in index points; anything else (SPY, /ES, QQQ, /NQ…) is rescaled.
const INDEX_POINT_SYMBOLS = new Set(["SPX", "$SPX", "^SPX", "SPXW", "NDX", "$NDX", "^NDX", "NDXP"]);

/**
 * Index points → this chart's price. The ratio is taken at the board's quote
 * time, not now: after 16:00 the option quotes freeze while /ES and SPY keep
 * trading, and a live ratio would slide every wall by the overnight move.
 */
function gammaWallScale(
  symbol: string,
  r: GammaWallsResponse,
  bars: EngineBar[],
  livePrice: number | undefined,
): number | null {
  if (INDEX_POINT_SYMBOLS.has(symbol.toUpperCase())) return 1;
  if (!r.spot) return null;
  const t = r.quote_as_of ? Date.parse(r.quote_as_of) : NaN;
  let px = livePrice;
  if (Number.isFinite(t)) {
    for (let i = bars.length - 1; i >= 0; i--) {
      if (bars[i].time <= t) {
        px = bars[i].close;
        break;
      }
    }
  }
  return px ? px / r.spot : null;
}

/** Alert-line colors per direction — cross-client convention (relay §2). */
function alertColor(direction: string): string {
  if (direction === "above") return withAlpha(resolveCssColor("--c-green", "#5bc98a"), 0.55);
  if (direction === "below") return withAlpha(resolveCssColor("--c-red", "#ef6f6c"), 0.55);
  return withAlpha(resolveCssColor("--fg-dim", "#9aa1ab"), 0.55);
}

/** Session-node level colors by kind — mirrors TvLightChart's palette. */
function sessionLevelColor(kind: SessionLevel["kind"]): string {
  switch (kind) {
    case "support":
      return resolveCssColor("--c-green", "#5bc98a");
    case "resistance":
      return resolveCssColor("--c-red", "#ef6f6c");
    case "poc":
      return resolveCssColor("--c-amber", "#e0975b");
    case "prior_high":
      return resolveCssColor("--c-blue", "#6cb0ff");
    case "prior_week_high":
    case "prior_week_low":
      return resolveCssColor("--c-purple", "#b48ce8");
    default:
      return resolveCssColor("--fg-faint", "#616873");
  }
}

/**
 * Study ids → engine series. Math + MA colors are shared with the TV path
 * (chart-studies/indicators); the oscillator colors mirror TvLightChart's
 * (RSI #c792ea, MACD #4d9de0 / signal #e0b040, sign-colored histogram).
 */
function composeStudies(
  bars: readonly EngineBar[],
  enabled: readonly string[],
): EngineStudies | null {
  if (bars.length === 0 || enabled.length === 0) return null;
  const closes = bars.map((b) => b.close);
  const overlays = OVERLAY_STUDIES.filter((s) => enabled.includes(s.id)).map(
    (s) => ({ label: s.label, color: s.color, values: s.compute(closes) }),
  );
  const panes: PaneStudySeries[] = [];
  if (enabled.includes("rsi")) {
    panes.push({
      kind: "rsi",
      label: "RSI 14",
      color: "#c792ea",
      values: rsiSeries(closes, 14),
    });
  }
  if (enabled.includes("macd")) {
    const m = macdSeries(closes);
    panes.push({
      kind: "macd",
      label: "MACD 12 26 9",
      macd: m.macd,
      signal: m.signal,
      histogram: m.histogram,
      macdColor: "#4d9de0",
      signalColor: "#e0b040",
      histUp: withAlpha(resolveCssColor("--chart-up", "#5ad1be"), 0.5),
      histDown: withAlpha(resolveCssColor("--chart-down", "#ef6f6c"), 0.5),
    });
  }
  if (overlays.length === 0 && panes.length === 0) return null;
  return { overlays, panes };
}

function resolveEngineColors(): EngineColors {
  const up = resolveCssColor("--chart-up", "#5ad1be");
  const down = resolveCssColor("--chart-down", "#ef6f6c");
  return {
    background: resolveCssColor(
      "--chart-bg",
      resolveCssColor("--surface-canvas", "#131519"),
    ),
    text: resolveCssColor("--text-primary", "#dfe3e8"),
    textDim: resolveCssColor("--text-secondary", "#9aa1ab"),
    grid: resolveCssColor("--chart-grid", "rgba(255,255,255,0.09)"),
    up,
    down,
    // line-mode series (equity / line charts) get their own knob, not candle-up
    accent: resolveCssColor("--chart-line", up),
    crosshair: resolveCssColor("--text-faint", "#616873"),
    crosshairLabelBg: resolveCssColor("--border-strong", "#333a44"),
    volumeUp: withAlpha(up, 0.6),
    volumeDown: withAlpha(down, 0.6),
  };
}

function resolveFontFamily(): string {
  if (typeof document === "undefined") return "system-ui, sans-serif";
  return getComputedStyle(document.body).fontFamily || "system-ui, sans-serif";
}

function legendText(bar: EngineBar, intraday: boolean): string {
  const chg = bar.open ? ((bar.close - bar.open) / bar.open) * 100 : 0;
  const sign = chg >= 0 ? "+" : "";
  return `${crosshairTimeLabel(bar.time, intraday)} · O ${bar.open.toFixed(2)} H ${bar.high.toFixed(2)} L ${bar.low.toFixed(2)} C ${bar.close.toFixed(2)} (${sign}${chg.toFixed(2)}%) · Vol ${formatVolume(bar.volume)}`;
}

export function NativeChart({
  symbol,
  timeframe: timeframeProp,
  style = "candle",
  livePrice,
  extPrice,
  marketSession,
  formingBar,
  barEvent,
  compact: _compact = false,
  extended = true,
  showLevels = false,
  showGammaWalls = false,
  showAlerts = true,
  showDrawings = true,
  showTagColumn = true,
  studies,
}: Props) {
  const timeframe = timeframeProp ?? CHART_TIMEFRAMES[5];
  const showExt = extended && timeframe.intraday;

  const containerRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<ChartEngine | null>(null);
  // The engine holds a reference to this same array: applyBarFrame
  // mutate it in place, so the wrapper's freshness/plausibility checks always
  // see the bars the engine is drawing.
  const barsRef = useRef<EngineBar[]>([]);
  const dataKeyRef = useRef<string>("");
  // Identity of the last bars payload actually applied — a cache hit hands back
  // the SAME object, and re-applying it would discard every tick merged since.
  const appliedPayloadRef = useRef<unknown>(null);
  const staleRefetchPendingRef = useRef(false);
  const staleRefetchLastSecRef = useRef(0);
  // Older-history backfill state for the CURRENT data key: one page in flight
  // at a time; `exhausted` once the vendor says there is nothing older.
  const historyRef = useRef({ key: "", loading: false, exhausted: false });
  const loadOlderRef = useRef<() => void>(() => {});
  // Coarse-bucket volume roll-up memory (merge.ts → VolumeMemory); reset
  // whenever a fetched payload replaces the bars.
  const volumeMemoryRef = useRef<VolumeMemoryRef>({ current: null });
  const styleRef = useRef(style);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [legend, setLegend] = useState("");
  // A chart on a workspace page that is not showing keeps its data (the page
  // stays mounted so switching is instant) but stops refetching it. Mirrored
  // into a ref for the polls that guard inside the interval callback rather
  // than re-running their effect — re-running would refetch on every hide.
  const pageActive = usePageActive();
  const pageActiveRef = useRef(pageActive);
  pageActiveRef.current = pageActive;

  const [themeNonce, setThemeNonce] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);
  // Bumped when a bars payload lands — studies recompute against the new bars.
  const [barsNonce, setBarsNonce] = useState(0);
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [levels, setLevels] = useState<SessionLevel[]>([]);
  const [gammaWalls, setGammaWalls] = useState<GammaWallsResponse | null>(null);
  const [drawings, setDrawings] = useState<ChartDrawing[]>([]);
  // CRUD bumps these to refetch immediately instead of waiting out the poll.
  const [alertsNonce, setAlertsNonce] = useState(0);
  const [drawingsNonce, setDrawingsNonce] = useState(0);

  // Right-click menu + alert-create popover (chart-ui rule: in-chart actions
  // live in a context menu, never in buttons floating over the canvas).
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    price: number;
    alertId: number | null;
    levelId: number | null;
    shapeId: number | null;
    orderId: string | null;
    positionHit: boolean;
  } | null>(null);
  const [pendingAlert, setPendingAlert] = useState<{ x: number; y: number } | null>(
    null,
  );
  const [alertPrice, setAlertPrice] = useState("");
  const [alertDirection, setAlertDirection] = useState<AlertDirection>("cross");
  const [alertRepeat, setAlertRepeat] = useState(false);
  const [alertSaving, setAlertSaving] = useState(false);
  const [alertError, setAlertError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const livePriceRef = useRef(livePrice);
  // The engine outlives symbol switches — its callbacks read these refs.
  const symbolRef = useRef(symbol);
  const drawingsRef = useRef<ChartDrawing[]>([]);

  // Chart trading: working orders + position for this symbol (default paper
  // account scope, same as the DOM), drawn as overlay lines. All actions ride
  // the admin-gated manual verbs; feedback renders as a pointer-events-none
  // line above the legend (never buttons floating on the canvas).
  const { snapshot, refresh: refreshSnapshot } = useSnapshot();
  const tradeScope = snapshot?.snapshots?.find((sn) => sn.is_default)
    ?? snapshot?.snapshots?.find((sn) => sn.money_type === "paper");
  // Memoized: these feed the overlays effect — fresh array identities every
  // render would re-push overlays to the engine on every state change.
  const chartOrders = useMemo(
    () => (tradeScope?.open_orders ?? []).filter((o) => o.symbol === symbol),
    [tradeScope, symbol],
  );
  const chartPosition = useMemo(
    () =>
      (tradeScope?.positions ?? []).find(
        (p) => p.symbol === symbol && p.side === "long",
      ),
    [tradeScope, symbol],
  );
  const chartOrdersRef = useRef(chartOrders);
  chartOrdersRef.current = chartOrders;
  const chartPositionRef = useRef(chartPosition);
  chartPositionRef.current = chartPosition;
  const refreshSnapshotRef = useRef(refreshSnapshot);
  refreshSnapshotRef.current = refreshSnapshot;
  const [tradeMsg, setTradeMsg] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);
  const tradeMsgTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showTradeMsg = useCallback((tone: "ok" | "err", text: string) => {
    setTradeMsg({ tone, text });
    if (tradeMsgTimer.current) clearTimeout(tradeMsgTimer.current);
    tradeMsgTimer.current = setTimeout(() => setTradeMsg(null), 6_000);
  }, []);

  useEffect(() => {
    styleRef.current = style;
  }, [style]);

  useEffect(() => {
    livePriceRef.current = livePrice;
  }, [livePrice]);

  useEffect(() => {
    symbolRef.current = symbol;
  }, [symbol]);

  useEffect(() => {
    drawingsRef.current = drawings;
  }, [drawings]);

  // A symbol/timeframe switch invalidates any open popup's price context —
  // and a half-placed drawing would anchor into the wrong series.
  useEffect(() => {
    setMenu(null);
    setPendingAlert(null);
    engineRef.current?.setDrawMode(null);
  }, [symbol, timeframe.value]);

  // Engine lifecycle — recreated only when the tick-label mode flips
  // (intraday vs daily); everything else flows through setters.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const engine = new ChartEngine(el, {
      colors: resolveEngineColors(),
      style: styleRef.current,
      intraday: timeframe.intraday,
      fontFamily: resolveFontFamily(),
      onCrosshair: (bar) =>
        setLegend(bar ? legendText(bar, timeframe.intraday) : ""),
      onContextMenu: (hit) => {
        if (hit.price == null) return;
        const alertId = hit.lineId?.startsWith("alert:")
          ? Number(hit.lineId.slice(6))
          : null;
        const levelId = hit.lineId?.startsWith("dlevel:")
          ? Number(hit.lineId.slice(7))
          : null;
        const shapeId = hit.segmentId?.startsWith("shape:")
          ? Number(hit.segmentId.slice(6))
          : null;
        const orderId = hit.lineId?.startsWith("order:")
          ? hit.lineId.slice(6)
          : null;
        const positionHit = hit.lineId?.startsWith("pos:") ?? false;
        setPendingAlert(null);
        setMenu({
          x: hit.x,
          y: hit.y,
          // A hit line's exact price beats the cursor's approximate one (the
          // 6px tolerance would otherwise seed alerts a few cents off).
          price: Math.round((hit.linePrice ?? hit.price) * 100) / 100,
          alertId,
          levelId,
          shapeId,
          orderId,
          positionHit,
        });
      },
      onLineDragEnd: (id, price) => {
        if (id.startsWith("order:")) {
          // Drag-to-move a working limit: cancel + revalidated re-place via
          // the manual verb. Refresh either way — success shows the new line,
          // failure snaps it back to the broker's truth.
          void replaceManualOrder(id.slice(6), snapPrice(price))
            .then((result) => {
              showTradeMsg(
                result.status === "executed" ? "ok" : "err",
                result.status === "executed"
                  ? `moved to ${snapPrice(price)}`
                  : verbText(result),
              );
            })
            .catch((err) => showTradeMsg("err", String(err)))
            .finally(() => refreshSnapshotRef.current());
          return;
        }
        if (!id.startsWith("alert:")) return;
        // Refetch either way: success confirms, failure snaps the line back.
        updatePriceAlert(Number(id.slice(6)), {
          price: Math.round(price * 100) / 100,
        })
          .catch(() => {})
          .finally(() => setAlertsNonce((n) => n + 1));
      },
      onDrawComplete: (kind, a, b) => {
        // Semantic color per the cross-client contract (relay §5.1); points in
        // epoch seconds, matching the /api/drawings schema.
        void createDrawing({
          symbol: symbolRef.current,
          kind: kind as ChartDrawingKind,
          data: {
            points: [
              { time: Math.round(a.time / 1000), price: a.price },
              { time: Math.round(b.time / 1000), price: b.price },
            ],
            color: "accent",
            ...(kind === "channel"
              ? { width: Math.max(0.01, Math.round(a.price * 0.02 * 100) / 100) }
              : {}),
          },
        })
          .catch(() => {})
          .finally(() => setDrawingsNonce((n) => n + 1));
      },
      onNeedHistory: () => loadOlderRef.current(),
      onSegmentDragEnd: (id, seg) => {
        if (!id.startsWith("shape:")) return;
        const drawingId = Number(id.slice(6));
        const existing = drawingsRef.current.find((d) => d.id === drawingId);
        void updateDrawing(drawingId, {
          data: {
            ...(existing?.data ?? {}),
            points: [
              { time: Math.round(seg.a.time / 1000), price: seg.a.price },
              { time: Math.round(seg.b.time / 1000), price: seg.b.price },
            ],
            ...(seg.parallelOffset != null ? { width: seg.parallelOffset } : {}),
          },
        })
          .catch(() => {})
          .finally(() => setDrawingsNonce((n) => n + 1));
      },
    });
    engineRef.current = engine;
    if (barsRef.current.length > 0) {
      engine.setBars(barsRef.current, { anchorRight: true });
    }
    return () => {
      engineRef.current = null;
      engine.destroy();
    };
  }, [timeframe.intraday]);

  useEffect(() => {
    engineRef.current?.setStyle(style);
  }, [style]);

  // Tag column toggle (timeframe.intraday dep: a recreated engine needs it
  // re-applied).
  useEffect(() => {
    engineRef.current?.setTagGutter(showTagColumn);
  }, [showTagColumn, timeframe.intraday]);

  // Studies: recompute on new bars payloads / toggles / theme (histogram
  // colors follow the theme). Live ticks between polls drift the forming
  // bar's study values by design — the ~30s refetch reconciles, like TV.
  const studiesKey = (studies ?? []).join(",");
  useEffect(() => {
    engineRef.current?.setStudies(
      composeStudies(barsRef.current, studies ?? []),
    );
  }, [barsNonce, studiesKey, themeNonce, timeframe.intraday]);

  // Ext-hours price line — same session gate as TvLightChart: whenever an ext
  // quote exists outside RTH (incl. weekend "closed", where the last ext print
  // would otherwise sit a session behind the candles). Independent of the ETH
  // candles toggle and of timeframe — this is how a daily chart still shows
  // where the name trades after the close. (timeframe.intraday dep: the engine
  // is recreated on that flip, and the new instance needs the line re-applied.)
  useEffect(() => {
    const show =
      extPrice != null &&
      marketSession != null &&
      (marketSession === "pre" ||
        marketSession === "post" ||
        marketSession === "overnight" ||
        marketSession === "closed");
    engineRef.current?.setExtPrice(show ? extPrice : null);
  }, [extPrice, marketSession, timeframe.intraday]);

  // Overlay data — same sources the TV chart reads. Fail-soft to empty.
  useEffect(() => {
    if (!showAlerts) {
      setAlerts([]);
      return;
    }
    let cancelled = false;
    const load = () =>
      fetchPriceAlerts(symbol)
        .then((a) => !cancelled && setAlerts(a))
        .catch(() => !cancelled && setAlerts([]));
    void load();
    const id = setInterval(() => {
      if (!pageActiveRef.current) return;
      void load();
    }, ALERTS_POLL_MS);
    const unsubscribe = subscribePriceAlertRefresh(() => void load());
    return () => {
      cancelled = true;
      clearInterval(id);
      unsubscribe();
    };
  }, [symbol, showAlerts, alertsNonce]);

  useEffect(() => {
    if (!showLevels) {
      setLevels([]);
      return;
    }
    let cancelled = false;
    fetchSessionLevels(symbol)
      .then((r) => !cancelled && setLevels(r.levels))
      .catch(() => !cancelled && setLevels([]));
    return () => {
      cancelled = true;
    };
  }, [symbol, showLevels]);

  useEffect(() => {
    if (!showGammaWalls) {
      setGammaWalls(null);
      return;
    }
    let cancelled = false;
    const load = () =>
      fetchGammaWalls(symbol)
        .then((r) => !cancelled && setGammaWalls(r.available ? r : null))
        .catch(() => !cancelled && setGammaWalls(null));
    void load();
    const id = setInterval(() => {
      if (!pageActiveRef.current) return;
      void load();
    }, GAMMA_WALLS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [symbol, showGammaWalls]);

  useEffect(() => {
    if (!showDrawings) {
      setDrawings([]);
      return;
    }
    let cancelled = false;
    const load = () =>
      fetchDrawings(symbol)
        .then((d) => !cancelled && setDrawings(d))
        .catch(() => !cancelled && setDrawings([]));
    void load();
    const id = setInterval(() => {
      if (!pageActiveRef.current) return;
      void load();
    }, DRAWINGS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [symbol, showDrawings, drawingsNonce]);

  // Compose alerts/levels/drawings into engine primitives with resolved
  // colors. Re-runs on theme change and after an engine recreation
  // (timeframe.intraday dep — the new instance needs the set re-applied).
  useEffect(() => {
    const lines: OverlayLine[] = [];
    const segments: OverlaySegment[] = [];
    for (const a of alerts) {
      if (a.triggered && !a.repeat) continue;
      lines.push({
        price: a.price,
        color: alertColor(a.direction),
        dash: [6, 4],
        label: "🔔",
        id: `alert:${a.id}`,
        draggable: true,
      });
    }
    for (const l of levels) {
      lines.push({
        price: l.price,
        color: withAlpha(sessionLevelColor(l.kind), 0.7),
        dash: [2, 3],
        label: l.name,
        // Auto-levels merge into one "node" line when zoom crowds them.
        collapsible: true,
      });
    }
    const wallScale = gammaWalls
      ? gammaWallScale(symbol, gammaWalls, barsRef.current, livePriceRef.current)
      : null;
    if (gammaWalls && wallScale) {
      // Sign is the dealer convention (long calls, short puts) — the label
      // carries the index strike so a rescaled SPY, /ES or /NQ line still names it.
      for (const w of gammaWalls.walls) {
        const call = w.net_b >= 0;
        lines.push({
          price: w.strike * wallScale,
          color: withAlpha(
            resolveCssColor(call ? "--c-green" : "--c-red", call ? "#5bc98a" : "#ef6f6c"),
            0.45,
          ),
          dash: [1, 2],
          label: `γ${call ? "+" : "−"} ${w.strike}`,
          collapsible: true,
        });
      }
      if (gammaWalls.flip != null) {
        lines.push({
          price: gammaWalls.flip * wallScale,
          color: withAlpha(resolveCssColor("--c-amber", "#e0975b"), 0.7),
          dash: [1, 2],
          label: "γ flip",
          collapsible: true,
        });
      }
    }
    for (const d of drawings) {
      // Semantic color names resolve to theme vars, legacy raw hex renders
      // as-is (cross-client contract, relay §5.1).
      const color = resolveDrawingColor(d.data.color);
      if (d.kind === "level" && d.data.price != null) {
        lines.push({
          price: d.data.price,
          color,
          dash: [1, 3],
          id: `dlevel:${d.id}`,
        });
      } else if (d.data.points && d.data.points.length >= 2) {
        const [a, b] = d.data.points;
        if (a.time == null || b.time == null) continue;
        segments.push({
          a: { time: a.time * 1000, price: a.price },
          b: { time: b.time * 1000, price: b.price },
          color,
          parallelOffset: d.kind === "channel" ? d.data.width : undefined,
          id: `shape:${d.id}`,
          draggable: true,
        });
      }
    }
    // Working orders / protective stops / position (chart trading). Alert-line
    // alpha convention (0.55); labels name the order so the founder never has
    // to guess which line is which.
    for (const o of chartOrders) {
      const linePrice = o.limit_price ?? o.stop_price;
      if (linePrice == null) continue;
      const remaining = o.qty - (o.filled_qty || 0);
      if (o.order_type === "stop" || o.order_type?.startsWith("stop") || (o.order_type ?? "").includes("trail")) {
        lines.push({
          price: linePrice,
          color: withAlpha(resolveCssColor("--c-amber", "#e0b154"), 0.55),
          dash: [4, 3],
          label: `stop ${remaining}`,
          // No id on purpose: not draggable, not cancelable from the chart —
          // the stop guard owns it.
        });
        continue;
      }
      const buy = o.side === "buy";
      lines.push({
        price: linePrice,
        color: withAlpha(
          resolveCssColor(buy ? "--c-green" : "--c-red", buy ? "#5bc98a" : "#ef6f6c"),
          0.55,
        ),
        dash: [6, 2],
        label: `${o.side} ${remaining} lmt`,
        id: `order:${o.id}`,
        draggable: o.order_type === "limit",
      });
    }
    if (chartPosition && chartPosition.avg_entry_price > 0) {
      lines.push({
        price: chartPosition.avg_entry_price,
        color: withAlpha(resolveCssColor("--fg-dim", "#9aa1ab"), 0.5),
        dash: [8, 4],
        label: `pos ${chartPosition.qty}`,
        id: `pos:${chartPosition.symbol}`,
      });
    }
    const overlays: EngineOverlays = { lines, segments };
    // Hiding the tag column hides every overlay line with it (founder call:
    // one quick switch back to a clean chart).
    engineRef.current?.setOverlays(
      showTagColumn && (lines.length || segments.length) ? overlays : null,
    );
  }, [alerts, levels, gammaWalls, barsNonce, drawings, chartOrders, chartPosition, themeNonce, timeframe.intraday, showTagColumn, symbol]);

  // Theme changes (mode toggle or custom-theme var edits) re-resolve colors.
  useEffect(() => {
    const obs = new MutationObserver(() => setThemeNonce((n) => n + 1));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    const onVars = () => setThemeNonce((n) => n + 1);
    window.addEventListener(THEME_VARS_EVENT, onVars);
    return () => {
      obs.disconnect();
      window.removeEventListener(THEME_VARS_EVENT, onVars);
    };
  }, []);

  useEffect(() => {
    if (themeNonce > 0) engineRef.current?.setColors(resolveEngineColors());
  }, [themeNonce]);

  // Data: cached fetch; a key change (symbol/timeframe/ETH) shows loading and
  // re-anchors the view; a periodic nonce refresh swaps data invisibly.
  useEffect(() => {
    let cancelled = false;
    const key = `${symbol}|${timeframe.value}|${showExt ? "ext" : "rth"}`;
    const keyChanged = dataKeyRef.current !== key;
    if (keyChanged) {
      setLoading(true);
      setError(null);
    }

    fetchBarsCached(symbol, {
      timeframe: timeframe.value,
      extended: showExt,
      limit: BARS_PAGE,
    })
      .then((data) => {
        if (cancelled) return;
        // A refresh reconciles with vendor truth; it must never REGRESS the
        // tail. The refresh interval is shorter than the cache TTL (30s vs 60s
        // on 1Min, 180s on 5Min), so most refreshes are cache hits handing back
        // a payload we already applied — re-mapping it wiped the live close /
        // high / low and, worse, deleted a bar we had rolled over locally. The
        // next tick then re-rolled it open at the CURRENT price, leaving a
        // phantom gap the size of everything that moved since the snapshot.
        if (!keyChanged && data === appliedPayloadRef.current) {
          setError(null);
          return; // nothing new to reconcile — leave the live bars alone
        }
        appliedPayloadRef.current = data;
        const fetched = toEngineBars(data.bars);
        // The newest window replaces the span it covers; history we backfilled
        // and hub-built bars past it survive. A key change is a different
        // series entirely — nothing to carry over.
        const bars = keyChanged
          ? fetched
          : mergeFetchedBars(fetched, barsRef.current);
        barsRef.current = bars;
        dataKeyRef.current = key;
        if (keyChanged) {
          historyRef.current = { key, loading: false, exhausted: !data.has_more };
        }
        volumeMemoryRef.current.current = null;
        engineRef.current?.setBars(bars, { anchorRight: keyChanged });
        setBarsNonce((n) => n + 1);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load bars");
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        staleRefetchPendingRef.current = false;
      });

    return () => {
      cancelled = true;
      // A cancelled in-flight staleness refetch must not leave the gate stuck
      // shut for the next key (its .finally() bails on `cancelled`).
      staleRefetchPendingRef.current = false;
    };
  }, [symbol, timeframe.value, showExt, refreshNonce]);

  // Older history on demand: the engine calls this (via the ref) whenever the
  // view nears the oldest bar. One page in flight per data key; stops once the
  // vendor reports nothing older. The page is prepended and the view shifted by
  // the same count, so the bars on screen never move.
  useEffect(() => {
    loadOlderRef.current = () => {
      const key = dataKeyRef.current;
      const h = historyRef.current;
      if (!key || h.key !== key || h.loading || h.exhausted) return;
      const oldest = barsRef.current[0];
      if (!oldest) return;
      h.loading = true;
      fetchBarsCached(symbol, {
        timeframe: timeframe.value,
        extended: showExt,
        limit: BARS_PAGE,
        before: new Date(oldest.time).toISOString(),
      })
        .then((data) => {
          if (dataKeyRef.current !== key) return; // series changed underneath us
          const { bars, added } = prependFetchedBars(
            toEngineBars(data.bars),
            barsRef.current,
          );
          if (added > 0) {
            barsRef.current = bars;
            engineRef.current?.setBars(bars, { prepended: added });
            setBarsNonce((n) => n + 1);
          }
          historyRef.current.exhausted = !data.has_more || added === 0;
        })
        .catch(() => {
          /* the next pan retries */
        })
        .finally(() => {
          if (historyRef.current.key === key) historyRef.current.loading = false;
        });
    };
  });

  // Periodic refetch — completed-bar reconciliation (volume, vendor
  // corrections) and the hole-filler backstop. Skips hidden tabs and pages.
  useEffect(() => {
    const ms = timeframe.intraday ? INTRADAY_REFRESH_MS : DAILY_REFRESH_MS;
    const id = setInterval(() => {
      if (!pageActiveRef.current) return;
      if (document.visibilityState === "hidden") return;
      setRefreshNonce((n) => n + 1);
    }, ms);
    return () => clearInterval(id);
  }, [timeframe.intraday]);

  // Popup dismissal: ESC closes both; a pointerdown outside closes the menu
  // (the popover has explicit Cancel — parity with the TV chart's flow).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenu(null);
      setPendingAlert(null);
      setAlertError(null);
      engineRef.current?.setDrawMode(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setMenu(null);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [menu]);

  function handleSetAlertFromMenu() {
    if (!menu) return;
    const current = livePriceRef.current ?? barsRef.current.at(-1)?.close;
    setAlertPrice(String(menu.price));
    setAlertDirection(defaultAlertDirection(menu.price, current));
    setAlertRepeat(false);
    setAlertError(null);
    setPendingAlert({ x: menu.x, y: menu.y });
    setMenu(null);
  }

  async function handleCreateAlert() {
    const price = Number(alertPrice.trim());
    if (!Number.isFinite(price)) {
      setAlertError("Enter a valid price");
      return;
    }
    setAlertError(null);
    setAlertSaving(true);
    try {
      await createPriceAlert({
        symbol,
        price,
        direction: alertDirection,
        repeat: alertRepeat,
      });
      setAlertsNonce((n) => n + 1);
      setPendingAlert(null);
    } catch {
      setAlertError("Could not create alert");
    } finally {
      setAlertSaving(false);
    }
  }

  async function handleRemoveAlert() {
    if (menu?.alertId == null) return;
    try {
      await deletePriceAlert(menu.alertId);
      setAlertsNonce((n) => n + 1);
      setMenu(null);
    } catch {
      /* menu stays open for retry */
    }
  }

  async function handleAddLevel() {
    if (!menu) return;
    try {
      await createDrawing({
        symbol,
        kind: "level",
        data: { price: menu.price },
      });
      setDrawingsNonce((n) => n + 1);
      setMenu(null);
    } catch {
      /* menu stays open for retry */
    }
  }

  async function handleRemoveLevel() {
    if (menu?.levelId == null) return;
    try {
      await deleteDrawing(menu.levelId);
      setDrawingsNonce((n) => n + 1);
      setMenu(null);
    } catch {
      /* menu stays open for retry */
    }
  }

  async function handleRemoveShape() {
    if (menu?.shapeId == null) return;
    try {
      await deleteDrawing(menu.shapeId);
      setDrawingsNonce((n) => n + 1);
      setMenu(null);
    } catch {
      /* menu stays open for retry */
    }
  }

  function handleDrawTool(kind: "trendline" | "channel") {
    engineRef.current?.setDrawMode(kind);
    setMenu(null);
  }

  async function handleCancelOrderFromMenu() {
    const orderId = menu?.orderId;
    setMenu(null);
    if (!orderId) return;
    try {
      const result = await cancelManualOrder(orderId);
      showTradeMsg(
        result.status === "cancel_sent" ? "ok" : "err",
        verbText(result),
      );
    } catch (err) {
      showTradeMsg("err", String(err));
    } finally {
      refreshSnapshotRef.current();
    }
  }

  async function handleFlattenFromMenu() {
    const pos = chartPositionRef.current;
    setMenu(null);
    if (!pos) return;
    try {
      const result = await closeManualPosition(pos.symbol);
      showTradeMsg(
        result.status === "executed" ? "ok" : "err",
        result.status === "executed" ? `flattening ${pos.symbol}` : verbText(result),
      );
    } catch (err) {
      showTradeMsg("err", String(err));
    } finally {
      refreshSnapshotRef.current();
    }
  }

  async function handlePlaceFromMenu(side: "buy" | "sell", price: number) {
    setMenu(null);
    const qty = useManualQtyStore.getState().qty;
    try {
      const result = await placeManualOrder({
        symbol: symbolRef.current,
        side,
        qty,
        order_type: "limit",
        limit_price: snapPrice(price),
      });
      showTradeMsg(
        result.status === "executed" ? "ok" : "err",
        result.status === "executed"
          ? `${side} ${qty} @ ${snapPrice(price)} sent`
          : verbText(result),
      );
    } catch (err) {
      showTradeMsg("err", String(err));
    } finally {
      refreshSnapshotRef.current();
    }
  }

  const hitShapeKind =
    menu?.shapeId != null
      ? (drawings.find((d) => d.id === menu.shapeId)?.kind ?? "trendline")
      : null;

  const menuQty = useManualQtyStore((st) => st.qty);
  const menuItems: ChartMenuItem[] = menu
    ? menu.orderId != null
      ? [
          {
            label: "Cancel order",
            onSelect: () => void handleCancelOrderFromMenu(),
          },
        ]
      : menu.positionHit
        ? [
            {
              label: `Flatten position`,
              onSelect: () => void handleFlattenFromMenu(),
            },
          ]
      : menu.alertId != null
      ? [{ label: "Remove alert", onSelect: () => void handleRemoveAlert() }]
      : menu.shapeId != null
        ? [
            {
              label: `Remove ${hitShapeKind}`,
              onSelect: () => void handleRemoveShape(),
            },
          ]
        : [
            ...(menu.levelId != null
              ? [{ label: "Remove level", onSelect: () => void handleRemoveLevel() }]
              : [
                  {
                    label: `Buy ${menuQty} @ ${menu.price.toFixed(2)} lmt`,
                    onSelect: () => void handlePlaceFromMenu("buy", menu.price),
                  },
                  {
                    label: `Sell ${menuQty} @ ${menu.price.toFixed(2)} lmt`,
                    onSelect: () => void handlePlaceFromMenu("sell", menu.price),
                  },
                  {
                    label: `Add level at ${menu.price.toFixed(2)}`,
                    onSelect: () => void handleAddLevel(),
                  },
                  {
                    label: "Draw trendline",
                    onSelect: () => handleDrawTool("trendline"),
                  },
                  {
                    label: "Draw channel",
                    onSelect: () => handleDrawTool("channel"),
                  },
                ]),
            {
              label: `Set alert at ${menu.price.toFixed(2)}`,
              onSelect: handleSetAlertFromMenu,
            },
          ]
    : [];

  // Live bars (card #325). The server is the bar authority; the client only
  // replaces/appends by bar time (merge.ts → applyBarFrame). No clock, no
  // bare price, no staleness gate, no plausibility band. Two inputs: the
  // forming minute riding each quote frame, and ordered `bar` events (sealed
  // minutes, Alpaca bars/updatedBars, daily bars).
  const applyFrame = useCallback(
    (frame: BarFrame) => {
      if (loading) return;
      // Never merge across a symbol/timeframe/ETH switch window: until the
      // new key's fetch lands, barsRef still holds the OLD series.
      const key = `${symbol}|${timeframe.value}|${showExt ? "ext" : "rth"}`;
      if (dataKeyRef.current !== key) return;
      if (frame.symbol !== symbol) return;
      const engine = engineRef.current;
      if (!engine || barsRef.current.length === 0) return;
      const result = engine.applyBarFrame(frame, {
        intraday: timeframe.intraday,
        intervalMs: timeframeSeconds(timeframe.value) * 1000,
        showExt,
        memory: volumeMemoryRef.current,
      });
      // A frame that landed past a hole means bars are missing between the
      // vendor tail and now (a symbol the hub only just started building, or
      // a stream hiccup) — ask the vendor for them, once per episode.
      if (result.kind === "appended" && result.skipped > 0) {
        const nowSec = Math.floor(Date.now() / 1000);
        const intervalSec = timeframeSeconds(timeframe.value);
        const cooldownSec = Math.max(2 * intervalSec, 300);
        if (
          !staleRefetchPendingRef.current &&
          nowSec - staleRefetchLastSecRef.current >= cooldownSec
        ) {
          staleRefetchPendingRef.current = true;
          staleRefetchLastSecRef.current = nowSec;
          invalidateBars(symbol, {
            timeframe: timeframe.value,
            extended: showExt,
            limit: BARS_PAGE,
          });
          setRefreshNonce((n) => n + 1);
        }
      }
    },
    [loading, symbol, timeframe.value, timeframe.intraday, showExt],
  );

  useEffect(() => {
    if (formingBar) applyFrame(formingBar);
  }, [formingBar, applyFrame]);

  useEffect(() => {
    if (barEvent) applyFrame(barEvent.bar);
  }, [barEvent, applyFrame]);

  return (
    <div className="relative h-full w-full min-h-0 overflow-hidden">
      {loading && <Skeleton className="absolute inset-0 z-10 rounded-none" />}
      <div ref={containerRef} className="absolute inset-0" />
      {legend ? (
        <div className="pointer-events-none absolute left-2 top-1.5 z-20">
          <p className="text-[11px] tabular-nums text-muted-foreground">
            {legend}
          </p>
        </div>
      ) : null}
      {tradeMsg ? (
        <div className="pointer-events-none absolute bottom-2 left-2 z-20">
          <p
            className="font-mono text-[length:var(--fs-table)]"
            style={{
              color:
                tradeMsg.tone === "ok" ? "var(--c-green)" : "var(--c-red)",
            }}
          >
            {tradeMsg.text}
          </p>
        </div>
      ) : null}
      {menu ? (
        <ChartContextMenu
          menuRef={menuRef}
          x={menu.x}
          y={menu.y}
          containerRef={containerRef}
          items={menuItems}
        />
      ) : null}
      {pendingAlert ? (
        <AlertCreatePopover
          x={pendingAlert.x}
          y={pendingAlert.y}
          containerRef={containerRef}
          price={alertPrice}
          direction={alertDirection}
          repeat={alertRepeat}
          saving={alertSaving}
          error={alertError}
          onPriceChange={setAlertPrice}
          onDirectionChange={setAlertDirection}
          onRepeatChange={setAlertRepeat}
          onCreate={() => void handleCreateAlert()}
          onCancel={() => {
            setPendingAlert(null);
            setAlertError(null);
          }}
        />
      ) : null}
      {error && !loading ? (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-background/80">
          <p className="text-xs text-muted-foreground">{error}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              invalidateBars(symbol, {
                timeframe: timeframe.value,
                extended: showExt,
                limit: BARS_PAGE,
              });
              setRefreshNonce((n) => n + 1);
            }}
          >
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}
