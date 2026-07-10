"use client";

import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type RefObject,
} from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  TickMarkType,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Logical,
  type Time,
  type AutoscaleInfoProvider,
} from "lightweight-charts";

import {
  createDrawing,
  createPriceAlert,
  deleteDrawing,
  fetchDrawings,
  fetchPriceAlerts,
  fetchSessionLevels,
  updateDrawing,
  type ChartDrawing,
  type PriceAlert,
  type SessionLevel,
  type StockBar,
} from "@/lib/api";
import { fetchBarsCached, invalidateBars } from "@/lib/bars-cache";
import { cn } from "@/lib/utils";
import {
  CHART_TIMEFRAMES,
  formatCrosshairTime,
  formatTickMark,
  formatVolume,
  isDailyTimeframe,
  sessionLabel,
  timeframeSeconds,
  toChartTime,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertCreatePopover,
  defaultAlertDirection,
  type AlertDirection,
} from "@/components/trading/chart-alert-popover";
import {
  resolveCssColor,
  resolveDrawingColor,
  toChartColor,
  withAlpha,
} from "@/lib/chart-colors";
import { THEME_VARS_EVENT } from "@/lib/workspace/custom-themes";
import { OSCILLATOR_STUDIES, OVERLAY_STUDIES } from "@/lib/chart-studies";
import { DrawingShape, type LPoint } from "@/lib/chart-trendline";
import { SessionBands } from "@/lib/chart-session-bands";
import { macdSeries, rsiSeries } from "@/lib/indicators";

type Ohlcv = {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const ALERTS_POLL_MS = 20_000;
const LEVEL_HIT_PX = 6;
const HANDLE_HIT_PX = 9;
const TREND_COLOR_VAR = "--c-accent";
const CHART_HANDLE_SCROLL = {
  mouseWheel: false,
  pressedMouseMove: true,
  horzTouchDrag: true,
  vertTouchDrag: true,
};
const CHART_HANDLE_SCALE = {
  mouseWheel: false,
  pinch: true,
  axisPressedMouseMove: true,
  axisDoubleClickReset: true,
};
const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);

function timeToLogical(time: number, bars: Ohlcv[], interval: number): number {
  if (bars.length === 0) return 0;
  for (let i = 0; i < bars.length; i++) if ((bars[i].time as number) === time) return i;
  const first = bars[0].time as number;
  const last = bars[bars.length - 1].time as number;
  if (time <= first) return interval > 0 ? (time - first) / interval : 0;
  if (time >= last) return bars.length - 1 + (interval > 0 ? (time - last) / interval : 0);
  let best = 0, bd = Infinity;
  for (let i = 0; i < bars.length; i++) {
    const d = Math.abs((bars[i].time as number) - time);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}

function logicalToTime(logical: number, bars: Ohlcv[], interval: number): number {
  if (bars.length === 0) return 0;
  const r = Math.round(logical);
  if (r >= 0 && r < bars.length) return bars[r].time as number;
  const last = bars.length - 1;
  if (r < 0) return (bars[0].time as number) + r * interval;
  return (bars[last].time as number) + (r - last) * interval;
}

function distToSegment(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const cx = x1 + t * dx;
  const cy = y1 + t * dy;
  return Math.hypot(px - cx, py - cy);
}

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
    case "prior_low":
      return resolveCssColor("--fg-faint", "#616873");
    default:
      return resolveCssColor("--fg-faint", "#616873");
  }
}

type Props = {
  symbol: string;
  timeframe?: ChartTimeframe;
  style?: "candle" | "line";
  livePrice?: number;
  extPrice?: number | null;
  marketSession?: string | null;
  /** Hide the chart's own toolbar + overlay — the workspace frame provides the
      controls (symbol, timeframe, style, Studies), so the internal one is redundant. */
  compact?: boolean;
  /** Controlled session-node levels toggle (driven by the frame's Studies dropdown). */
  showLevels?: boolean;
  /** Visibility toggles (frame's eye menu): price-alert lines / user drawings+levels. */
  showAlerts?: boolean;
  showDrawings?: boolean;
  studies?: string[];
  /** Controlled ETH (extended-hours) toggle — pre/post candles + session shading.
      Defaults ON for intraday when uncontrolled; ignored on daily+ timeframes. */
  extended?: boolean;
};

