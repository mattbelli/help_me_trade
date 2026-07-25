"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchPriceAlerts,
  marketWsUrl,
  triggerPriceAlert,
  type PriceAlert,
} from "@/lib/api";

export type TriggeredAlert = PriceAlert & { trigger_price?: number };

type Options = {
  enabled?: boolean;
  onTriggered?: (alert: TriggeredAlert) => void;
};

const RECONNECT_MS = 3_000;

function crossed(
  prev: number,
  current: number,
  target: number,
  direction: string,
): boolean {
  if (direction === "above") return prev < target && current >= target;
  if (direction === "below") return prev > target && current <= target;
  return (
    (prev < target && current >= target) || (prev > target && current <= target)
  );
}

function directionLabel(direction: string) {
  if (direction === "above") return "rose above";
  if (direction === "below") return "fell below";
  return "crossed";
}

export function usePriceAlertMonitor({
  enabled = true,
  onTriggered,
}: Options = {}) {
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const alertsRef = useRef<PriceAlert[]>([]);
  const prevPrices = useRef<Record<string, number>>({});
  const onTriggeredRef = useRef(onTriggered);
  onTriggeredRef.current = onTriggered;

  const loadAlerts = useCallback(async () => {
    try {
      const list = await fetchPriceAlerts();
      alertsRef.current = list;
      setAlerts(list);
    } catch {
      alertsRef.current = [];
      setAlerts([]);
    }
  }, []);

  // NOTE: there is deliberately no second load loop here. This hook used to run
  // one, duplicating the 30s refresh in the socket effect below — two fetches of
  // the same list in the same second, every 30s, which is what showed up in the
  // backend log as the /api/price-alerts cadence. That effect already does the
  // initial load (before connecting) and the periodic refresh, and it has to own
  // the refresh anyway because it re-subscribes the socket to the new symbols.
  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function handleTrigger(alert: PriceAlert, current: number) {
      const payload: TriggeredAlert = { ...alert, trigger_price: current };
      onTriggeredRef.current?.(payload);

      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(`${alert.symbol} price alert`, {
          body: `${directionLabel(alert.direction)} ${alert.price.toFixed(2)} (now ${current.toFixed(2)})`,
        });
      }

      triggerPriceAlert(alert.id).catch(() => {});
      alertsRef.current = alertsRef.current.filter((a) => a.id !== alert.id);
      setAlerts([...alertsRef.current]);
    }

    function onPrice(symbol: string, price: number) {
      const prev = prevPrices.current[symbol];
      if (prev === undefined) {
        prevPrices.current[symbol] = price;
        return;
      }
      if (prev === price) return;

      for (const alert of alertsRef.current) {
        if (alert.symbol !== symbol) continue;
        if (crossed(prev, price, alert.price, alert.direction)) {
          handleTrigger(alert, price);
        }
      }
      prevPrices.current[symbol] = price;
    }

    function connect() {
      if (cancelled) return;
      const symbols = [...new Set(alertsRef.current.map((a) => a.symbol))];
      if (symbols.length === 0) return;

      ws = new WebSocket(marketWsUrl());

      ws.onopen = () => {
        if (cancelled) return;
        ws?.send(JSON.stringify({ action: "subscribe", symbols }));
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as {
            type?: string;
            symbol?: string;
            price?: number;
            quotes?: Record<string, { price: number }>;
          };
          if (msg.type === "quotes" && msg.quotes) {
            for (const [sym, quote] of Object.entries(msg.quotes)) {
              onPrice(sym, quote.price);
            }
          }
          if (msg.type === "quote" && msg.symbol && msg.price != null) {
            onPrice(msg.symbol, msg.price);
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

    loadAlerts().then(() => {
      if (!cancelled) connect();
    });

    const refresh = setInterval(() => {
      loadAlerts().then(() => {
        if (cancelled || !ws || ws.readyState !== WebSocket.OPEN) return;
        const symbols = [...new Set(alertsRef.current.map((a) => a.symbol))];
        if (symbols.length) {
          ws.send(JSON.stringify({ action: "subscribe", symbols }));
        }
      });
    }, 30_000);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(refresh);
      ws?.close();
    };
  }, [enabled, loadAlerts]);

  return { alerts, reload: loadAlerts };
}

export async function requestAlertNotifications() {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
}
