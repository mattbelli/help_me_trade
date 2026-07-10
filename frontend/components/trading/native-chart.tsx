"use client";

import { useEffect, useRef, useState } from "react";

import {
  createDrawing,
  createPriceAlert,
  deleteDrawing,
  deletePriceAlert,
  fetchDrawings,
  fetchPriceAlerts,
  fetchSessionLevels,
  updateDrawing,
  updatePriceAlert,
  type ChartDrawing,
  type ChartDrawingKind,
  type PriceAlert,
  type SessionLevel,
  type StockBar,
} from "@/lib/api";
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
import { crosshairTimeLabel } from "@/lib/chart-engine/ticks";
import type {
  EngineBar,
  EngineColors,
  EngineOverlays,
  OverlayLine,
  OverlaySegment,
} from "@/lib/chart-engine/types";
import {
  CHART_TIMEFRAMES,
  formatVolume,
  timeframeSeconds,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";
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
  compact?: boolean;
  /** ETH candles — defaults ON for intraday; ignored on daily+. */
  extended?: boolean;
  /** Session-node levels (frame's ☰ menu). */
  showLevels?: boolean;
  /** Eye-menu visibility toggles. Undefined = shown. */
  showAlerts?: boolean;
  showDrawings?: boolean;
};

// Rollover/refetch cadence: the bars cache TTL is the real gate — this just
// polls it (a cache hit is free), so forming-bar rollover lags TTL, not this.
const INTRADAY_REFRESH_MS = 30_000;
const DAILY_REFRESH_MS = 300_000;
// Overlay polls (match TvLightChart's alert cadence; drawings change rarely).
const ALERTS_POLL_MS = 20_000;
const DRAWINGS_POLL_MS = 60_000;

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
    default:
      return resolveCssColor("--fg-faint", "#616873");
  }
}

