"use client";

import { useEffect, useRef, useState } from "react";

import { marketWsUrl, type WatchlistQuote } from "@/lib/api";
import type { BarFrame } from "@/lib/chart-engine/merge";

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
  change_pct?: number;
  live?: boolean;
  ext_price?: number | null;
  ext_change_pct?: number | null;
  market_session?: WatchlistQuote["market_session"] | null;
  /** Forming minute from the server's bar authority (rides every quote frame). */
  bar?: BarFrame;
};

/**
 * One `bar` frame with a monotonic sequence number, so consumers can apply
 * every frame in order from an effect (a plain "latest bar" state would
 * collapse two frames that land in one render — a sealed minute followed by
 * the vendor's copy, for instance).
 */
export type BarEvent = { seq: number; bar: BarFrame };

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
  // Live bars (card #325): the forming minute off the quote frame, and the
  // sealed / vendor-final / daily bars as an ordered event stream.
  const [formingBar, setFormingBar] = useState<BarFrame | undefined>();
  const [barEvent, setBarEvent] = useState<BarEvent | undefined>();
  const seqRef = useRef(0);

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
            const snapBar = (quote as WatchlistQuote & { bar?: BarFrame }).bar;
            if (snapBar) setFormingBar(snapBar);
            setLive(Boolean(msg.live));
          }
          if (msg.type === "bar" && msg.symbol === normalized) {
            const bar = msg as unknown as BarFrame;
            if (typeof bar.time === "number") {
              seqRef.current += 1;
              setBarEvent({ seq: seqRef.current, bar });
            }
          }
          if (msg.type === "quote" && msg.symbol === normalized) {
            setQuoteSymbol(normalized);
            if (msg.bar) setFormingBar(msg.bar);
            if (msg.price != null) setPrice(msg.price);
            if (msg.change_pct != null) setChangePct(msg.change_pct);
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
      formingBar: undefined,
      barEvent: undefined,
    };
  }
  return {
    price,
    live,
    changePct,
    extPrice,
    extChangePct,
    marketSession,
    formingBar: formingBar?.symbol === normalized ? formingBar : undefined,
    barEvent: barEvent?.bar.symbol === normalized ? barEvent : undefined,
  };
}
