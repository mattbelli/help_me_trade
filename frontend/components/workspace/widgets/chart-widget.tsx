"use client";

import { Maximize2 } from "lucide-react";
import { useState } from "react";

import { TvLightChart } from "@/components/trading/tv-light-chart";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  const [maximized, setMaximized] = useState(false);

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
      <div className="relative min-h-0 flex-1">
        {symbol ? (
          <>
            <button
              type="button"
              onClick={() => setMaximized(true)}
              aria-label="Expand chart"
              title="Expand chart"
              className="absolute left-2 top-2 z-30 rounded border border-border/60 bg-background/70 p-1 text-muted-foreground backdrop-blur transition-colors hover:text-foreground"
            >
              <Maximize2 className="size-3.5" />
            </button>
            <TvLightChart
              symbol={symbol}
              compact
              timeframe={timeframe}
              style={style}
              livePrice={livePrice}
            />
          </>
        ) : (
          <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
            Enter a symbol
          </p>
        )}
      </div>

      {/* Full, fully-interactive chart (timeframe / style / Levels toggle / legend)
          opened from the workspace so the founder never has to leave the grid. */}
      <Dialog open={maximized} onOpenChange={setMaximized}>
        <DialogContent className="h-[90vh] w-[95vw] max-w-[95vw] gap-0 p-0">
          <DialogHeader className="sr-only">
            <DialogTitle>{symbol} chart</DialogTitle>
          </DialogHeader>
          <div className="h-full w-full p-2">
            {symbol ? (
              <TvLightChart symbol={symbol} livePrice={livePrice} />
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
