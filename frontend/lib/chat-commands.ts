import type { ChatToolId } from "@/lib/chat-toolbar-config";

export type ChatCommand = {
  id: string;
  label: string;
  description: string;
  prompt: string;
  /** Tools auto-enabled in the composer when this command is picked. */
  tools?: ChatToolId[];
};

export const CHAT_COMMANDS: ChatCommand[] = [
  {
    id: "portfolio",
    label: "portfolio",
    description: "Summarize positions and exposure",
    prompt: "Summarize my current portfolio positions and exposure.",
    tools: ["get_portfolio_snapshot"],
  },
  {
    id: "performance",
    label: "performance",
    description: "Win rate and P&L scorecard",
    prompt: "Show my performance scorecard for the past year.",
    tools: ["get_performance_insights"],
  },
  {
    id: "web",
    label: "web",
    description: "Search the web via SearXNG",
    prompt:
      "Search the web for the most talked-about stocks this morning on X, Reddit, and financial news.",
    tools: ["web_search"],
  },
  {
    id: "research",
    label: "research",
    description: "Queue a deep research report",
    prompt: "Start a deep research report on: ",
    tools: ["start_research"],
  },
  {
    id: "trades",
    label: "trades",
    description: "Recent fills and dividends",
    prompt: "Summarize my recent trade history.",
    tools: ["get_trade_history"],
  },
  {
    id: "memories",
    label: "memories",
    description: "Search saved preferences and lessons",
    prompt: "What do you remember about my trading preferences?",
    tools: ["search_memories"],
  },
  {
    id: "buy",
    label: "buy",
    description: "Paper order proposal (Approve/Reject card)",
    prompt: "Buy 10 shares of NVDA at market (paper proposal).",
    tools: ["propose_order"],
  },
  {
    id: "help",
    label: "help",
    description: "List available slash commands",
    prompt:
      "List the slash commands you support (/portfolio, /performance, /web, /research, /trades, /memories, /buy) and what each does.",
  },
];

export function findChatCommand(id: string): ChatCommand | undefined {
  return CHAT_COMMANDS.find((cmd) => cmd.id === id);
}
