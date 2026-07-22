"use client";

import { useEffect, useState } from "react";

import {
  FactsSection,
  FundamentalsSection,
  HeaderSection,
  NewsSection,
} from "@/components/workspace/company-info-sections";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { fetchTickerInfo, type TickerInfo } from "@/lib/ticker-info-api";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

export function CompanyInfoWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const symbol = channel !== "none" ? channelSymbol : config.symbol;
  const { publish, linked } = usePublishSymbol(channel);

  const [data, setData] = useState<TickerInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);
    fetchTickerInfo(symbol)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load company info");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono">
      <div className="min-h-0 flex-1 overflow-auto p-3 scroll-shadow-y">
        {!symbol ? (
          <WidgetEmpty
            message={
              channel !== "none"
                ? "Click a ticker to load its company info"
                : "Link a channel or pick a symbol"
            }
            className="p-0"
          />
        ) : loading ? (
          <WidgetLoading rows={4} cols={[1]} className="p-0" />
        ) : error ? (
          <WidgetError message={error} className="p-0" />
        ) : data ? (
          <div className="flex flex-col gap-4">
            <HeaderSection
              symbol={data.symbol}
              profile={data.profile}
              metrics={data.metrics}
              facts={data.facts}
              linked={linked}
              onPublish={() => publish(data.symbol)}
            />
            <FundamentalsSection
              profile={data.profile}
              metrics={data.metrics}
              shortInterest={data.short_interest}
              price={data.facts.quote?.price}
            />
            <FactsSection facts={data.facts} />
            <NewsSection news={data.news} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
