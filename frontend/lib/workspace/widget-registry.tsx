"use client";

import type { ComponentType } from "react";

import { AccountSummaryWidget } from "@/components/workspace/widgets/account-summary-widget";
import { ChartWidget } from "@/components/workspace/widgets/chart-widget";
import { ChatWidget } from "@/components/workspace/widgets/chat-widget";
import { EquityCurveWidget } from "@/components/workspace/widgets/equity-curve-widget";
import { OpenOrdersWidget } from "@/components/workspace/widgets/open-orders-widget";
import { PositionsWidget } from "@/components/workspace/widgets/positions-widget";
import { PriceAlertsWidget } from "@/components/workspace/widgets/price-alerts-widget";
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
    id: "chat",
    title: "copilot",
    description: "Trading copilot conversation",
    multiInstance: false,
    channelAware: false,
    component: ChatWidget,
    defaultSize: { w: 8, h: 8, minW: 5, minH: 5 },
  },
];

export const WIDGET_MAP = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.id, w]),
) as Record<string, WidgetDefinition>;

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return WIDGET_MAP[id];
}
