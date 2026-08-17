"use client";

import { useEffect, useState } from "react";

import { marketWsUrl, type WatchlistQuote } from "@/lib/api";

const RECONNECT_MS = 3_000;

type Options = {
  symbol: string;
  enabled?: boolean;
};

type WsQuoteFrame = {
  type?: string;
  symbol?: string;
  quotes?: Record<string, WatchlistQuote>;
  price?: number;
  live?: boolean;
  ext_price?: number | null;
  ext_change_pct?: number | null;
  market_session?: WatchlistQuote["market_session"] | null;
};

export function useSymbolQuote({ symbol, enabled = true }: Options) {
  const [price, setPrice] = useState<number | undefined>();
  const [live, setLive] = useState(false);
  const [changePct, setChangePct] = useState<number | undefined>();
  const [extPrice, setExtPrice] = useState<number | null | undefined>();
  const [extChangePct, setExtChangePct] = useState<number | null | undefined>();
  const [marketSession, setMarketSession] = useState<
    WatchlistQuote["market_session"] | null | undefined
  >();
  // Which symbol the values above belong to. State outlives a symbol switch,
  // and the new socket needs a round-trip before the first frame lands — in that
  // window the previous symbol's price would otherwise be reported for the new
  // one. The chart merges live prices into the forming candle, so a switch from
  // GLD (~406) to TSLA (~340) painted a 406 wick on every TSLA timeframe
  // (2026-08-17). Values are only returned while this tag matches the request.
  const [quoteSymbol, setQuoteSymbol] = useState<string | undefined>();

  const normalized = symbol.trim().toUpperCase();

  useEffect(() => {
    if (!enabled || !normalized) return;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(marketWsUrl());

      ws.onopen = () => {
        if (cancelled) return;
        setLive(true);
        ws?.send(
          JSON.stringify({ action: "subscribe", symbols: [normalized] }),
        );
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WsQuoteFrame;
          if (msg.type === "quotes" && msg.quotes?.[normalized]) {
            const quote = msg.quotes[normalized];
            setQuoteSymbol(normalized);
            setPrice(quote.price);
            setChangePct(quote.change_pct);
            setExtPrice(quote.ext_price);
            setExtChangePct(quote.ext_change_pct);
            setMarketSession(quote.market_session);
            setLive(Boolean(msg.live));
          }
          if (msg.type === "quote" && msg.symbol === normalized) {
            setQuoteSymbol(normalized);
            if (msg.price != null) setPrice(msg.price);
            if (msg.ext_price !== undefined) setExtPrice(msg.ext_price);
            if (msg.ext_change_pct !== undefined) {
              setExtChangePct(msg.ext_change_pct);
            }
            if (msg.market_session !== undefined) {
              setMarketSession(msg.market_session);
            }
            setLive(true);
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

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [enabled, normalized]);

  if (quoteSymbol !== normalized) {
    return {
      price: undefined,
      live,
      changePct: undefined,
      extPrice: undefined,
      extChangePct: undefined,
      marketSession: undefined,
    };
  }
  return { price, live, changePct, extPrice, extChangePct, marketSession };
}
