"use client";

import { useEffect, useState } from "react";

import { marketWsUrl, type WatchlistQuote } from "@/lib/api";

const RECONNECT_MS = 3_000;

type Options = {
  symbol: string;
  enabled?: boolean;
};

export function useSymbolQuote({ symbol, enabled = true }: Options) {
  const [price, setPrice] = useState<number | undefined>();
  const [live, setLive] = useState(false);
  const [changePct, setChangePct] = useState<number | undefined>();

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
          const msg = JSON.parse(event.data) as {
            type?: string;
            symbol?: string;
            quotes?: Record<string, WatchlistQuote>;
            price?: number;
            live?: boolean;
          };
          if (msg.type === "quotes" && msg.quotes?.[normalized]) {
            setPrice(msg.quotes[normalized].price);
            setChangePct(msg.quotes[normalized].change_pct);
            setLive(Boolean(msg.live));
          }
          if (msg.type === "quote" && msg.symbol === normalized) {
            if (msg.price != null) setPrice(msg.price);
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

  return { price, live, changePct };
}
