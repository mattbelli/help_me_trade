"use client";

import { useEffect, useState } from "react";

import { NativeChart } from "@/components/trading/native-chart";
import { WidgetEmpty } from "@/components/workspace/widget-states";
import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-time";
import { scheduleFrameWork } from "@/lib/frame-scheduler";
import { useSymbolQuote } from "@/lib/use-symbol-quote";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

function resolveTimeframe(value?: string): ChartTimeframe {
  return (
    CHART_TIMEFRAMES.find((tf) => tf.value === value) ?? CHART_TIMEFRAMES[5]
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

  const {
    price: livePrice,
    extPrice,
    marketSession,
    formingBar,
    barEvent,
  } = useSymbolQuote({
    symbol,
    enabled: Boolean(symbol),
  });

  // Defer the (heavy) chart construction until a scheduled frame so a page
  // switch with several charts paints its panels immediately and the charts
  // build progressively, one per frame, instead of blocking the switch.
  const [mountChart, setMountChart] = useState(false);
  useEffect(() => {
    const cancel = scheduleFrameWork(() => setMountChart(true));
    return cancel;
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="relative min-h-0 flex-1">
        {symbol ? (
          mountChart ? (
            // The in-house engine — full parity: candles/volume/pan/zoom/
            // crosshair/live ticks, alerts, drawings, levels, and studies
            // (MA overlays + RSI/MACD panes, CHART_ENGINE.md Phase 3).
            <NativeChart
              symbol={symbol}
              compact
              showLevels={Boolean(config.showSessionNodes)}
              showAlerts={config.showAlerts ?? true}
              showDrawings={config.showDrawings ?? true}
              showTagColumn={config.showTagColumn ?? true}
              studies={config.studies}
              extended={config.extendedHours ?? true}
              timeframe={timeframe}
              style={style}
              livePrice={livePrice}
              extPrice={extPrice}
              marketSession={marketSession}
              formingBar={formingBar}
              barEvent={barEvent}
            />
          ) : (
            <div className="h-full w-full" aria-hidden />
          )
        ) : (
          <WidgetEmpty message="Enter a symbol" />
        )}
      </div>
    </div>
  );
}