export function TvLightChart({
  symbol,
  timeframe: timeframeProp,
  style: styleProp,
  livePrice,
  extPrice,
  marketSession,
  compact = false,
  showLevels: showLevelsProp,
  showAlerts = true,
  showDrawings = true,
  studies,
  extended: extendedProp,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainRef = useRef<ISeriesApi<"Candlestick" | "Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const levelLinesRef = useRef<IPriceLine[]>([]);
  const sessionLevelLinesRef = useRef<IPriceLine[]>([]);
  const extPriceLineRef = useRef<IPriceLine | null>(null);
  const sessionBandsRef = useRef<SessionBands | null>(null);
  const studySeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const oscSeriesRef = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const barsRef = useRef<Ohlcv[]>([]);
  // True right after the chart is (re)created — i.e. on timeframe/style change.
  // A symbol change reuses the same chart instance, so this stays false and we
  // preserve the user's current view instead of zooming out.
  const freshChartRef = useRef(true);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [internalTimeframe, setInternalTimeframe] = useState<ChartTimeframe>(
    CHART_TIMEFRAMES[5],
  );
  const [internalStyle, setInternalStyle] = useState<"candle" | "line">("candle");
  const timeframe = timeframeProp ?? internalTimeframe;
  const style = styleProp ?? internalStyle;
  const isControlledTimeframe = timeframeProp != null;
  const isControlledStyle = styleProp != null;
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [alertsRefreshKey, setAlertsRefreshKey] = useState(0);
  const [levels, setLevels] = useState<ChartDrawing[]>([]);
  const [levelsRefreshKey, setLevelsRefreshKey] = useState(0);
  const [shapes, setShapes] = useState<ChartDrawing[]>([]);
  const [drawTool, setDrawTool] = useState<null | "trendline" | "channel">(null);
  const drawToolRef = useRef(drawTool);
  const symbolRef = useRef(symbol);
  const pendingPointsRef = useRef<LPoint[]>([]);
  const previewRef = useRef<DrawingShape | null>(null);
  const shapePrimitivesRef = useRef<Map<number, DrawingShape>>(new Map());
  const [editingId, setEditingId] = useState<number | null>(null);
  const editingIdRef = useRef<number | null>(null);
  const draggingRef = useRef<{ id: number; handleIdx: number } | null>(null);
  const zoomHintTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showZoomHint, setShowZoomHint] = useState(false);
  const [alertMenu, setAlertMenu] = useState<{
    x: number;
    y: number;
    price: number;
    level?: ChartDrawing | null;
    shape?: ChartDrawing | null;
  } | null>(null);
  const [pendingAlert, setPendingAlert] = useState<{
    price: number;
    x: number;
    y: number;
  } | null>(null);
  const [alertPrice, setAlertPrice] = useState("");
  const [alertDirection, setAlertDirection] = useState<AlertDirection>("cross");
  const [alertRepeat, setAlertRepeat] = useState(false);
  const [alertSaving, setAlertSaving] = useState(false);
  const [alertError, setAlertError] = useState<string | null>(null);
  const alertMenuRef = useRef<HTMLDivElement>(null);
  const livePriceRef = useRef(livePrice);
  // Internal toggle for the non-compact (/trading) header; in the workspace the
  // frame's Studies dropdown drives it via the `showLevels` prop (controlled).
  const [internalShowLevels, setInternalShowLevels] = useState(false);
  const showLevels = showLevelsProp ?? internalShowLevels;
  // ETH (extended-hours) toggle. Defaults ON for intraday when uncontrolled; on
  // daily+ there is no intraday session so ext is always off (and the toggle hides).
  const [internalExtended, setInternalExtended] = useState(true);
  const extended = extendedProp ?? internalExtended;
  const showExt = timeframe.intraday && extended;
  const [sessionLevels, setSessionLevels] = useState<SessionLevel[]>([]);
  const [legend, setLegend] = useState<string>("");
  const [session, setSession] = useState(sessionLabel());
  const [themeNonce, setThemeNonce] = useState(0);
  const [barsVersion, setBarsVersion] = useState(0);
  // Bumped by the live-tick layer to force the data effect to refetch when it
  // detects the cached tail has gone stale (backgrounded past the freshness
  // window). The ref de-dupes a burst of ticks into ONE refetch; it's cleared
  // when the fresh data lands so a later staleness can trigger again.
  const [refreshNonce, setRefreshNonce] = useState(0);
  const staleRefetchPendingRef = useRef(false);
  // Off-hours the tail stays legitimately old (no new intraday bars overnight)
  // while ext ticks keep arriving — without a cooldown, every post-refetch tick
  // would fire another cache-busting fetch all night. A staleness episode may
  // only re-fire after a cooldown when the refetch didn't heal it.
  const staleRefetchLastSecRef = useRef(0);

  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() => setThemeNonce((n) => n + 1));
    obs.observe(el, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const onVars = () => setThemeNonce((n) => n + 1);
    window.addEventListener(THEME_VARS_EVENT, onVars);
    return () => {
      obs.disconnect();
      window.removeEventListener(THEME_VARS_EVENT, onVars);
    };
  }, []);

  useEffect(() => {
    const id = setInterval(() => setSession(sessionLabel()), 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    livePriceRef.current = livePrice;
  }, [livePrice]);

  useEffect(() => {
    drawToolRef.current = drawTool;
  }, [drawTool]);

  useEffect(() => {
    editingIdRef.current = editingId;
  }, [editingId]);

  useEffect(() => {
    symbolRef.current = symbol;
  }, [symbol]);

  const shapePixelPoints = (prim: DrawingShape) => {
    const chart = chartRef.current;
    const series = mainRef.current;
    if (!chart || !series) return null;
    const ts = chart.timeScale();
    const px = (lp: LPoint) => ({
      x: ts.logicalToCoordinate(lp.logical as Logical),
      y: series.priceToCoordinate(lp.price),
    });
    return { p1: px(prim.p1), p2: px(prim.p2), handles: prim.handlePoints().map(px) };
  };

  const drawingHitId = (x: number, y: number): number | null => {
    let best: number | null = null;
    let bd = HANDLE_HIT_PX + 1;
    for (const [id, prim] of shapePrimitivesRef.current) {
      const g = shapePixelPoints(prim);
      if (!g || g.p1.x == null || g.p1.y == null || g.p2.x == null || g.p2.y == null) continue;
      const segs: Array<[number, number, number, number]> = [[g.p1.x, g.p1.y, g.p2.x, g.p2.y]];
      if (prim.kind === "channel") {
        const s = mainRef.current!;
        const oy1 = s.priceToCoordinate(prim.p1.price + prim.width);
        const oy2 = s.priceToCoordinate(prim.p2.price + prim.width);
        if (oy1 != null && oy2 != null) segs.push([g.p1.x, oy1, g.p2.x, oy2]);
      }
      for (const [x1, y1, x2, y2] of segs) {
        const d = distToSegment(x, y, x1, y1, x2, y2);
        if (d <= HANDLE_HIT_PX && d < bd) {
          bd = d;
          best = id;
        }
      }
    }
    return best;
  };

  useEffect(() => {
    const id = setInterval(
      () => setAlertsRefreshKey((key) => key + 1),
      ALERTS_POLL_MS,
    );
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAlertMenu(null);
        setPendingAlert(null);
        setAlertError(null);
        setEditingId(null);
        if (drawToolRef.current) {
          setDrawTool(null);
          pendingPointsRef.current = [];
          if (previewRef.current) {
            try {
              mainRef.current?.detachPrimitive(previewRef.current);
            } catch {
              /* chart removed */
            }
            previewRef.current = null;
          }
        }
        return;
      }
      if (
        (event.key === "Delete" || event.key === "Backspace") &&
        editingIdRef.current != null &&
        !pendingAlert &&
        !alertMenu
      ) {
        const id = editingIdRef.current;
        void deleteDrawing(id).then(() => {
          setEditingId(null);
          setLevelsRefreshKey((k) => k + 1);
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [alertMenu, pendingAlert]);

  useEffect(() => {
    if (!alertMenu) return;
    const onPointerDown = (event: PointerEvent) => {
      const menu = alertMenuRef.current;
      if (menu?.contains(event.target as Node)) return;
      setAlertMenu(null);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [alertMenu]);

  function closeAlertPopover() {
    setPendingAlert(null);
    setAlertError(null);
  }

  function handleChartContextMenu(event: MouseEvent) {
    event.preventDefault();
    const container = containerRef.current;
    const series = mainRef.current;
    const chart = chartRef.current;
    if (!container || !series) return;
    const rect = container.getBoundingClientRect();
    const xRel = event.clientX - rect.left;
    const yRel = event.clientY - rect.top;
    const raw = series.coordinateToPrice(yRel);
    if (raw == null) return;
    const price = Math.round(raw * 100) / 100;

    let hitLevel: ChartDrawing | null = null;
    let hitShape: ChartDrawing | null = null;
    let bestLevelDist = LEVEL_HIT_PX + 1;
    let bestShapeDist = LEVEL_HIT_PX + 1;
    for (const lvl of levels) {
      const lvlPrice = lvl.data.price;
      if (lvlPrice == null) continue;
      const ly = series.priceToCoordinate(lvlPrice);
      if (ly == null) continue;
      const dist = Math.abs(ly - yRel);
      if (dist <= LEVEL_HIT_PX && dist < bestLevelDist) {
        bestLevelDist = dist;
        hitLevel = lvl;
      }
    }

    if (chart) {
      const ts = chart.timeScale();
      for (const [id, primitive] of shapePrimitivesRef.current) {
        const x1 = ts.logicalToCoordinate(primitive.p1.logical as Logical);
        const y1 = series.priceToCoordinate(primitive.p1.price);
        const x2 = ts.logicalToCoordinate(primitive.p2.logical as Logical);
        const y2 = series.priceToCoordinate(primitive.p2.price);
        if (x1 == null || y1 == null || x2 == null || y2 == null) continue;
        const distBase = distToSegment(xRel, yRel, x1, y1, x2, y2);
        let dist = distBase;
        if (primitive.kind === "channel") {
          const y1o = series.priceToCoordinate(primitive.p1.price + primitive.width);
          const y2o = series.priceToCoordinate(primitive.p2.price + primitive.width);
          if (y1o != null && y2o != null) {
            dist = Math.min(distBase, distToSegment(xRel, yRel, x1, y1o, x2, y2o));
          }
        }
        if (dist <= LEVEL_HIT_PX && dist < bestShapeDist) {
          bestShapeDist = dist;
          hitShape = shapes.find((s) => s.id === id) ?? null;
        }
      }
    }
    if (hitLevel) hitShape = null;

    setPendingAlert(null);
    setAlertMenu({
      x: xRel,
      y: yRel,
      price,
      level: hitLevel ?? null,
      shape: hitShape ?? null,
    });
  }

  function handleSetAlertFromMenu() {
    if (!alertMenu) return;
    const seedPrice = alertMenu.level?.data.price ?? alertMenu.price;
    const currentPrice =
      livePriceRef.current ?? barsRef.current.at(-1)?.close;
    setAlertPrice(String(seedPrice));
    setAlertDirection(defaultAlertDirection(seedPrice, currentPrice));
    setAlertRepeat(false);
    setAlertError(null);
    setPendingAlert({
      price: seedPrice,
      x: alertMenu.x,
      y: alertMenu.y,
    });
    setAlertMenu(null);
  }

  async function handleAddLevel() {
    if (!alertMenu) return;
    try {
      await createDrawing({
        symbol,
        kind: "level",
        data: { price: alertMenu.price },
      });
      setLevelsRefreshKey((key) => key + 1);
      setAlertMenu(null);
    } catch {
      /* ignore — menu stays open for retry */
    }
  }

  async function handleRemoveLevel() {
    if (!alertMenu?.level) return;
    try {
      await deleteDrawing(alertMenu.level.id);
      setLevelsRefreshKey((key) => key + 1);
      setAlertMenu(null);
    } catch {
      /* ignore — menu stays open for retry */
    }
  }

  async function handleRemoveShape() {
    if (!alertMenu?.shape) return;
    const id = alertMenu.shape.id;
    try {
      await deleteDrawing(id);
      if (editingIdRef.current === id) setEditingId(null);
      setLevelsRefreshKey((key) => key + 1);
      setAlertMenu(null);
    } catch {
      /* ignore — menu stays open for retry */
    }
  }

  function handleDrawTrendline() {
    setDrawTool("trendline");
    pendingPointsRef.current = [];
    setAlertMenu(null);
  }

  function handleDrawChannel() {
    setDrawTool("channel");
    pendingPointsRef.current = [];
    setAlertMenu(null);
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
      setAlertsRefreshKey((key) => key + 1);
      closeAlertPopover();
    } catch {
      setAlertError("Could not create alert");
    } finally {
      setAlertSaving(false);
    }
  }

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const background = resolveCssColor(
      "--chart-background",
      resolveCssColor("--background", "#131519"),
    );
    const text = resolveCssColor("--fg-dim", "#9aa1ab");
    const border = toChartColor(
      resolveCssColor("--border", "rgba(255,255,255,0.09)"),
      "rgba(255,255,255,0.09)",
    );
    const crosshairLine = toChartColor(
      resolveCssColor("--fg-faint", "#616873"),
      "#616873",
    );
    const crosshairLabel = toChartColor(
      resolveCssColor("--border-strong", "#333a44"),
      "#333a44",
    );
    const accent = resolveCssColor("--chart-accent", "#5ad1be");
    const down = resolveCssColor("--chart-down", "#ef6f6c");
    const intraday = !isDailyTimeframe(timeframe.value);

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: background },
        textColor: text,
        fontFamily: "var(--font-sans), system-ui, sans-serif",
        // Per-chart logo off; the required attribution lives ONCE in the
        // workspace footer (see workspace-grid) per the lightweight-charts
        // license terms.
        attributionLogo: false,
        panes: {
          separatorColor: crosshairLabel,
          // Same as idle — the accent hover band read as a "huge green bar"
          // over the volume pane and sat in the way of alert placement.
          separatorHoverColor: crosshairLabel,
          enableResize: true,
        },
      },
      grid: {
        vertLines: { color: border },
        horzLines: { color: border },
      },
      rightPriceScale: {
        borderColor: border,
        scaleMargins: { top: 0.08, bottom: 0.06 },
      },
      timeScale: {
        borderColor: border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) =>
          formatTickMark(time as number, tickMarkType),
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: crosshairLine,
          labelBackgroundColor: crosshairLabel,
        },
        horzLine: {
          color: crosshairLine,
          labelBackgroundColor: crosshairLabel,
        },
      },
      localization: {
        timeFormatter: (time: number) =>
          formatCrosshairTime(time, intraday),
      },
      autoSize: true,
      handleScroll: CHART_HANDLE_SCROLL,
      handleScale: CHART_HANDLE_SCALE,
    });

    const mainSeries =
      style === "candle"
        ? chart.addSeries(CandlestickSeries, {
            upColor: accent,
            downColor: down,
            borderUpColor: accent,
            borderDownColor: down,
            wickUpColor: accent,
            wickDownColor: down,
          })
        : chart.addSeries(LineSeries, {
            color: accent,
            lineWidth: 2,
          });

    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        autoscaleInfoProvider: ((original) => {
          const res = original();
          if (res && res.priceRange) res.priceRange.minValue = 0;
          return res;
        }) satisfies AutoscaleInfoProvider,
      },
      1,
    );
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.15, bottom: 0 },
      borderColor: border,
    });

    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes[1]?.setStretchFactor(1);

    chartRef.current = chart;
    mainRef.current = mainSeries;
    volumeRef.current = volumeSeries;
    freshChartRef.current = true;

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData.size) {
        setLegend("");
        return;
      }
      const bar = barsRef.current.find((b) => b.time === param.time);
      if (!bar) {
        setLegend("");
        return;
      }
      const chg = bar.open ? ((bar.close - bar.open) / bar.open) * 100 : 0;
      const sign = chg >= 0 ? "+" : "";
      setLegend(
        `${formatCrosshairTime(param.time as number, intraday)} · O ${bar.open.toFixed(2)} H ${bar.high.toFixed(2)} L ${bar.low.toFixed(2)} C ${bar.close.toFixed(2)} (${sign}${chg.toFixed(2)}%) · Vol ${formatVolume(bar.volume)}`,
      );
    });

    const drawColor = resolveCssColor(TREND_COLOR_VAR, "#5ad1be");

    chart.subscribeClick((param) => {
      if (!param.point) return;
      const tool = drawToolRef.current;
      if (!tool) {
        setEditingId(drawingHitId(param.point.x, param.point.y));
        return;
      }
      const s = mainRef.current;
      if (!s) return;
      const logical = chart.timeScale().coordinateToLogical(param.point.x);
      const price = s.coordinateToPrice(param.point.y);
      if (logical == null || price == null) return;
      const pts = [...pendingPointsRef.current, { logical: logical as number, price }];
      pendingPointsRef.current = pts;
      if (pts.length >= 2) {
        const bars = barsRef.current;
        const interval = timeframeSeconds(timeframe.value);
        const toTimePoint = (p: LPoint) => ({ time: logicalToTime(p.logical, bars, interval), price: p.price });
        const data =
          tool === "channel"
            ? { points: [toTimePoint(pts[0]), toTimePoint(pts[1])], width: pts[0].price * 0.02, color: drawColor }
            : { points: [toTimePoint(pts[0]), toTimePoint(pts[1])], color: drawColor };
        void createDrawing({ symbol: symbolRef.current, kind: tool, data }).then(() => setLevelsRefreshKey((k) => k + 1));
        if (previewRef.current) {
          try { mainRef.current?.detachPrimitive(previewRef.current); } catch { /* chart removed */ }
          previewRef.current = null;
        }
        pendingPointsRef.current = [];
        setDrawTool(null);
      }
    });

    chart.subscribeCrosshairMove((param) => {
      if (!drawToolRef.current || pendingPointsRef.current.length !== 1 || !param.point) return;
      const s = mainRef.current;
      if (!s) return;
      const logical = chart.timeScale().coordinateToLogical(param.point.x);
      const price = s.coordinateToPrice(param.point.y);
      if (logical == null || price == null) return;
      const p1 = pendingPointsRef.current[0];
      const kind = drawToolRef.current;
      const width = kind === "channel" ? p1.price * 0.02 : 0;
      if (!previewRef.current) {
        previewRef.current = new DrawingShape(-1, kind, p1, { logical: logical as number, price }, width, drawColor);
        s.attachPrimitive(previewRef.current);
      } else {
        previewRef.current.p2 = { logical: logical as number, price };
        previewRef.current.width = width;
        previewRef.current.requestUpdate();
      }
    });

    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const ts = chart.timeScale();
        const cur = ts.options().barSpacing;
        const next = Math.min(60, Math.max(0.5, cur * Math.exp(-e.deltaY * 0.0015)));
        ts.applyOptions({ barSpacing: next });
        return;
      }
      setShowZoomHint(true);
      if (zoomHintTimeoutRef.current) clearTimeout(zoomHintTimeoutRef.current);
      zoomHintTimeoutRef.current = setTimeout(() => setShowZoomHint(false), 800);
    };

    el.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      el.removeEventListener("wheel", onWheel);
      if (zoomHintTimeoutRef.current) clearTimeout(zoomHintTimeoutRef.current);
      pendingPointsRef.current = [];
      previewRef.current = null;
      setDrawTool(null);
      chart.remove();
      chartRef.current = null;
      mainRef.current = null;
      volumeRef.current = null;
      extPriceLineRef.current = null;
    };
  }, [timeframe.value, style, themeNonce]);

  useEffect(() => {
    fetchPriceAlerts(symbol)
      .then(setAlerts)
      .catch(() => setAlerts([]));
  }, [symbol, alertsRefreshKey]);

  useEffect(() => {
    fetchDrawings(symbol)
      .then((list) => {
        setLevels(list.filter((d) => d.kind === "level"));
        setShapes(list.filter((d) => d.kind === "trendline" || d.kind === "channel"));
      })
      .catch(() => {
        setLevels([]);
        setShapes([]);
      });
  }, [symbol, levelsRefreshKey]);

  useEffect(() => {
    if (!showLevels) {
      setSessionLevels([]);
      return;
    }
    let cancelled = false;
    fetchSessionLevels(symbol)
      .then((data) => {
        if (!cancelled) setSessionLevels(data.levels);
      })
      .catch(() => {
        if (!cancelled) setSessionLevels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, showLevels]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Capture the current view so a symbol change can keep it (logical range is
    // index-based, so the same zoom/scroll transfers across symbols).
    const priorRange = freshChartRef.current
      ? null
      : (chartRef.current?.timeScale().getVisibleLogicalRange() ?? null);

    // Drop the previous timeframe/symbol's bars NOW, not when the fetch lands.
    // The live-tick merge effect runs in this same commit with the pre-switch
    // `loading` closure, and merging a tick into the OLD bars paints a stale bar
    // into the NEW chart — from 1D that appends a BusinessDay time to a series
    // about to receive numeric times, which corrupts the time scale and every
    // later series call throws (the 1D→4H page kill); between intraday
    // timeframes it draws phantom giant wicks until the fetch lands.
    barsRef.current = [];

    fetchBarsCached(symbol, {
      timeframe: timeframe.value,
      days: timeframe.days,
      extended: showExt,
    })
      .then((data) => {
        if (cancelled) return;
        const accent = resolveCssColor("--chart-accent", "#5ad1be");
        const down = resolveCssColor("--chart-down", "#ef6f6c");

        const ohlcv: Ohlcv[] = data.bars.map((b: StockBar) => ({
          time: toChartTime(b.time, data.timeframe) as Time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
          volume: b.volume,
        }));
        barsRef.current = ohlcv;

        if (style === "line") {
          mainRef.current?.setData(
            ohlcv.map((b) => ({ time: b.time, value: b.close })),
          );
        } else {
          mainRef.current?.setData(ohlcv);
        }

        volumeRef.current?.setData(
          ohlcv.map((b, i) => {
            const prev = i > 0 ? ohlcv[i - 1].close : b.open;
            return {
              time: b.time,
              value: b.volume,
              color: b.close >= prev ? withAlpha(accent, 0.6) : withAlpha(down, 0.6),
            };
          }),
        );

        const timeScale = chartRef.current?.timeScale();
        const count = ohlcv.length;
        if (timeScale && count > 0) {
          // Anchor the most recent candle ~80% across (a 20% right margin), and
          // always show recent data. On a symbol switch we PRESERVE THE ZOOM
          // (the visible bar count) but re-anchor to the latest bar — restoring
          // the old absolute indices would point at different bars on a ticker
          // with a different history length and drift the view back in time.
          const RIGHT_MARGIN_FRAC = 0.2;
          const DEFAULT_VISIBLE_BARS = 150;
          const visibleBars =
            priorRange && priorRange.to > priorRange.from
              ? priorRange.to - priorRange.from
              : Math.min(count, DEFAULT_VISIBLE_BARS);
          const rightMargin = RIGHT_MARGIN_FRAC * visibleBars;
          // rightOffset keeps the ~20% right margin PERSISTENT. The chart overhaul's
          // extra panes/studies trigger resize refits that clobber a one-shot
          // setVisibleLogicalRange, so the latest candle was snapping to the right edge;
          // rightOffset is re-applied by the chart across those refits + new live bars.
          timeScale.applyOptions({ rightOffset: rightMargin });
          const to = count - 1 + rightMargin;
          timeScale.setVisibleLogicalRange({ from: to - visibleBars, to });
          // Refit the price axis to the new symbol (a $700→$50 switch rescales
          // vertically even though the time window is unchanged).
          mainRef.current?.priceScale().applyOptions({ autoScale: true });
        }
        freshChartRef.current = false;
        setBarsVersion((v) => v + 1);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load bars");
        mainRef.current?.setData([]);
        volumeRef.current?.setData([]);
        barsRef.current = [];
        setBarsVersion((v) => v + 1);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
        // The (possibly cache-invalidating) refetch has landed — allow the
        // live-tick layer to trigger another staleness refetch if needed.
        staleRefetchPendingRef.current = false;
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, style, themeNonce, refreshNonce, showExt]);

  useEffect(() => {
    const series = mainRef.current;
    if (!series) return;
    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    const alertUp = resolveCssColor("--c-green", "#5bc98a");
    const alertDown = resolveCssColor("--c-red", "#ef6f6c");
    const alertCross = resolveCssColor("--fg-dim", "#9aa1ab");
    priceLinesRef.current = showAlerts
      ? alerts.map((alert) => {
          // Founder spec: no "Alert <price>" pill on the axis — a small bell
          // with +/− (above/below; cross stays bare), ↻ marks a repeating alert.
          const glyph =
            alert.direction === "above"
              ? "🔔+"
              : alert.direction === "below"
                ? "🔔−"
                : "🔔";
          const color =
            alert.direction === "above"
              ? alertUp
              : alert.direction === "below"
                ? alertDown
                : alertCross;
          return series.createPriceLine({
            price: alert.price,
            color,
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: false,
            title: alert.repeat ? `${glyph}↻` : glyph,
          });
        })
      : [];
  }, [alerts, loading, themeNonce, showAlerts]);

  useEffect(() => {
    const series = mainRef.current;
    if (!series) return;
    for (const line of levelLinesRef.current) {
      series.removePriceLine(line);
    }
    const neutral = resolveCssColor("--fg-dim", "#9aa1ab");
    levelLinesRef.current = showDrawings
      ? levels
          .filter((lvl) => lvl.data.price != null)
          .map((lvl) => {
            const p = lvl.data.price!;
            return series.createPriceLine({
              price: p,
              color: neutral,
              lineWidth: 1,
              lineStyle: 0,
              axisLabelVisible: true,
              title: `⊹ ${p.toFixed(2)}`,
            });
          })
      : [];
  }, [levels, loading, themeNonce, showDrawings]);

  useEffect(() => {
    const series = mainRef.current;

    const removeSessionLevelLines = () => {
      if (!series) return;
      for (const line of sessionLevelLinesRef.current) {
        series.removePriceLine(line);
      }
      sessionLevelLinesRef.current = [];
    };

    if (!showLevels || !series || loading) {
      removeSessionLevelLines();
      return removeSessionLevelLines;
    }

    removeSessionLevelLines();
    sessionLevelLinesRef.current = sessionLevels.map((level) =>
      series.createPriceLine({
        price: level.price,
        color: sessionLevelColor(level.kind),
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: level.name,
      }),
    );

    return removeSessionLevelLines;
  }, [showLevels, sessionLevels, loading, timeframe.value, style, themeNonce]);

  useEffect(() => {
    const series = mainRef.current;

    const removeExtPriceLine = () => {
      if (extPriceLineRef.current && series) {
        try {
          series.removePriceLine(extPriceLineRef.current);
        } catch {
          /* series removed */
        }
      }
      extPriceLineRef.current = null;
    };

    const showExt =
      extPrice != null &&
      marketSession != null &&
      (marketSession === "pre" ||
        marketSession === "post" ||
        marketSession === "overnight" ||
        // weekend/holiday: candles (extended) end on the last ext print — keep
        // the dashed line so the label can't sit a session behind the candles
        marketSession === "closed");

    if (!showExt || !series || loading) {
      removeExtPriceLine();
      return removeExtPriceLine;
    }

    removeExtPriceLine();
    const color = withAlpha(resolveCssColor(TREND_COLOR_VAR, "#5ad1be"), 0.75);
    extPriceLineRef.current = series.createPriceLine({
      price: extPrice,
      color,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "ext",
    });

    return removeExtPriceLine;
  }, [extPrice, marketSession, loading, timeframe.value, style, symbol, themeNonce]);

  // Session shading: subtle pre/post background bands, drawn beneath the candles.
  // The primitive reads barsRef live, so bands recompute automatically as live ext
  // ticks roll new post/pre candles (the chart re-renders on each series.update).
  useEffect(() => {
    const series = mainRef.current;

    const detach = () => {
      if (sessionBandsRef.current && series) {
        try {
          series.detachPrimitive(sessionBandsRef.current);
        } catch {
          /* series removed */
        }
      }
      sessionBandsRef.current = null;
    };

    if (!showExt || !series || loading) {
      detach();
      return detach;
    }

    detach();
    const tint = resolveCssColor("--fg-faint", "#616873");
    const prim = new SessionBands(
      () => barsRef.current as unknown as ReadonlyArray<{ time: number }>,
      { pre: withAlpha(tint, 0.05), post: withAlpha(tint, 0.08) },
    );
    series.attachPrimitive(prim);
    sessionBandsRef.current = prim;

    return detach;
  }, [showExt, loading, symbol, timeframe.value, style, themeNonce, barsVersion]);

  useEffect(() => {
    const chart = chartRef.current;
    const bars = barsRef.current;
    if (!chart || bars.length === 0) return;
    const closes = bars.map((b) => b.close);
    const enabled = studies ?? [];
    const created: ISeriesApi<"Line">[] = [];
    for (const def of OVERLAY_STUDIES) {
      if (!enabled.includes(def.id)) continue;
      const values = def.compute(closes);
      const data = values
        .map((v, i) => (v == null ? null : { time: bars[i].time, value: v }))
        .filter((p): p is { time: Time; value: number } => p !== null);
      const line = chart.addSeries(LineSeries, {
        color: def.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(data);
      created.push(line);
    }
    studySeriesRef.current = created;
    return () => {
      for (const s of created) {
        try {
          chart.removeSeries(s);
        } catch {
          /* chart already removed on recreate */
        }
      }
      studySeriesRef.current = [];
    };
  }, [barsVersion, (studies ?? []).join(","), themeNonce]);

  useEffect(() => {
    const chart = chartRef.current;
    const bars = barsRef.current;
    if (!chart || bars.length === 0) return;
    const enabledOsc = (studies ?? []).filter((id) =>
      OSCILLATOR_STUDIES.some((s) => s.id === id),
    );
    if (enabledOsc.length === 0) return;

    const closes = bars.map((b) => b.close);
    const border = toChartColor(
      resolveCssColor("--border", "rgba(255,255,255,0.09)"),
      "rgba(255,255,255,0.09)",
    );
    const accent = resolveCssColor("--chart-accent", "#5ad1be");
    const down = resolveCssColor("--chart-down", "#ef6f6c");
    const created: ISeriesApi<"Line" | "Histogram">[] = [];
    let paneIndex = 2; // 0 = price, 1 = volume

    for (const def of OSCILLATOR_STUDIES) {
      if (!enabledOsc.includes(def.id)) continue;

      if (def.id === "rsi") {
        const values = rsiSeries(closes, 14);
        const data = values
          .map((v, i) => (v == null ? null : { time: bars[i].time, value: v }))
          .filter((p): p is { time: Time; value: number } => p !== null);
        const line = chart.addSeries(
          LineSeries,
          {
            color: "#c792ea",
            lineWidth: 1,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
          },
          paneIndex,
        );
        line.setData(data);
        line.priceScale().applyOptions({ borderColor: border });
        line.createPriceLine({ price: 70, color: border, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
        line.createPriceLine({ price: 30, color: border, lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
        created.push(line);
        chart.panes()[paneIndex]?.setStretchFactor(1);
        paneIndex++;
      } else if (def.id === "macd") {
        const { macd, signal, histogram } = macdSeries(closes);
        const toData = (arr: (number | null)[]) =>
          arr
            .map((v, i) => (v == null ? null : { time: bars[i].time, value: v }))
            .filter((p): p is { time: Time; value: number } => p !== null);
        const histData = histogram
          .map((v, i) =>
            v == null
              ? null
              : { time: bars[i].time, value: v, color: withAlpha(v >= 0 ? accent : down, 0.5) },
          )
          .filter((p): p is { time: Time; value: number; color: string } => p !== null);

        const hist = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, paneIndex);
        hist.setData(histData);
        const macdLine = chart.addSeries(
          LineSeries,
          { color: "#4d9de0", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
          paneIndex,
        );
        macdLine.setData(toData(macd));
        const signalLine = chart.addSeries(
          LineSeries,
          { color: "#e0b040", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
          paneIndex,
        );
        signalLine.setData(toData(signal));
        macdLine.priceScale().applyOptions({ borderColor: border });
        created.push(hist, macdLine, signalLine);
        chart.panes()[paneIndex]?.setStretchFactor(1);
        paneIndex++;
      }
    }

    oscSeriesRef.current = created;
    return () => {
      for (const s of created) {
        try {
          chart.removeSeries(s);
        } catch {
          /* chart already removed */
        }
      }
      oscSeriesRef.current = [];
      try {
        for (let i = chart.panes().length - 1; i >= 2; i--) {
          try {
            chart.removePane(i);
          } catch {
            /* already gone */
          }
        }
      } catch {
        /* chart removed */
      }
    };
  }, [
    barsVersion,
    (studies ?? []).filter((id) => OSCILLATOR_STUDIES.some((s) => s.id === id)).join(","),
    themeNonce,
  ]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = mainRef.current;
    if (!chart || !series) return;
    const bars = barsRef.current;
    const interval = timeframeSeconds(timeframe.value);
    const map = new Map<number, DrawingShape>();
    for (const shape of showDrawings ? shapes : []) {
      const pts = shape.data.points;
      if (!pts || pts.length < 2) continue;
      const toL = (p: { time: number | null; price: number }): LPoint => ({
        logical: timeToLogical((p.time as number) ?? 0, bars, interval),
        price: p.price,
      });
      const d = new DrawingShape(
        shape.id,
        shape.kind === "channel" ? "channel" : "trendline",
        toL(pts[0]), toL(pts[1]),
        shape.data.width ?? 0,
        // Semantic names (native-engine writes, relay §5.1) resolve to theme
        // vars; legacy raw hex passes through.
        resolveDrawingColor(shape.data.color),
      );
      d.selected = shape.id === editingId;
      series.attachPrimitive(d);
      map.set(shape.id, d);
    }
    shapePrimitivesRef.current = map;
    return () => {
      for (const d of map.values()) {
        try { series.detachPrimitive(d); } catch { /* series removed */ }
      }
      shapePrimitivesRef.current = new Map();
    };
  }, [shapes, barsVersion, timeframe.value, style, themeNonce, editingId, showDrawings]);

  useEffect(() => {
    if (loading) return;
    const bars = barsRef.current;
    if (bars.length === 0) return;
    const series = mainRef.current;
    if (!series) return;

    // Which field drives the forming candle depends on the session (commit b41731b
    // froze the regular `price` outside RTH and routes stream ticks to `ext_price`):
    //   regular / unknown → livePrice drives the RTH candle (byte-identical to before)
    //   pre / post / overnight → ext_price drives the forming ext candle when ETH on
    //   closed            → no candle merge (weekend/holiday last print only)
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

    const last = bars[bars.length - 1];

    // Time-type sanity: intraday bars carry numeric epochs, daily+ carry
    // calendar objects. A mismatch means `bars` still belongs to the previous
    // timeframe (switch window) — never merge a tick into those.
    if (timeframe.intraday !== (typeof last.time === "number")) return;

    // Freshness gate (before any merge/rollover branch): if the cached tail is
    // ≥2 intervals behind the wall clock — the page was backgrounded past the
    // freshness window — merging the live tick would fuse the whole gap into one
    // candle (the long-candle artifact) or fabricate a jumped bucket. Instead,
    // drop the stale cache entry and refetch real bars: ONE refetch per staleness
    // episode, de-duped by staleRefetchPendingRef (cleared when the fetch lands).
    // Intraday only (numeric bar time); daily+ use object times with different
    // interval semantics and aren't affected by this bug.
    const staleIntervalSec = timeframe.intraday
      ? timeframeSeconds(timeframe.value)
      : 0;
    if (staleIntervalSec > 0 && typeof last.time === "number") {
      const nowSec = Math.floor(Date.now() / 1000);
      if (nowSec - (last.time as number) >= 2 * staleIntervalSec) {
        const cooldownSec = Math.max(2 * staleIntervalSec, 300);
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
    }

    // Reject an implausible live tick (bad print / crossed quote): a value >1.5× or
    // <1/2 of the last COMPLETED bar's close is never a real intraday move and would
    // spike the forming candle's wick (e.g. a $52 stock printing $750). Mirrors the
    // server's ±50% band (the primary guard). Reference the prior completed bar — not
    // `last`, which live ticks mutate — so one bad tick can't poison the reference and
    // freeze the chart.
    const ref = bars.length >= 2 ? bars[bars.length - 2].close : last.open;
    if (ref > 0 && (tick > ref * 1.5 || tick * 2 < ref)) return;

    // Intraday: when the wall clock crosses into the next interval bucket, roll the
    // forming candle over to a NEW bar (the live tick has no timestamp, so bucket off
    // the client clock, aligned to the historical bars' own grid via last.time).
    const intervalSec = timeframe.intraday ? timeframeSeconds(timeframe.value) : 0;
    if (intervalSec > 0 && typeof last.time === "number") {
      const nowSec = Math.floor(Date.now() / 1000);
      const lastTime = last.time as number;
      if (nowSec >= lastTime + intervalSec) {
        const steps = Math.floor((nowSec - lastTime) / intervalSec);
        if (steps > 1) {
          // Unreachable: the freshness gate above already catches ≥2 intervals
          // stale. Belt-and-suspenders — never fabricate a jumped bucket; route
          // to the same real refetch instead.
          if (!staleRefetchPendingRef.current) {
            staleRefetchPendingRef.current = true;
            invalidateBars(symbol, timeframe.value, timeframe.days, showExt);
            setRefreshNonce((n) => n + 1);
          }
          return;
        }
        const newTime = (lastTime + steps * intervalSec) as Time;
        const newBar: Ohlcv = {
          time: newTime,
          open: tick,
          high: tick,
          low: tick,
          close: tick,
          volume: 0,
        };
        bars.push(newBar);
        try {
          if (style === "line") {
            series.update({ time: newTime, value: tick });
          } else {
            series.update(newBar);
          }
          volumeRef.current?.update({ time: newTime, value: 0 });
        } catch {
          /* stale series/time during a switch window — next fetch repaints */
        }
        return;
      }
    }

    // Heal a still-FORMING bar already poisoned by an earlier spike: if its high sits
    // >1.5× (or its low <1/2 — the −50% mirror) off BOTH the reference close and the
    // current live price, that wick came from a bad print baked in before this guard
    // tightened — collapse it back onto the real OHLC before extending. Only the
    // forming bar is healed here; completed bars heal via refetch, and the server
    // ±50% band is the primary fix. Accepted residual: a legit extreme V-wick could
    // be eaten here — display-only, rare, and a refetch RESTORES a real wick since it
    // exists in Schwab's bars.
    if (ref > 0 && last.high > ref * 1.5 && last.high > tick * 1.5) {
      last.high = Math.max(last.open, last.close, tick);
    }
    if (ref > 0 && last.low > 0 && last.low * 2 < ref && last.low * 2 < tick) {
      last.low = Math.min(last.open, last.close, tick);
    }

    last.close = tick;
    last.high = Math.max(last.high, tick);
    last.low = Math.min(last.low, tick);

    // A series.update() that violates the library's invariants (disposed series,
    // older-than-last time) must degrade to a skipped paint, never take down the
    // React tree — the next setData self-heals.
    try {
      if (style === "line") {
        series.update({ time: last.time, value: tick });
      } else {
        series.update(last);
      }
    } catch {
      /* stale series/time during a switch window — next fetch repaints */
    }
  }, [
    livePrice,
    extPrice,
    marketSession,
    showExt,
    loading,
    style,
    symbol,
    timeframe.value,
    timeframe.days,
    timeframe.intraday,
  ]);

  return (
    <div className="relative flex h-full w-full min-h-0 flex-col">
      {!compact && (
      <div className="flex flex-wrap items-center gap-1 border-b border-border/80 px-2 py-1.5">
        {CHART_TIMEFRAMES.map((tf) => (
          <Button
            key={tf.label}
            type="button"
            size="sm"
            variant={timeframe.label === tf.label ? "secondary" : "ghost"}
            className="h-7 px-2 text-xs"
            onClick={() => {
              if (!isControlledTimeframe) setInternalTimeframe(tf);
            }}
          >
            {tf.label}
          </Button>
        ))}
        <span className="mx-1 h-4 w-px bg-border" />
        <Button
          type="button"
          size="sm"
          variant={style === "candle" ? "secondary" : "ghost"}
          className="h-7 px-2 text-xs"
          onClick={() => {
            if (!isControlledStyle) setInternalStyle("candle");
          }}
        >
          Candles
        </Button>
        <Button
          type="button"
          size="sm"
          variant={style === "line" ? "secondary" : "ghost"}
          className="h-7 px-2 text-xs"
          onClick={() => {
            if (!isControlledStyle) setInternalStyle("line");
          }}
        >
          Line
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        {/* Becomes a "Studies" dropdown when a 2nd study is added. */}
        <Button
          type="button"
          size="sm"
          variant={showLevels ? "secondary" : "ghost"}
          className="h-7 px-2 text-xs"
          onClick={() => setInternalShowLevels((on) => !on)}
        >
          Session Nodes
        </Button>
        {timeframe.intraday && (
          <Button
            type="button"
            size="sm"
            variant={extended ? "secondary" : "ghost"}
            className="h-7 px-2 text-xs"
            title="Extended hours (pre/post-market candles)"
            onClick={() => {
              if (extendedProp == null) setInternalExtended((on) => !on);
            }}
          >
            ETH
          </Button>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {session} · ET · Schwab
        </span>
      </div>
      )}

      <div
        className={cn(
          "relative min-h-0 flex-1",
          drawTool && "cursor-crosshair",
        )}
        onContextMenu={handleChartContextMenu}
        onPointerDownCapture={(e) => {
          if (editingIdRef.current == null) return;
          const prim = shapePrimitivesRef.current.get(editingIdRef.current);
          const chart = chartRef.current;
          const container = containerRef.current;
          if (!prim || !chart || !container) return;
          const rect = container.getBoundingClientRect();
          const x = e.clientX - rect.left;
          const y = e.clientY - rect.top;
          const g = shapePixelPoints(prim);
          if (!g) return;
          let idx = -1;
          g.handles.forEach((h, i) => {
            if (idx === -1 && h.x != null && h.y != null && Math.hypot(h.x - x, h.y - y) <= HANDLE_HIT_PX) {
              idx = i;
            }
          });
          if (idx === -1) return;
          e.stopPropagation();
          e.preventDefault();
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          draggingRef.current = { id: editingIdRef.current, handleIdx: idx };
          chart.applyOptions({ handleScroll: false, handleScale: false });
        }}
        onPointerMove={(e) => {
          const drag = draggingRef.current;
          const chart = chartRef.current;
          const series = mainRef.current;
          const container = containerRef.current;
          if (!drag || !chart || !series || !container) return;
          const prim = shapePrimitivesRef.current.get(drag.id);
          if (!prim) return;
          const rect = container.getBoundingClientRect();
          const logical = chart.timeScale().coordinateToLogical(e.clientX - rect.left);
          const price = series.coordinateToPrice(e.clientY - rect.top);
          if (logical == null || price == null) return;
          if (prim.kind === "channel" && drag.handleIdx === 2) {
            prim.width = price - (prim.p1.price + prim.p2.price) / 2;
          } else if (drag.handleIdx === 0) {
            prim.p1 = { logical: logical as number, price };
          } else {
            prim.p2 = { logical: logical as number, price };
          }
          prim.requestUpdate();
        }}
        onPointerUp={(e) => {
          const drag = draggingRef.current;
          draggingRef.current = null;
          const chart = chartRef.current;
          if (chart) {
            chart.applyOptions({
              handleScroll: CHART_HANDLE_SCROLL,
              handleScale: CHART_HANDLE_SCALE,
            });
          }
          try {
            (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
          } catch {
            /* not captured */
          }
          if (!drag) return;
          const prim = shapePrimitivesRef.current.get(drag.id);
          if (!prim) return;
          const bars = barsRef.current;
          const interval = timeframeSeconds(timeframe.value);
          const data: ChartDrawing["data"] = {
            points: [
              { time: logicalToTime(prim.p1.logical, bars, interval), price: prim.p1.price },
              { time: logicalToTime(prim.p2.logical, bars, interval), price: prim.p2.price },
            ],
            color: prim.color,
            ...(prim.kind === "channel" ? { width: prim.width } : {}),
          };
          setShapes((prev) =>
            prev.map((s) => (s.id === drag.id ? { ...s, data: { ...s.data, ...data } } : s)),
          );
          void updateDrawing(drag.id, { data }).catch(() => {});
        }}
      >
        {loading && (
          <Skeleton className="absolute inset-0 z-10 rounded-none" />
        )}
        <div ref={containerRef} className="absolute inset-0" />
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 z-30 flex items-center justify-center transition-opacity duration-200",
            showZoomHint ? "opacity-100" : "opacity-0",
          )}
        >
          <span className="rounded-md border border-border/80 bg-background/95 px-2.5 py-1 text-[11px] text-muted-foreground shadow-md backdrop-blur-sm">
            {IS_MAC ? "⌘" : "Ctrl"} + scroll to zoom
          </span>
        </div>
        {alertMenu && (
          <ChartAlertContextMenu
            menuRef={alertMenuRef}
            x={alertMenu.x}
            y={alertMenu.y}
            price={alertMenu.price}
            level={alertMenu.level ?? null}
            shape={alertMenu.shape ?? null}
            containerRef={containerRef}
            onAddLevel={() => void handleAddLevel()}
            onRemoveLevel={() => void handleRemoveLevel()}
            onRemoveShape={() => void handleRemoveShape()}
            onDrawTrendline={handleDrawTrendline}
            onDrawChannel={handleDrawChannel}
            onSetAlert={handleSetAlertFromMenu}
          />
        )}
        {pendingAlert && (
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
            onCancel={closeAlertPopover}
          />
        )}
        {!compact && (
        <div className="pointer-events-none absolute left-3 right-3 top-3 z-20 space-y-0.5">
          <p className="text-xs font-medium text-foreground/90">{symbol}</p>
          {legend ? (
            <p className="text-[11px] tabular-nums text-muted-foreground">
              {legend}
            </p>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              Hover chart for OHLCV · volume pane below
              {error ? ` · ${error}` : ""}
            </p>
          )}
        </div>
        )}
      </div>
    </div>
  );
}

type ChartAlertContextMenuProps = {
  menuRef: RefObject<HTMLDivElement | null>;
  x: number;
  y: number;
  price: number;
  level?: ChartDrawing | null;
  shape?: ChartDrawing | null;
  containerRef: RefObject<HTMLDivElement | null>;
  onAddLevel: () => void;
  onRemoveLevel: () => void;
  onRemoveShape: () => void;
  onDrawTrendline: () => void;
  onDrawChannel: () => void;
  onSetAlert: () => void;
};

function ChartAlertContextMenu({
  menuRef,
  x,
  y,
  price,
  level,
  shape,
  containerRef,
  onAddLevel,
  onRemoveLevel,
  onRemoveShape,
  onDrawTrendline,
  onDrawChannel,
  onSetAlert,
}: ChartAlertContextMenuProps) {
  const [position, setPosition] = useState({ left: x, top: y });
  const alertPrice = level?.data.price ?? price;

  useEffect(() => {
    const container = containerRef.current;
    const menu = menuRef.current;
    if (!container || !menu) {
      setPosition({ left: x, top: y });
      return;
    }
    const margin = 8;
    const maxLeft = Math.max(
      margin,
      container.clientWidth - menu.offsetWidth - margin,
    );
    const maxTop = Math.max(
      margin,
      container.clientHeight - menu.offsetHeight - margin,
    );
    setPosition({
      left: Math.min(Math.max(margin, x), maxLeft),
      top: Math.min(Math.max(margin, y), maxTop),
    });
  }, [x, y, containerRef, menuRef]);

  const buttonClass =
    "w-full px-2.5 py-1.5 text-left text-[11px] text-foreground transition-colors hover:bg-secondary/80";

  return (
    <div
      ref={menuRef}
      className="workspace-no-drag absolute z-40 min-w-[160px] overflow-hidden rounded-md border border-border/80 bg-background/95 py-0.5 shadow-md backdrop-blur-sm"
      style={{ left: position.left, top: position.top }}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      {level ? (
        <button type="button" className={buttonClass} onClick={onRemoveLevel}>
          Remove level
        </button>
      ) : shape ? (
        <button type="button" className={buttonClass} onClick={onRemoveShape}>
          Remove {shape.kind}
        </button>
      ) : (
        <>
          <button type="button" className={buttonClass} onClick={onAddLevel}>
            Add level at {price.toFixed(2)}
          </button>
          <button type="button" className={buttonClass} onClick={onDrawTrendline}>
            Draw trendline
          </button>
          <button type="button" className={buttonClass} onClick={onDrawChannel}>
            Draw channel
          </button>
        </>
      )}
      <button type="button" className={buttonClass} onClick={onSetAlert}>
        Set alert at {alertPrice.toFixed(2)}
      </button>
    </div>
  );
}
