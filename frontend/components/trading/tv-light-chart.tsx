"use client";

import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  TickMarkType,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

import {
  fetchBars,
  fetchPriceAlerts,
  fetchSessionLevels,
  type PriceAlert,
  type SessionLevel,
  type StockBar,
} from "@/lib/api";
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
  resolveCssColor,
  toChartColor,
  withAlpha,
} from "@/lib/chart-colors";

type Ohlcv = {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

function sessionLevelColor(kind: SessionLevel["kind"]): string {
  switch (kind) {
    case "support":
      return "#3d8f7a";
    case "resistance":
      return "#9f2f2d";
    case "poc":
      return "#c9922a";
    case "prior_high":
    case "prior_low":
      return "#7a7a78";
    default:
      return "#7a7a78";
  }
}

type Props = {
  symbol: string;
  timeframe?: ChartTimeframe;
  style?: "candle" | "line";
  livePrice?: number;
  /** Hide the chart's own toolbar + overlay — the workspace frame provides the
      controls (symbol, timeframe, style, Studies), so the internal one is redundant. */
  compact?: boolean;
  /** Controlled session-node levels toggle (driven by the frame's Studies dropdown). */
  showLevels?: boolean;
};

export function TvLightChart({
  symbol,
  timeframe: timeframeProp,
  style: styleProp,
  livePrice,
  compact = false,
  showLevels: showLevelsProp,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainRef = useRef<ISeriesApi<"Candlestick" | "Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const sessionLevelLinesRef = useRef<IPriceLine[]>([]);
  const barsRef = useRef<Ohlcv[]>([]);
  // True right after the chart is (re)created — i.e. on timeframe/style change.
  // A symbol change reuses the same chart instance, so this stays false and we
  // preserve the user's current view instead of zooming out.
  const freshChartRef = useRef(true);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [internalTimeframe, setInternalTimeframe] = useState<ChartTimeframe>(
    CHART_TIMEFRAMES[4],
  );
  const [internalStyle, setInternalStyle] = useState<"candle" | "line">("candle");
  const timeframe = timeframeProp ?? internalTimeframe;
  const style = styleProp ?? internalStyle;
  const isControlledTimeframe = timeframeProp != null;
  const isControlledStyle = styleProp != null;
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  // Internal toggle for the non-compact (/trading) header; in the workspace the
  // frame's Studies dropdown drives it via the `showLevels` prop (controlled).
  const [internalShowLevels, setInternalShowLevels] = useState(false);
  const showLevels = showLevelsProp ?? internalShowLevels;
  const [sessionLevels, setSessionLevels] = useState<SessionLevel[]>([]);
  const [legend, setLegend] = useState<string>("");
  const [session, setSession] = useState(sessionLabel());

  useEffect(() => {
    const id = setInterval(() => setSession(sessionLabel()), 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const text = resolveCssColor("--foreground", "#c8c6c3");
    const border = toChartColor(
      resolveCssColor("--border", "rgba(255,255,255,0.09)"),
      "rgba(255,255,255,0.09)",
    );
    const accent = resolveCssColor("--chart-accent", "#3d8f7a");
    const down = "#9f2f2d";
    const intraday = !isDailyTimeframe(timeframe.value);

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: text,
        fontFamily: "var(--font-sans), system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: border },
        horzLines: { color: border },
      },
      rightPriceScale: {
        borderColor: border,
        scaleMargins: { top: 0.08, bottom: 0.28 },
      },
      timeScale: {
        borderColor: border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) =>
          formatTickMark(time as number, tickMarkType),
      },
      crosshair: { mode: CrosshairMode.Normal },
      localization: {
        timeFormatter: (time: number) =>
          formatCrosshairTime(time, intraday),
      },
      autoSize: true,
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

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

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

    return () => {
      chart.remove();
      chartRef.current = null;
      mainRef.current = null;
      volumeRef.current = null;
    };
  }, [timeframe.value, style]);

  useEffect(() => {
    fetchPriceAlerts(symbol)
      .then(setAlerts)
      .catch(() => setAlerts([]));
  }, [symbol]);

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

    fetchBars(symbol, { timeframe: timeframe.value, days: timeframe.days })
      .then((data) => {
        if (cancelled) return;
        const accent = resolveCssColor("--chart-accent", "#3d8f7a");
        const down = "#9f2f2d";

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
          const to = count - 1 + RIGHT_MARGIN_FRAC * visibleBars;
          timeScale.setVisibleLogicalRange({ from: to - visibleBars, to });
          // Refit the price axis to the new symbol (a $700→$50 switch rescales
          // vertically even though the time window is unchanged).
          mainRef.current?.priceScale().applyOptions({ autoScale: true });
        }
        freshChartRef.current = false;
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load bars");
        mainRef.current?.setData([]);
        volumeRef.current?.setData([]);
        barsRef.current = [];
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, style]);

  useEffect(() => {
    const series = mainRef.current;
    if (!series) return;
    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    priceLinesRef.current = alerts.map((alert) =>
      series.createPriceLine({
        price: alert.price,
        color: alert.direction === "below" ? "#9f2f2d" : "#3d8f7a",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `Alert ${alert.price.toFixed(2)}`,
      }),
    );
  }, [alerts, loading]);

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
  }, [showLevels, sessionLevels, loading, timeframe.value, style]);

  useEffect(() => {
    if (livePrice == null || loading) return;
    const bars = barsRef.current;
    if (bars.length === 0) return;
    const series = mainRef.current;
    if (!series) return;

    const last = bars[bars.length - 1];

    // Intraday: when the wall clock crosses into the next interval bucket, roll the
    // forming candle over to a NEW bar (the live tick has no timestamp, so bucket off
    // the client clock, aligned to the historical bars' own grid via last.time).
    const intervalSec = timeframe.intraday ? timeframeSeconds(timeframe.value) : 0;
    if (intervalSec > 0 && typeof last.time === "number") {
      const nowSec = Math.floor(Date.now() / 1000);
      const lastTime = last.time as number;
      if (nowSec >= lastTime + intervalSec) {
        const steps = Math.floor((nowSec - lastTime) / intervalSec);
        const newTime = (lastTime + steps * intervalSec) as Time;
        const newBar: Ohlcv = {
          time: newTime,
          open: livePrice,
          high: livePrice,
          low: livePrice,
          close: livePrice,
          volume: 0,
        };
        bars.push(newBar);
        if (style === "line") {
          series.update({ time: newTime, value: livePrice });
        } else {
          series.update(newBar);
        }
        volumeRef.current?.update({ time: newTime, value: 0 });
        return;
      }
    }

    last.close = livePrice;
    last.high = Math.max(last.high, livePrice);
    last.low = Math.min(last.low, livePrice);

    if (style === "line") {
      series.update({ time: last.time, value: livePrice });
    } else {
      series.update(last);
    }
  }, [livePrice, loading, style, timeframe.value, timeframe.intraday]);

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
        <span className="ml-auto text-[11px] text-muted-foreground">
          {session} · ET · Schwab
        </span>
      </div>
      )}

      <div className="relative min-h-0 flex-1">
        {loading && (
          <Skeleton className="absolute inset-0 z-10 rounded-none" />
        )}
        <div ref={containerRef} className="absolute inset-0" />
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
