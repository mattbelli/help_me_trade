"use client";

import { useState } from "react";

import {
  requestAlertNotifications,
  usePriceAlertMonitor,
  type TriggeredAlert,
} from "@/lib/use-price-alert-monitor";

export function PriceAlertMonitor() {
  const [toast, setToast] = useState<TriggeredAlert | null>(null);

  usePriceAlertMonitor({
    onTriggered: (alert) => {
      setToast(alert);
      window.setTimeout(() => setToast(null), 8_000);
    },
  });

  return (
    <>
      {toast ? (
        <div className="fixed bottom-6 right-6 z-50 max-w-sm rounded-lg border border-border bg-card px-4 py-3 shadow-lg">
          <p className="text-sm font-medium">{toast.symbol} alert triggered</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Target {toast.price.toFixed(2)}
            {toast.trigger_price != null
              ? ` · now ${toast.trigger_price.toFixed(2)}`
              : ""}
          </p>
        </div>
      ) : null}
    </>
  );
}

export { requestAlertNotifications };
