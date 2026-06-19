"use client";

import { TvLightChart } from "@/components/trading/tv-light-chart";
import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { useSymbolQuote } from "@/lib/use-symbol-quote";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

function resolveTimeframe(value?: string): ChartTimeframe {
  return (
    CHART_TIMEFRAMES.find((tf) => tf.value === value) ?? CHART_TIMEFRAMES[4]
  );
}

export function ChartWidget({ config }: WidgetProps) {
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

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
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
