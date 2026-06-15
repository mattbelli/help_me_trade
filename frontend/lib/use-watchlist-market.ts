"use client";

import { useEffect, useState } from "react";

import {
  API_BASE,
  fetchWatchlistMarket,
  type WatchlistQuote,
} from "@/lib/api";

const SPARKLINE_REFRESH_MS = 5 * 60_000;
const RECONNECT_MS = 3_000;

function marketWsUrl() {
  return `${API_BASE.replace(/^http/, "ws")}/ws/market`;
}

function symbolsFromKey(key: string): string[] {
  if (!key) return [];
  return key.split(",").filter(Boolean);
}

type Options = {
  symbols: string[];
  enabled?: boolean;
};

export function useWatchlistMarket({ symbols, enabled = true }: Options) {
  const [quotes, setQuotes] = useState<Record<string, WatchlistQuote>>({});
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);

  const key = symbols.join(",");

  useEffect(() => {
    const symbolList = symbolsFromKey(key);
    const active = enabled && symbolList.length > 0;

    if (!active) {
      setQuotes((prev) => (Object.keys(prev).length === 0 ? prev : {}));
      setLive((prev) => (prev ? false : prev));
      setLoading((prev) => (prev ? false : prev));
      return;
    }

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let sparkTimer: ReturnType<typeof setInterval> | null = null;

    function mergeQuote(symbol: string, patch: Partial<WatchlistQuote>) {
      setQuotes((prev) => ({
        ...prev,
        [symbol]: {
          price: patch.price ?? prev[symbol]?.price ?? 0,
          change_pct: patch.change_pct ?? prev[symbol]?.change_pct ?? 0,
          sparkline: patch.sparkline ?? prev[symbol]?.sparkline ?? [],
        },
      }));
    }

    function mergeQuotes(next: Record<string, WatchlistQuote>) {
      setQuotes((prev) => ({ ...prev, ...next }));
    }

    async function loadSparklines() {
      try {
        const data = await fetchWatchlistMarket(symbolList);
        if (!cancelled) mergeQuotes(data);
      } catch {
        // Keep streaming prices if REST refresh fails.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(marketWsUrl());

      ws.onopen = () => {
        if (cancelled) return;
        setLive(true);
        ws?.send(
          JSON.stringify({ action: "subscribe", symbols: symbolList }),
        );
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as {
            type?: string;
            symbol?: string;
            quotes?: Record<string, WatchlistQuote>;
            price?: number;
            change_pct?: number;
            sparkline?: number[];
            live?: boolean;
          };
          if (msg.type === "quotes" && msg.quotes) {
            mergeQuotes(msg.quotes);
            setLive(Boolean(msg.live));
            setLoading(false);
          }
          if (msg.type === "quote" && msg.symbol) {
            mergeQuote(msg.symbol, {
              price: msg.price,
              change_pct: msg.change_pct,
              sparkline: msg.sparkline,
            });
            setLive(true);
            setLoading(false);
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setLive(false);
        reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    setLoading(true);
    loadSparklines();
    connect();
    sparkTimer = setInterval(loadSparklines, SPARKLINE_REFRESH_MS);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (sparkTimer) clearInterval(sparkTimer);
      ws?.close();
    };
  }, [enabled, key]);

  return { quotes, live, loading };
}
