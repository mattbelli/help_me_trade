"use client";

import { useEffect } from "react";

import { marketWsUrl, type ServerNotification } from "@/lib/api";
import {
  isPriceAlertsChangedMessage,
  requestPriceAlertRefresh,
} from "@/lib/price-alert-sync";
import { requestAlertNotifications } from "@/lib/use-price-alert-monitor";

import {
  mapServerNotification,
  useNotificationStore,
} from "./use-notification-store";

const RECONNECT_MS = 3_000;

type NotificationWsMessage = ServerNotification & { type: "notification" };

type NotificationUpdateWsMessage = {
  type: "notification_update";
  id: string;
  read: boolean;
  dismissed?: boolean;
};

function isNotificationMessage(
  msg: Record<string, unknown>,
): msg is NotificationWsMessage {
  return (
    msg.type === "notification" &&
    typeof msg.id === "string" &&
    typeof msg.kind === "string" &&
    typeof msg.title === "string" &&
    typeof msg.body === "string" &&
    typeof msg.created_at === "string"
  );
}

function isNotificationUpdateMessage(
  msg: Record<string, unknown>,
): msg is NotificationUpdateWsMessage {
  return (
    msg.type === "notification_update" &&
    typeof msg.id === "string" &&
    typeof msg.read === "boolean"
  );
}

export function useNotificationStream({
  enabled = true,
}: { enabled?: boolean } = {}) {
  useEffect(() => {
    if (!enabled) return;

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

          if (isPriceAlertsChangedMessage(msg)) {
            requestPriceAlertRefresh();
            return;
          }

          if (isNotificationMessage(msg)) {
            useNotificationStore
              .getState()
              .addNotification(mapServerNotification(msg));
            return;
          }

          if (isNotificationUpdateMessage(msg)) {
            useNotificationStore.getState().applyUpdate(msg.id, {
              read: msg.read,
              dismissed: msg.dismissed,
            });
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onopen = () => {
        // Reconcile anything committed while this client was disconnected.
        requestPriceAlertRefresh();
      };

      ws.onclose = () => {
        if (cancelled) return;
        reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    const catchUpIfVisible = () => {
      if (document.visibilityState === "visible") requestPriceAlertRefresh();
    };
    document.addEventListener("visibilitychange", catchUpIfVisible);
    window.addEventListener("focus", catchUpIfVisible);
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      document.removeEventListener("visibilitychange", catchUpIfVisible);
      window.removeEventListener("focus", catchUpIfVisible);
      ws?.close();
    };
  }, [enabled]);
}
