"use client";

import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

import {
  fetchBars,
  fetchPriceAlerts,
  type PriceAlert,
  type StockBar,
} from "@/lib/api";
import {
  CHART_TIMEFRAMES,
  formatCrosshairTime,
  formatTickMark,
  formatVolume,
  isDailyTimeframe,
  sessionLabel,
  toChartTime,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type Ohlcv = {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

import {
  resolveCssColor,
  toChartColor,
  withAlpha,
} from "@/lib/chart-colors";

type Props = {
  symbol: string;
  compact?: boolean;
};

export function TvLightChart({ symbol, compact = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainRef = useRef<ISeriesApi<"Candlestick" | "Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const barsRef = useRef<Ohlcv[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<ChartTimeframe>(
    CHART_TIMEFRAMES[4],
  );
  const [style, setStyle] = useState<"candle" | "line">("candle");
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
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
        tickMarkFormatter: (time: Time) =>
          formatTickMark(time as number, intraday),
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
    let cancelled = false;
    setLoading(true);
    setError(null);

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

        chartRef.current?.timeScale().fitContent();
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

  return (
    <div
      className={`relative flex h-full w-full flex-col ${compact ? "min-h-0" : "min-h-[460px]"}`}
    >
      {!compact && (
        <div className="flex flex-wrap items-center gap-1 border-b border-border/80 px-2 py-1.5">
          {CHART_TIMEFRAMES.map((tf) => (
            <Button
              key={tf.label}
              type="button"
              size="sm"
              variant={timeframe.label === tf.label ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setTimeframe(tf)}
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
            onClick={() => setStyle("candle")}
          >
            Candles
          </Button>
          <Button
            type="button"
            size="sm"
            variant={style === "line" ? "secondary" : "ghost"}
            className="h-7 px-2 text-xs"
            onClick={() => setStyle("line")}
          >
            Line
          </Button>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {session} · ET · IEX
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
        {compact && error && (
          <p className="pointer-events-none absolute bottom-1 left-2 z-20 text-[10px] text-destructive">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
