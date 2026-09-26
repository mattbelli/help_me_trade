export type PriceAlertsChangedMessage = {
  type: "price_alerts_changed";
  action?: "created" | "updated" | "deleted" | "triggered";
  alert_id?: number;
  symbol?: string;
};

type RefreshListener = () => void;

const refreshListeners = new Set<RefreshListener>();

export function isPriceAlertsChangedMessage(
  value: unknown,
): value is PriceAlertsChangedMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { type?: unknown }).type === "price_alerts_changed"
  );
}

/** Fan one server invalidation out to every mounted alert surface. */
export function requestPriceAlertRefresh() {
  for (const listener of [...refreshListeners]) {
    listener();
  }
}

export function subscribePriceAlertRefresh(listener: RefreshListener) {
  refreshListeners.add(listener);
  return () => {
    refreshListeners.delete(listener);
  };
}
