"use client";

import type { ComponentType } from "react";

import { AccountSummaryWidget } from "@/components/workspace/widgets/account-summary-widget";
import { ChartWidget } from "@/components/workspace/widgets/chart-widget";
import { EquityCurveWidget } from "@/components/workspace/widgets/equity-curve-widget";
import { OpenOrdersWidget } from "@/components/workspace/widgets/open-orders-widget";
import { PositionsWidget } from "@/components/workspace/widgets/positions-widget";
import { PriceAlertsWidget } from "@/components/workspace/widgets/price-alerts-widget";
import { WatchlistWidget } from "@/components/workspace/widgets/watchlist-widget";

export type WidgetDefaultSize = {
  w: number;
  h: number;
  minW: number;
  minH: number;
};

export type WidgetDefinition = {
  id: string;
  title: string;
  meta?: string;
  description?: string;
  component: ComponentType;
  defaultSize: WidgetDefaultSize;
};

export const WIDGET_REGISTRY: WidgetDefinition[] = [
  {
    id: "account-summary",
    title: "account summary",
    description: "Portfolio, buying power, cash, unrealized P/L",
    component: AccountSummaryWidget,
    defaultSize: { w: 12, h: 3, minW: 4, minH: 2 },
  },
  {
    id: "open-orders",
    title: "open orders",
    description: "Working and recent orders",
    component: OpenOrdersWidget,
    defaultSize: { w: 6, h: 5, minW: 4, minH: 3 },
  },
  {
    id: "positions",
    title: "positions",
    description: "Open positions table",
    component: PositionsWidget,
    defaultSize: { w: 6, h: 5, minW: 4, minH: 3 },
  },
  {
    id: "equity-curve",
    title: "equity curve",
    description: "Portfolio equity over time",
    component: EquityCurveWidget,
    defaultSize: { w: 6, h: 5, minW: 4, minH: 3 },
  },
  {
    id: "watchlist",
    title: "watchlist",
    description: "Active watchlist with live quotes",
    component: WatchlistWidget,
    defaultSize: { w: 6, h: 5, minW: 4, minH: 3 },
  },
  {
    id: "chart",
    title: "chart",
    description: "Candlestick chart for a symbol",
    component: ChartWidget,
    defaultSize: { w: 6, h: 6, minW: 4, minH: 4 },
  },
  {
    id: "price-alerts",
    title: "price alerts",
    description: "Configured price alerts",
    component: PriceAlertsWidget,
    defaultSize: { w: 6, h: 4, minW: 4, minH: 3 },
  },
];

export const WIDGET_MAP = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.id, w]),
) as Record<string, WidgetDefinition>;

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return WIDGET_MAP[id];
}
