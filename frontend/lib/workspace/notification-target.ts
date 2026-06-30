import type { WorkspaceNotification } from "./use-notification-store";

export type NotificationTarget = { widgetType: string; symbol?: string };

export function notificationTarget(
  n: WorkspaceNotification,
): NotificationTarget | null {
  const raw = n.ref_id ?? (n.data?.symbol as string | undefined);
  const sym = raw ? String(raw).trim().toUpperCase() || undefined : undefined;

  switch (n.ref_type) {
    case "symbol":
      return { widgetType: "chart", symbol: sym };
    case "position":
      return { widgetType: "positions" };
    case "order":
      return { widgetType: "open-orders" };
  }
  switch (n.kind) {
    case "proposal":
    case "agent_action":
    case "exit_signal":
      return { widgetType: "agent-proposals" };
    case "price_alert":
    case "rvol":
    case "earnings":
      return sym ? { widgetType: "chart", symbol: sym } : null;
    case "report_ready":
      return { widgetType: "reports" };
  }
  return null;
}
