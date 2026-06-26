"use client";

import { useEffect } from "react";

import { marketWsUrl } from "@/lib/api";
import {
  useNotificationStore,
  type WorkspaceNotification,
} from "@/lib/workspace/use-notification-store";

const RECONNECT_MS = 3_000;

type ExecutionMessage = {
  type?: string;
  id?: string;
  ts?: string;
  execution_id?: string;
  status?: string;
  symbol?: string;
  side?: string;
  qty?: string | number;
  execution_mode?: string;
  strategy_id?: string;
  broker_order_id?: string;
};

function executionToNotification(msg: ExecutionMessage): WorkspaceNotification | null {
  const status = msg.status ?? "";
  const executed = ["submitted", "filled", "dry_run", "partially_filled"].includes(status);
  const rejected = ["rejected", "error"].includes(status);
  if (!executed && !rejected) return null;

  const strat = msg.strategy_id;
  const verb = executed ? "Trade executed" : "Trade rejected";
  return {
    id: `exec-${msg.execution_id}`,
    source: "execution",
    ruleName: strat ?? "Manual trade",
    message:
      `${verb} — ${String(msg.side).toUpperCase()} ${msg.qty} ${msg.symbol}` +
      (strat ? ` · ${strat}` : "") +
      (msg.execution_mode ? ` [${msg.execution_mode}]` : ""),
    ts: msg.ts ?? new Date().toISOString(),
    read: false,
  };
}

export function useExecutionMonitor({ enabled = true }: { enabled?: boolean } = {}) {
  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;

      ws = new WebSocket(marketWsUrl());

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as ExecutionMessage;
          if (msg.type !== "execution") return;
          const n = executionToNotification(msg);
          if (n) {
            useNotificationStore.getState().addNotification(n);
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
      ws?.close();
    };
  }, [enabled]);
}
