import type { WorkspaceNotification } from "./use-notification-store";

export type NotificationTarget =
  | { widgetType: string; symbol?: string }
  | { route: string };

function resolveSymbol(n: WorkspaceNotification): string | undefined {
  // Prefer the payload's symbol — for chart-drawn price alerts `ref_id` is the
  // alert's numeric row-id, which would otherwise shadow the real ticker.
  const fromData =
    typeof n.data?.symbol === "string" ? n.data.symbol.trim() : "";
  if (fromData) return fromData.toUpperCase();
  // Fall back to `ref_id` only when it actually looks like a ticker (not a
  // numeric id).
  const fromRef = n.ref_id ? String(n.ref_id).trim() : "";
  if (fromRef && /^[A-Za-z][A-Za-z.-]*$/.test(fromRef)) {
    return fromRef.toUpperCase();
  }
  return undefined;
}

export function notificationTarget(
  n: WorkspaceNotification,
): NotificationTarget | null {
  const sym = resolveSymbol(n);

  switch (n.ref_type) {
    case "symbol":
      return { widgetType: "chart", symbol: sym };
    case "position":
      return { widgetType: "positions" };
    case "order":
      return { widgetType: "orders" };
  }
  switch (n.kind) {
    case "proposal":
    case "agent_action":
    case "exit_signal":
      return { widgetType: "agent-proposals" };
    case "price_alert":
    case "rule_triggered":
    case "rvol":
    case "earnings":
      return sym ? { widgetType: "chart", symbol: sym } : null;
    case "report_ready":
      return { widgetType: "reports" };
    case "research_ready":
    case "scan_ready":
      return { widgetType: "research" };
  }
  return null;
}
