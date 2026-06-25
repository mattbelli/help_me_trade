/** Tool ids must match keys in `buildChatTools` / Python `TOOL_DEFINITIONS`. */
export const CHAT_TOOL_OPTIONS = [
  {
    id: "get_portfolio_snapshot",
    label: "Portfolio",
    description: "All accounts — equity, positions, aggregate",
  },
  {
    id: "get_agent_status",
    label: "Agent status",
    description: "Strategies, managed positions, pending approvals",
  },
  {
    id: "get_earnings_dates",
    label: "Earnings dates",
    description: "Upcoming and recent earnings for a symbol",
  },
  {
    id: "get_context",
    label: "Symbol context",
    description: "Quote, RVOL, session-node levels, technicals, earnings, score",
  },
  {
    id: "search_memories",
    label: "Memories",
    description: "Search saved preferences and lessons",
  },
  {
    id: "start_research",
    label: "Research",
    description: "Enqueue deep research jobs",
  },
  {
    id: "get_trade_history",
    label: "Trade history",
    description: "Ledger fills and dividends",
  },
  {
    id: "get_performance_insights",
    label: "Performance",
    description: "Win rate and P&L scorecard",
  },
  {
    id: "web_search",
    label: "Web search",
    description: "SearXNG web index — not live X/Reddit sentiment",
  },
  {
    id: "get_option_quote",
    label: "Option quote",
    description: "Broker options bid/ask for limit-price context",
  },
  {
    id: "propose_order",
    label: "Propose order",
    description: "Paper trade approval card (no execution)",
  },
  {
    id: "propose_rule",
    label: "Propose alert",
    description: "Signal/alert rule approval card",
  },
] as const;

export type ChatToolId = (typeof CHAT_TOOL_OPTIONS)[number]["id"];

export const DEFAULT_ENABLED_TOOLS: ChatToolId[] = CHAT_TOOL_OPTIONS.map(
  (t) => t.id,
);

export type ChatSettings = {
  provider: string;
  model: string;
  enabledTools: ChatToolId[];
  /** When true, relevant memories are injected into the system prompt each turn. */
  autoInjectMemories: boolean;
};

export const DEFAULT_CHAT_SETTINGS: ChatSettings = {
  provider: "",
  model: "",
  // All tools on by default — an empty list means streamText gets zero tools and
  // the model fabricates instead of calling web_search / get_performance_insights.
  enabledTools: DEFAULT_ENABLED_TOOLS,
  // Always on — tools/auto-inject are no longer toggled in chat (out of sight, simple).
  autoInjectMemories: true,
};