function resolveEngineColors(): EngineColors {
  const up = resolveCssColor("--chart-accent", "#5ad1be");
  const down = resolveCssColor("--chart-down", "#ef6f6c");
  return {
    background: resolveCssColor(
      "--chart-background",
      resolveCssColor("--background", "#131519"),
    ),
    text: resolveCssColor("--foreground", "#dfe3e8"),
    textDim: resolveCssColor("--fg-dim", "#9aa1ab"),
    grid: resolveCssColor("--border", "rgba(255,255,255,0.09)"),
    up,
    down,
    accent: up,
    crosshair: resolveCssColor("--fg-faint", "#616873"),
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
  compact: _compact = false,
  extended = true,
  showLevels = false,
  showAlerts = true,
  showDrawings = true,
}: Props) {
  const timeframe = timeframeProp ?? CHART_TIMEFRAMES[5];
  const showExt = extended && timeframe.intraday;

  const containerRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<ChartEngine | null>(null);
  // The engine holds a reference to this same array: applyLive/rollover
  // mutate it in place, so the wrapper's freshness/plausibility checks always
  // see the bars the engine is drawing.
  const barsRef = useRef<EngineBar[]>([]);
  const dataKeyRef = useRef<string>("");
  const staleRefetchPendingRef = useRef(false);
  const staleRefetchLastSecRef = useRef(0);
  const styleRef = useRef(style);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [legend, setLegend] = useState("");
  const [themeNonce, setThemeNonce] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [levels, setLevels] = useState<SessionLevel[]>([]);
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
        });
      },
      onLineDragEnd: (id, price) => {
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
    const id = setInterval(load, ALERTS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
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
    const id = setInterval(load, DRAWINGS_POLL_MS);
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
    const overlays: EngineOverlays = { lines, segments };
    engineRef.current?.setOverlays(
      lines.length || segments.length ? overlays : null,
    );
  }, [alerts, levels, drawings, themeNonce, timeframe.intraday]);

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
      days: timeframe.days,
      extended: showExt,
    })
      .then((data) => {
        if (cancelled) return;
        const bars: EngineBar[] = data.bars.map((b: StockBar) => ({
          time: Date.parse(b.time),
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
          volume: b.volume,
        }));
        barsRef.current = bars;
        dataKeyRef.current = key;
        engineRef.current?.setBars(bars, { anchorRight: keyChanged });
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
  }, [symbol, timeframe.value, timeframe.days, showExt, refreshNonce]);

  // Periodic refetch — Phase 1's completed-bar reconciliation (volume, vendor
  // corrections) and the rollover backstop. Skips hidden tabs.
  useEffect(() => {
    const ms = timeframe.intraday ? INTRADAY_REFRESH_MS : DAILY_REFRESH_MS;
    const id = setInterval(() => {
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

  const hitShapeKind =
    menu?.shapeId != null
      ? (drawings.find((d) => d.id === menu.shapeId)?.kind ?? "trendline")
      : null;

  const menuItems: ChartMenuItem[] = menu
    ? menu.alertId != null
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

  // Live-tick merge — mirrors TvLightChart's gating exactly:
  //   regular/unknown → livePrice; pre/post/overnight → extPrice when ETH on;
  //   closed → nothing. Then the staleness gate, the ±50% plausibility band
  //   vs the prior COMPLETED bar, single-step rollover, and finally the merge.
  useEffect(() => {
    if (loading) return;
    // Never merge across a symbol/timeframe/ETH switch window: until the new
    // key's fetch lands, barsRef still holds the OLD granularity's bars and a
    // rollover/merge against them paints a garbage candle (the TV 1D→4H bug
    // class — here the guard is the data key, since EngineBar times are
    // always numeric).
    const key = `${symbol}|${timeframe.value}|${showExt ? "ext" : "rth"}`;
    if (dataKeyRef.current !== key) return;
    const bars = barsRef.current;
    const engine = engineRef.current;
    if (!engine || bars.length === 0) return;

    const sess = marketSession ?? "regular";
    let tick: number | undefined;
    if (sess === "pre" || sess === "post" || sess === "overnight") {
      tick = showExt ? (extPrice ?? undefined) : undefined;
    } else if (sess === "closed") {
      tick = undefined;
    } else {
      tick = livePrice ?? undefined;
    }
    if (tick == null) return;

    const intervalSec = timeframe.intraday
      ? timeframeSeconds(timeframe.value)
      : 0;
    const last = bars[bars.length - 1];
    const lastSec = Math.floor(last.time / 1000);
    const nowSec = Math.floor(Date.now() / 1000);

    // Staleness gate: a tail ≥2 intervals behind the clock means the page was
    // backgrounded past the freshness window — merging would fuse the gap into
    // one candle. Refetch real bars instead (once per episode, with an
    // off-hours cooldown so overnight ext ticks don't refetch all night).
    if (intervalSec > 0 && nowSec - lastSec >= 2 * intervalSec) {
      const cooldownSec = Math.max(2 * intervalSec, 300);
      if (
        !staleRefetchPendingRef.current &&
        nowSec - staleRefetchLastSecRef.current >= cooldownSec
      ) {
        staleRefetchPendingRef.current = true;
        staleRefetchLastSecRef.current = nowSec;
        invalidateBars(symbol, timeframe.value, timeframe.days, showExt);
        setRefreshNonce((n) => n + 1);
      }
      return;
    }

    // Plausibility band vs the prior COMPLETED bar (live ticks mutate `last`,
    // so it can't be the reference): a >1.5×/<0.5× jump is a bad print.
    const ref = bars.length >= 2 ? bars[bars.length - 2].close : last.open;
    if (ref > 0 && (tick > ref * 1.5 || tick * 2 < ref)) return;

    // Single-step rollover: the clock crossed into the next bucket, so open
    // the forming bar there instead of polluting the completed one.
    if (intervalSec > 0 && nowSec >= lastSec + intervalSec) {
      engine.rollover(intervalSec * 1000, tick);
      return;
    }

    engine.applyLive(tick);
  }, [
    livePrice,
    extPrice,
    marketSession,
    showExt,
    loading,
    symbol,
    timeframe.value,
    timeframe.days,
    timeframe.intraday,
  ]);

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
              invalidateBars(symbol, timeframe.value, timeframe.days, showExt);
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
