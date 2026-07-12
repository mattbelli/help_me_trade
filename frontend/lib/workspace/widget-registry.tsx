"use client";

import type { ComponentType } from "react";

import { AccountSummaryWidget } from "@/components/workspace/widgets/account-summary-widget";
import { AgentProposalsWidget } from "@/components/workspace/widgets/agent-proposals-widget";
import { ChartWidget } from "@/components/workspace/widgets/chart-widget";
import { EquityCurveWidget } from "@/components/workspace/widgets/equity-curve-widget";
import { JournalWidget } from "@/components/workspace/widgets/journal-widget";
import { OpenOrdersWidget } from "@/components/workspace/widgets/open-orders-widget";
import { PerformanceWidget } from "@/components/workspace/widgets/performance-widget";
import { PositionsWidget } from "@/components/workspace/widgets/positions-widget";
import { MemoryWidget } from "@/components/workspace/widgets/memory-widget";
import { NotificationsWidget } from "@/components/workspace/widgets/notifications-widget";
import { PriceAlertsWidget } from "@/components/workspace/widgets/price-alerts-widget";
import { ReportsWidget } from "@/components/workspace/widgets/reports-widget";
import { ResearchWidget } from "@/components/workspace/widgets/research-widget";
import { RulesWidget } from "@/components/workspace/widgets/rules-widget";
import { RvolScannerWidget } from "@/components/workspace/widgets/rvol-scanner-widget";
import { OptionsFlowWidget } from "@/components/workspace/widgets/options-flow-widget";
import { OptionsChainWidget } from "@/components/workspace/widgets/options-chain-widget";
import { ScansWidget } from "@/components/workspace/widgets/scans-widget";
import { WatchlistWidget } from "@/components/workspace/widgets/watchlist-widget";
import { PerformanceStatsWidget } from "@/components/workspace/widgets/performance-stats-widget";
import { TradingCalendarWidget } from "@/components/workspace/widgets/trading-calendar-widget";
import { OrdersWidget } from "@/components/workspace/widgets/orders-widget";
import { SectorRadarWidget } from "@/components/workspace/widgets/sector-radar-widget";
import { PnlScatterWidget } from "@/components/workspace/widgets/pnl-scatter-widget";
import { OutcomeDistributionWidget } from "@/components/workspace/widgets/outcome-distribution-widget";
import { WinrateTrendWidget } from "@/components/workspace/widgets/winrate-trend-widget";
import { CompanyInfoWidget } from "@/components/workspace/widgets/company-info-widget";
import { IdeaOutcomesWidget } from "@/components/workspace/widgets/idea-outcomes-widget";
import { FilingsWidget } from "@/components/workspace/widgets/filings-widget";
import { ChatWidget } from "@/components/workspace/widgets/chat-widget";

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
    channelAware: true,
    component: OpenOrdersWidget,
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "positions",
    title: "positions",
    description: "Open positions table",
    channelAware: true,
    component: PositionsWidget,
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
    channelAware: true,
    component: PriceAlertsWidget,
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
    description: "Universe screener — technical scan presets, ranked matches",
    channelAware: true,
    component: ScansWidget,
    defaultSize: { w: 12, h: 5, minW: 5, minH: 3 },
  },
  {
    id: "options-flow",
    title: "options flow",
    description:
      "Unusual options activity on the watchlist — facts about the tape (perception), not a buy/sell signal",
    channelAware: true,
    component: OptionsFlowWidget,
    defaultSize: { w: 12, h: 6, minW: 5, minH: 4 },
  },
  {
    id: "options-chain",
    title: "Options Chain",
    description:
      "Browse an underlying's option chain — expirations, strikes, bid/ask, IV, greeks, volume, OI",
    channelAware: true,
    component: OptionsChainWidget,
    defaultSize: { w: 14, h: 7, minW: 6, minH: 4 },
  },
  {
    id: "idea-outcomes",
    title: "idea outcomes",
    description:
      "Shadow-tracked scan ideas — every idea's mechanical outcome + win rate",
    channelAware: true,
    component: IdeaOutcomesWidget,
    defaultSize: { w: 12, h: 6, minW: 8, minH: 4 },
  },
  {
    id: "filings",
    title: "filings",
    description:
      "Insider Form 4 buys, 13D stakes, congressional trades — free primary-source feed",
    channelAware: true,
    component: FilingsWidget,
    defaultSize: { w: 12, h: 6, minW: 8, minH: 4 },
  },
  {
    id: "rules",
    title: "rules",
    description: "Create, arm, and manage signal rules with trigger log",
    component: RulesWidget,
    defaultSize: { w: 12, h: 10, minW: 6, minH: 6 },
  },
  {
    id: "research",
    title: "research",
    description: "Deep research runs, artifact history, and report viewer",
    component: ResearchWidget,
    defaultSize: { w: 12, h: 10, minW: 6, minH: 5 },
  },
  {
    id: "performance",
    title: "performance (legacy)",
    description: "Trade analytics — P&L charts, calendar, win rate, and round-trips",
    component: PerformanceWidget,
    defaultSize: { w: 16, h: 12, minW: 8, minH: 6 },
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
    description: "Inbox of alert cards — batched, filtered, with a ding",
    channelAware: true,
    component: NotificationsWidget,
    defaultSize: { w: 8, h: 6, minW: 4, minH: 3 },
  },
  {
    id: "agent-proposals",
    title: "agent proposals",
    description: "Pending AI order proposals with score — Approve / Reject",
    channelAware: true,
    component: AgentProposalsWidget,
    defaultSize: { w: 12, h: 6, minW: 5, minH: 3 },
  },
  {
    id: "journal",
    title: "decision journal",
    description:
      "The agent's own paper-call track record and your real trades — AI ⇄ Me toggle",
    channelAware: true,
    component: JournalWidget,
    defaultSize: { w: 12, h: 8, minW: 5, minH: 4 },
  },
  {
    id: "rvol-scanner",
    title: "Volume Scanner",
    description: "Market-wide unusual volume ranking (RVOL ≥ 1.5×)",
    channelAware: true,
    component: RvolScannerWidget,
    defaultSize: { w: 8, h: 7, minW: 4, minH: 3 },
  },
  {
    id: "performance-stats",
    title: "performance stats",
    description: "Realized/unrealized P&L, win rate, trades, fees",
    component: PerformanceStatsWidget as WidgetDefinition["component"],
    defaultSize: { w: 16, h: 3, minW: 8, minH: 2 },
  },
  {
    id: "trade-calendar",
    title: "trading calendar",
    description: "Monthly P&L calendar with week & day-of-week rollups",
    component: TradingCalendarWidget as WidgetDefinition["component"],
    defaultSize: { w: 14, h: 9, minW: 9, minH: 6 },
  },
  {
    id: "orders",
    title: "orders",
    description: "Trade blotter — filled / open / rejected",
    channelAware: true,
    component: OrdersWidget,
    defaultSize: { w: 12, h: 6, minW: 6, minH: 4 },
  },
  {
    id: "sector-radar",
    title: "sector radar",
    description: "Portfolio exposure & realized P&L by sector",
    component: SectorRadarWidget as WidgetDefinition["component"],
    defaultSize: { w: 10, h: 7, minW: 6, minH: 5 },
  },
  {
    id: "pnl-scatter",
    title: "hold time vs p&l",
    description: "Each closed trade: holding period vs P&L",
    channelAware: true,
    component: PnlScatterWidget,
    defaultSize: { w: 10, h: 6, minW: 6, minH: 4 },
  },
  {
    id: "trade-distribution",
    title: "trade distribution",
    description: "Histogram of trade outcomes (edge shape)",
    component: OutcomeDistributionWidget as WidgetDefinition["component"],
    defaultSize: { w: 10, h: 5, minW: 6, minH: 4 },
  },
  {
    id: "winrate-trend",
    title: "win rate trend",
    description: "Rolling win rate & expectancy over time",
    component: WinrateTrendWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 5, minW: 6, minH: 4 },
  },
  {
    id: "company-info",
    title: "company info",
    description: "Profile, fundamentals, facts & news — follows a channel; all-watchlist news mode",
    multiInstance: true,
    channelAware: true,
    component: CompanyInfoWidget,
    defaultSize: { w: 12, h: 9, minW: 6, minH: 5 },
  },
  {
    id: "chat",
    title: "chat",
    description: "The agent chat — same conversation as the terminal drawer",
    multiInstance: false,
    component: ChatWidget as WidgetDefinition["component"],
    defaultSize: { w: 12, h: 6, minW: 5, minH: 4 },
  },
];

export const WIDGET_MAP = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.id, w]),
) as Record<string, WidgetDefinition>;

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return WIDGET_MAP[id];
}
