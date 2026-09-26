"use client";

import { useEffect, useState } from "react";

import { marketWsUrl, type WatchlistQuote } from "@/lib/api";

const RECONNECT_MS = 3_000;
// Positions are a P&L view, not a tape — a 1s refresh is the founder's call.
const FLUSH_MS = 1_000;

// Subscribes the given symbols to the shared market websocket and returns the
// latest REGULAR-SESSION trade price per symbol. This mirrors the WS client in
// use-watchlist-market.ts but strips it to a single number per symbol: no REST,
// no ext/session state. Outside RTH the backend freezes `price` and routes live
// ticks to ext_price (b41731b), so consuming `price` only means marks stop moving
// after the close exactly like the broker's own numbers.
export function useLiveMarks(symbols: string[]): Record<string, number> {
  const [marks, setMarks] = useState<Record<string, number>>({});

  const key = symbols.join(",");

  useEffect(() => {
    const symbolList = key ? key.split(",").filter(Boolean) : [];

    // Empty set → no socket. Any marks left from a prior set are harmless:
    // markPaper/markReal only read marks[symbol] for currently-rendered rows,
    // and each REST poll re-baselines, so a stale entry can never walk.
    if (symbolList.length === 0) return;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    // Ticks arrive hundreds per second across a big book; one setState per
    // tick re-rendered the whole positions table each time and saturated the
    // main thread (prices froze, then lurched). Buffer and flush once a second.
    let pending: Record<string, number> = {};
    let flushTimer: ReturnType<typeof setTimeout> | null = null;

    function flush() {
      flushTimer = null;
      const batch = pending;
      pending = {};
      setMarks((prev) => {
        let next = prev;
        for (const [symbol, price] of Object.entries(batch)) {
          if (next[symbol] === price) continue;
          if (next === prev) next = { ...prev };
          next[symbol] = price;
        }
        return next;
      });
    }

    function setMark(symbol: string, price: number | null | undefined) {
      if (typeof price !== "number" || !Number.isFinite(price)) return;
      pending[symbol] = price;
      flushTimer ??= setTimeout(flush, FLUSH_MS);
    }

    function connect() {
      if (cancelled) return;
      ws = new WebSocket(marketWsUrl());

      ws.onopen = () => {
        if (cancelled) return;
        ws?.send(JSON.stringify({ action: "subscribe", symbols: symbolList }));
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as {
            type?: string;
            symbol?: string;
            price?: number;
            quotes?: Record<string, WatchlistQuote>;
          };
          // Regular-session `price` only — ext fields are ignored on purpose.
          if (msg.type === "quotes" && msg.quotes) {
            for (const [symbol, q] of Object.entries(msg.quotes)) {
              setMark(symbol, q.price);
            }
          }
          if (msg.type === "quote" && msg.symbol) {
            setMark(msg.symbol, msg.price);
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
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
      if (flushTimer) clearTimeout(flushTimer);
      ws?.close();
    };
  }, [key]);

  return marks;
}
