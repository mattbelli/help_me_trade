"use client";

import { useEffect } from "react";

import { marketWsUrl } from "@/lib/api";
import { requestAlertNotifications } from "@/lib/use-price-alert-monitor";

import { useNotificationStore } from "./use-notification-store";

const RECONNECT_MS = 3_000;

function actionMessage(action: unknown): string | undefined {
  if (action == null) return undefined;
  if (typeof action === "string") return action;
  if (typeof action === "object" && action !== null) {
    const obj = action as Record<string, unknown>;
    if (typeof obj.message === "string" && obj.message.trim()) {
      return obj.message.trim();
    }
  }
  return undefined;
}

function isRuleAlert(msg: Record<string, unknown>): boolean {
  if (msg.alert != null) return false;
  return Boolean(msg.rule_name || msg.rule_id || msg.source);
}

export function useAlertStream() {
  useEffect(() => {
    void requestAlertNotifications();

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;

      ws = new WebSocket(marketWsUrl());

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as Record<string, unknown>;
          if (msg.type !== "alert" || !isRuleAlert(msg)) return;

          const id =
            typeof msg.id === "string"
              ? msg.id
              : `alert-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

          useNotificationStore.getState().addNotification({
            id,
            ruleId: typeof msg.rule_id === "string" ? msg.rule_id : undefined,
            ruleName:
              typeof msg.rule_name === "string" ? msg.rule_name : "Alert",
            action: msg.action,
            source: typeof msg.source === "string" ? msg.source : "rule",
            message: actionMessage(msg.action),
            ts:
              typeof msg.ts === "string"
                ? msg.ts
                : new Date().toISOString(),
            read: false,
          });
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
  }, []);
}
