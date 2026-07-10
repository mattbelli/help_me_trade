"use client";

import { useEffect, useRef, useState } from "react";

import type { StockBar } from "@/lib/api";
import { fetchBarsCached, invalidateBars } from "@/lib/bars-cache";
import { resolveCssColor, withAlpha } from "@/lib/chart-colors";
import { ChartEngine } from "@/lib/chart-engine/engine";
import { crosshairTimeLabel } from "@/lib/chart-engine/ticks";
import type { EngineBar, EngineColors } from "@/lib/chart-engine/types";
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
};

// Rollover/refetch cadence: the bars cache TTL is the real gate — this just
// polls it (a cache hit is free), so forming-bar rollover lags TTL, not this.
const INTRADAY_REFRESH_MS = 30_000;
const DAILY_REFRESH_MS = 300_000;

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

  useEffect(() => {
    styleRef.current = style;
  }, [style]);

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
