"use client";

import { useEffect, useState } from "react";

import {
  FactsSection,
  FundamentalsSection,
  HeaderSection,
  NewsSection,
} from "@/components/workspace/company-info-sections";
import {
  fetchTickerInfo,
  fetchWatchlistNews,
  type TickerInfo,
  type WatchlistNewsItem,
} from "@/lib/ticker-info-api";
import { cn } from "@/lib/utils";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

type Mode = "symbol" | "watchlist-news";

const modeButtonClass = (active: boolean) =>
  cn(
    "rounded px-2 py-0.5 font-mono text-[10px] transition-colors",
    active
      ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
      : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
  );

export function CompanyInfoWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const symbol = channel !== "none" ? channelSymbol : config.symbol;
  const { publish, linked } = usePublishSymbol(channel);

  const [mode, setMode] = useState<Mode>("symbol");

  // Symbol mode state.
  const [data, setData] = useState<TickerInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "symbol" || !symbol) return;
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
  }, [mode, symbol]);

  // Watchlist-news mode state — fetched lazily, once, the first time the mode is opened.
  const [newsLoaded, setNewsLoaded] = useState(false);
  const [newsItems, setNewsItems] = useState<WatchlistNewsItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "watchlist-news" || newsLoaded) return;
    let cancelled = false;
    setNewsError(null);
    setNewsLoading(true);
    fetchWatchlistNews()
      .then((res) => {
        if (!cancelled) {
          setNewsItems(res);
          setNewsLoaded(true);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setNewsError(err instanceof Error ? err.message : "Failed to load watchlist news");
        }
      })
      .finally(() => {
        if (!cancelled) setNewsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, newsLoaded]);

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-1.5">
        <span className="truncate text-[11px] text-[var(--fg-dim)]">
          {mode === "symbol" ? (symbol ?? "Company info") : "All watchlists"}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMode("symbol")}
            className={modeButtonClass(mode === "symbol")}
          >
            Symbol
          </button>
          <button
            type="button"
            onClick={() => setMode("watchlist-news")}
            className={modeButtonClass(mode === "watchlist-news")}
          >
            Watchlist news
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {mode === "symbol" ? (
          !symbol ? (
            <p className="text-[11px] text-[var(--fg-dim)]">
              {channel !== "none"
                ? "Click a ticker to load its company info"
                : "Link a channel or pick a symbol"}
            </p>
          ) : loading ? (
            <p className="text-[11px] text-[var(--fg-dim)]">Loading…</p>
          ) : error ? (
            <p className="text-[11px] text-[var(--c-red)]">{error}</p>
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
              <FundamentalsSection profile={data.profile} metrics={data.metrics} />
              <FactsSection facts={data.facts} />
              <NewsSection news={data.news} />
            </div>
          ) : null
        ) : newsLoading && newsItems.length === 0 ? (
          <p className="text-[11px] text-[var(--fg-dim)]">Loading…</p>
        ) : newsError ? (
          <p className="text-[11px] text-[var(--c-red)]">{newsError}</p>
        ) : (
          <NewsSection news={newsItems} title="Watchlist news" showSymbol />
        )}
      </div>
    </div>
  );
}
