"use client";

import { useEffect, useState } from "react";

import { marketWsUrl, type WatchlistQuote } from "@/lib/api";
import type { BarFrame } from "@/lib/chart-engine/merge";

const RECONNECT_MS = 3_000;

/**
 * NBBO quote hook for the manual-trading surfaces (ticket + DOM ladder).
 *
 * Mirrors `useSymbolQuote`'s own-socket pattern, but additionally sends the
 * `quotes` watch action so the backend subscribes the upstream NBBO channel
 * for this one symbol. Sizes are SHARES. Values are tagged by symbol so a
 * switch never reports the previous symbol's book.
 */

export type NbboQuote = {
  live: boolean;
  price?: number;
  changePct?: number;
  bid?: number;
  ask?: number;
  bidSize?: number;
  askSize?: number;
  quoteTs?: number | null;
  volume?: number;
  marketSession?: WatchlistQuote["market_session"] | null;
  formingBar?: BarFrame;
};

type WsFrame = {
  type?: string;
  symbol?: string;
  live?: boolean;
  quotes?: Record<string, WatchlistQuote>;
  price?: number;
  change_pct?: number;
  bid?: number;
  ask?: number;
  bid_size?: number;
  ask_size?: number;
  quote_ts?: number | null;
  volume?: number;
  market_session?: WatchlistQuote["market_session"] | null;
  bar?: BarFrame;
};

export function useNbboQuote(symbol: string, enabled = true): NbboQuote {
  const [state, setState] = useState<NbboQuote>({ live: false });

  const normalized = symbol.trim().toUpperCase();

  useEffect(() => {
    if (!enabled || !normalized) {
      setState({ live: false });
      return;
    }

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const applyQuote = (q: WsFrame | WatchlistQuote, live: boolean) => {
      setState((prev) => ({
        ...prev,
        live,
        price: q.price ?? prev.price,
        changePct: q.change_pct ?? prev.changePct,
        bid: q.bid ?? prev.bid,
        ask: q.ask ?? prev.ask,
        bidSize: q.bid_size ?? prev.bidSize,
        askSize: q.ask_size ?? prev.askSize,
        quoteTs: q.quote_ts ?? prev.quoteTs,
        volume: q.volume ?? prev.volume,
        marketSession:
          q.market_session !== undefined ? q.market_session : prev.marketSession,
        formingBar: (q as WsFrame).bar ?? prev.formingBar,
      }));
    };

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(marketWsUrl());

      ws.onopen = () => {
        if (cancelled) return;
        ws?.send(JSON.stringify({ action: "subscribe", symbols: [normalized] }));
        ws?.send(JSON.stringify({ action: "quotes", symbols: [normalized] }));
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WsFrame;
          if (msg.type === "quotes" && msg.quotes?.[normalized]) {
            applyQuote(msg.quotes[normalized], Boolean(msg.live));
          } else if (msg.type === "quote" && msg.symbol === normalized) {
            applyQuote(msg, true);
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        setState((prev) => ({ ...prev, live: false }));
        reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    setState({ live: false });
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [enabled, normalized]);

  // The effect resets state on every symbol switch, so `state` never carries
  // a previous symbol's book (the setState({live:false}) before connect()).
  return state;
}
