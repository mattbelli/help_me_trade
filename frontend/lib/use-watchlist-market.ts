"use client";

import { useEffect, useState } from "react";

import {
  fetchWatchlistMarket,
  marketWsUrl,
  type WatchlistQuote,
} from "@/lib/api";

// Full REST refresh cadence. The WS carries live prices (incl. extended/overnight ext_*
// fields), so this is the floor for when no broadcasts are flowing (e.g. web is the only
// client overnight). 5 min was far too long — the displayed ext_price went stale until a
// manual reload. 20 s steady polling matches the phone and is well under any burst limit.
const MARKET_REFRESH_MS = 20_000;
const RECONNECT_MS = 3_000;

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
          ...prev[symbol],
          price: patch.price ?? prev[symbol]?.price ?? 0,
          change_pct: patch.change_pct ?? prev[symbol]?.change_pct ?? 0,
          sparkline: patch.sparkline ?? prev[symbol]?.sparkline ?? [],
          // Carry the extended/overnight fields through — the displayed price uses these
          // when the market_session is pre/post/overnight. Dropping them was why the web
          // froze at the last REST refresh while the phone (which polls fast) stayed live.
          ext_price: patch.ext_price ?? prev[symbol]?.ext_price ?? null,
          ext_change_pct: patch.ext_change_pct ?? prev[symbol]?.ext_change_pct ?? null,
          market_session: patch.market_session ?? prev[symbol]?.market_session,
        },
      }));
    }

    function mergeQuotes(next: Record<string, WatchlistQuote>) {
      setQuotes((prev) => ({ ...prev, ...next }));
    }

    async function refreshMarket() {
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
            ext_price?: number | null;
            ext_change_pct?: number | null;
            market_session?: WatchlistQuote["market_session"];
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
              ext_price: msg.ext_price,
              ext_change_pct: msg.ext_change_pct,
              market_session: msg.market_session,
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
    refreshMarket();
    connect();
    sparkTimer = setInterval(refreshMarket, MARKET_REFRESH_MS);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (sparkTimer) clearInterval(sparkTimer);
      ws?.close();
    };
  }, [enabled, key]);

  return { quotes, live, loading };
}
