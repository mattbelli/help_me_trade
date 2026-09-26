"use client";

import { useEffect, useRef, useState } from "react";

import {
  fetchWatchlistMarket,
  marketWsUrl,
  type WatchlistQuote,
} from "@/lib/api";
import { usePageActive } from "@/lib/workspace/page-active-context";

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
  // The socket stays connected on a hidden page (it is push, and reconnecting on
  // every page switch costs more than it saves) — only the REST floor pauses.
  const pageActive = usePageActive();
  const pageActiveRef = useRef(pageActive);
  pageActiveRef.current = pageActive;

  const [quotes, setQuotes] = useState<Record<string, WatchlistQuote>>({});
  const [spyChangePct, setSpyChangePct] = useState<number | null>(null);
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

    // One setState per tick re-rendered every watchlist row hundreds of times
    // a second and starved the main thread; coalesce ticks to one flush per frame.
    let pending: Record<string, Partial<WatchlistQuote>> = {};
    let flushFrame: number | null = null;

    function queueQuote(symbol: string, patch: Partial<WatchlistQuote>) {
      const merged: Record<string, unknown> = { ...pending[symbol] };
      for (const [k, v] of Object.entries(patch)) {
        if (v != null) merged[k] = v;
      }
      pending[symbol] = merged as Partial<WatchlistQuote>;
      flushFrame ??= requestAnimationFrame(flushQuotes);
    }

    function flushQuotes() {
      flushFrame = null;
      const batch = pending;
      pending = {};
      setQuotes((prev) => {
        const out = { ...prev };
        for (const [symbol, patch] of Object.entries(batch)) {
          out[symbol] = mergedQuote(prev, symbol, patch);
        }
        return out;
      });
    }

    function mergedQuote(
      prev: Record<string, WatchlistQuote>,
      symbol: string,
      patch: Partial<WatchlistQuote>,
    ): WatchlistQuote {
      return {
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
        // REST polls don't carry bid/ask, so the last WS values must survive
        // the merge — carry them forward when a frame omits them.
        bid: patch.bid ?? prev[symbol]?.bid ?? null,
        ask: patch.ask ?? prev[symbol]?.ask ?? null,
      };
    }

    function mergeQuotes(next: Record<string, WatchlistQuote>) {
      // REST payloads never carry bid/ask (WS-only fields) — a plain spread
      // would wipe them on every 20s poll, blanking the Bid/Ask/Sprd columns.
      setQuotes((prev) => {
        const out = { ...prev };
        for (const [sym, q] of Object.entries(next)) {
          out[sym] = {
            ...q,
            bid: q.bid ?? prev[sym]?.bid ?? null,
            ask: q.ask ?? prev[sym]?.ask ?? null,
          };
        }
        return out;
      });
    }

    async function refreshMarket() {
      try {
        const data = await fetchWatchlistMarket(symbolList);
        if (!cancelled) {
          mergeQuotes(data.quotes);
          setSpyChangePct(data.spyChangePct);
        }
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
            bid?: number | null;
            ask?: number | null;
            live?: boolean;
          };
          if (msg.type === "quotes" && msg.quotes) {
            mergeQuotes(msg.quotes);
            setLive(Boolean(msg.live));
            setLoading(false);
          }
          if (msg.type === "quote" && msg.symbol) {
            queueQuote(msg.symbol, {
              price: msg.price,
              change_pct: msg.change_pct,
              sparkline: msg.sparkline,
              ext_price: msg.ext_price,
              ext_change_pct: msg.ext_change_pct,
              market_session: msg.market_session,
              bid: msg.bid,
              ask: msg.ask,
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
    sparkTimer = setInterval(() => {
      if (!pageActiveRef.current) return;
      refreshMarket();
    }, MARKET_REFRESH_MS);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (sparkTimer) clearInterval(sparkTimer);
      if (flushFrame != null) cancelAnimationFrame(flushFrame);
      ws?.close();
    };
  }, [enabled, key]);

  return { quotes, spyChangePct, live, loading };
}
