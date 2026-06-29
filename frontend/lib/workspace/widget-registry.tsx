"use client";

import type { ComponentType } from "react";

import { AccountSummaryWidget } from "@/components/workspace/widgets/account-summary-widget";
import { AgentProposalsWidget } from "@/components/workspace/widgets/agent-proposals-widget";
import { ChartWidget } from "@/components/workspace/widgets/chart-widget";
import { EquityCurveWidget } from "@/components/workspace/widgets/equity-curve-widget";
import { OpenOrdersWidget } from "@/components/workspace/widgets/open-orders-widget";
import { PositionsWidget } from "@/components/workspace/widgets/positions-widget";
import { MemoryWidget } from "@/components/workspace/widgets/memory-widget";
import { NotificationsWidget } from "@/components/workspace/widgets/notifications-widget";
import { PriceAlertsWidget } from "@/components/workspace/widgets/price-alerts-widget";
import { ReportsWidget } from "@/components/workspace/widgets/reports-widget";
import { RvolScannerWidget } from "@/components/workspace/widgets/rvol-scanner-widget";
import { ScansWidget } from "@/components/workspace/widgets/scans-widget";
import { WatchlistWidget } from "@/components/workspace/widgets/watchlist-widget";

import type { WidgetInstanceConfig } from "./layout-persistence";

export type WidgetDefaultSize = {
  w: number;
  h: number;
  minW: number;
  minH: number;
};

export type WidgetProps = {
  instanceId: string;
  config: WidgetInstanceConfig;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
};

export type WidgetDefinition = {
  id: string;
  title: string;
  meta?: string;
  description?: string;
  multiInstance?: boolean;
  channelAware?: boolean;
  component: ComponentType<WidgetProps>;
  defaultSize: WidgetDefaultSize;
};

export const WIDGET_REGISTRY: WidgetDefinition[] = [
  {
    id: "account-summary",
    title: "account summary",
    description: "Portfolio, buying power, cash, unrealized P/L",
    component: AccountSummaryWidget as WidgetDefinition["component"],
    defaultSize: { w: 24, h: 3, minW: 6, minH: 2 },
  },
  {
    id: "open-orders",
    title: "open orders",
    description: "Working and recent orders",
    component: OpenOrdersWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "positions",
    title: "positions",
    description: "Open positions table",
    component: PositionsWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "equity-curve",
    title: "equity curve",
    description: "Portfolio equity over time",
    component: EquityCurveWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "watchlist",
    title: "watchlist",
    description: "Active watchlist with live quotes",
    multiInstance: true,
    channelAware: true,
    component: WatchlistWidget,
    defaultSize: { w: 8, h: 6, minW: 4, minH: 3 },
  },
  {
    id: "chart",
    title: "chart",
    description: "Candlestick chart for a symbol",
    multiInstance: true,
    channelAware: true,
    component: ChartWidget,
    defaultSize: { w: 16, h: 6, minW: 6, minH: 4 },
  },
  {
    id: "price-alerts",
    title: "price alerts",
    description: "Configured price alerts",
    component: PriceAlertsWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 4, minW: 5, minH: 3 },
  },
  {
    id: "reports",
    title: "reports",
    description: "Morning levels and other generated reports",
    component: ReportsWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 6, minW: 5, minH: 4 },
  },
  {
    id: "scans",
    title: "scans",
    description: "Armed signal rules and recent fires",
    component: ScansWidget,
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "memory",
    title: "memory",
    description: "Long-term preferences and lessons the copilot has learned",
    component: MemoryWidget,
    defaultSize: { w: 10, h: 5, minW: 4, minH: 3 },
  },
  {
    id: "notifications",
    title: "notifications",
    description: "Full inbox — read, filter, and dismiss alerts",
    component: NotificationsWidget,
    defaultSize: { w: 8, h: 6, minW: 4, minH: 3 },
  },
  {
    id: "agent-proposals",
    title: "agent proposals",
    description: "Pending AI order proposals with score — Approve / Reject",
    component: AgentProposalsWidget,
    defaultSize: { w: 12, h: 6, minW: 5, minH: 3 },
  },
  {
    id: "rvol-scanner",
    title: "Volume Scanner",
    description: "Market-wide unusual volume ranking (RVOL ≥ 1.5×)",
    channelAware: true,
    component: RvolScannerWidget,
    defaultSize: { w: 8, h: 7, minW: 4, minH: 3 },
  },
];

export const WIDGET_MAP = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.id, w]),
) as Record<string, WidgetDefinition>;

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return WIDGET_MAP[id];
}
