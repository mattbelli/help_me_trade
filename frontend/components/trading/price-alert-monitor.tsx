"use client";

import {
  requestAlertNotifications,
  usePriceAlertMonitor,
} from "@/lib/use-price-alert-monitor";

export function PriceAlertMonitor() {
  usePriceAlertMonitor();
  return null;
}

export { requestAlertNotifications };
