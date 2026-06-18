"use client";

import { useMemo } from "react";

import { TvLightChart } from "@/components/trading/tv-light-chart";
import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { useSymbolQuote } from "@/lib/use-symbol-quote";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

function resolveTimeframe(value?: string): ChartTimeframe {
  return (
    CHART_TIMEFRAMES.find((tf) => tf.value === value) ?? CHART_TIMEFRAMES[4]
  );
}

export function ChartWidget({ instanceId, config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const timeframe = resolveTimeframe(config.timeframe);
  const style = config.style ?? "candle";

  const symbol =
    channel !== "none"
      ? (channelSymbol ?? config.symbol ?? "SPY")
      : (config.symbol ?? "SPY");

  const { price: livePrice } = useSymbolQuote({
    symbol,
    enabled: Boolean(symbol),
  });

  const manualMode = channel === "none";

  const timeframeButtons = useMemo(
    () =>
      CHART_TIMEFRAMES.map((tf) => (
        <button
          key={tf.label}
          type="button"
          onClick={() => onConfigChange({ timeframe: tf.value })}
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors",
            timeframe.value === tf.value
              ? "bg-[var(--bg-hover)] text-foreground"
              : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
          )}
        >
          {tf.label}
        </button>
      )),
    [onConfigChange, timeframe.value],
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-1.5">
        {manualMode ? (
          <>
            <label
              htmlFor={`workspace-chart-symbol-${instanceId}`}
              className="font-mono text-[10px] text-[var(--fg-faint)] uppercase"
            >
              Symbol
            </label>
            <input
              id={`workspace-chart-symbol-${instanceId}`}
              type="text"
              value={config.symbol ?? "SPY"}
              onChange={(e) =>
                onConfigChange({
                  symbol: e.target.value.toUpperCase().trim() || "SPY",
                })
              }
              className="w-20 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-0.5 font-mono text-[11px] text-foreground uppercase outline-none focus:border-[var(--c-accent)]"
            />
          </>
        ) : (
          <span className="font-mono text-[11px] font-medium text-[var(--c-accent)]">
            {symbol}
          </span>
        )}
        <span className="h-3 w-px bg-[var(--border)]" />
        <div className="flex flex-wrap items-center gap-0.5">{timeframeButtons}</div>
        <span className="h-3 w-px bg-[var(--border)]" />
        <button
          type="button"
          onClick={() => onConfigChange({ style: "candle" })}
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors",
            style === "candle"
              ? "bg-[var(--bg-hover)] text-foreground"
              : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]",
          )}
        >
          Candles
        </button>
        <button
          type="button"
          onClick={() => onConfigChange({ style: "line" })}
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors",
            style === "line"
              ? "bg-[var(--bg-hover)] text-foreground"
              : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]",
          )}
        >
          Line
        </button>
      </div>
      <div className="min-h-0 flex-1">
        {symbol ? (
          <TvLightChart
            symbol={symbol}
            compact
            timeframe={timeframe}
            style={style}
            livePrice={livePrice}
          />
        ) : (
          <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
            Enter a symbol
          </p>
        )}
      </div>
    </div>
  );
}
