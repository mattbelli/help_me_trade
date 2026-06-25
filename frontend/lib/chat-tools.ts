import { tool } from "ai";
import { z } from "zod";

import type { ChatToolId } from "@/lib/chat-toolbar-config";
import { executePythonTool } from "./python-server";

export function buildChatTools(enabledIds?: readonly ChatToolId[]) {
  const enabled = enabledIds
    ? new Set<string>(enabledIds)
    : null;

  const all = {
    get_portfolio_snapshot: tool({
      description:
        "All accounts (Alpaca paper + read-only Schwab): equity, cash, positions, open orders — per account plus an aggregate. Includes today's day P&L per account + aggregate, plus cumulative unrealized P&L on positions.",
      inputSchema: z.object({}),
      execute: async () =>
        executePythonTool("get_portfolio_snapshot", {}),
    }),
    get_agent_status: tool({
      description:
        "What the trading agent is running: enabled strategies, managed positions with exit state, and proposals pending phone approval.",
      inputSchema: z.object({}),
      execute: async () => executePythonTool("get_agent_status", {}),
    }),
    get_earnings_dates: tool({
      description:
        "Upcoming + recent earnings dates for a symbol (Finnhub calendar). Use for 'when does X report?' instead of web_search.",
      inputSchema: z.object({ symbol: z.string() }),
      execute: async (input) => executePythonTool("get_earnings_dates", input),
    }),
    get_context: tool({
      description:
        "Read everything the app knows about a symbol — quote, RVOL, session-node levels (POC/VAH/VAL/PDH/PDL), technicals (vs 50/200-DMA, RSI, ATR), fundamentals, earnings, the calibrated decision score, and active alerts. Use this FIRST for any question about a symbol's data, levels, or signals — never web_search for our own data.",
      inputSchema: z.object({ symbol: z.string() }),
      execute: async (input) => executePythonTool("get_context", input),
    }),
    search_memories: tool({
      description:
        "Search stored preferences, lessons, and research notes.",
      inputSchema: z.object({
        query: z.string(),
        limit: z.number().int().min(1).max(20).optional(),
      }),
      execute: async (input) => executePythonTool("search_memories", input),
    }),
    start_research: tool({
      description:
        "Enqueue deep research (portfolio, bars, memories, web). Returns a job id.",
      inputSchema: z.object({
        topic: z.string(),
        symbol: z.string().nullable().optional(),
        depth: z.enum(["quick", "standard", "deep"]).optional(),
      }),
      execute: async (input) => executePythonTool("start_research", input),
    }),
    get_trade_history: tool({
      description:
        "Imported/synced trade ledger: fills, dividends, fees.",
      inputSchema: z.object({
        symbol: z.string().nullable().optional(),
        from_date: z.string().nullable().optional(),
        to_date: z.string().nullable().optional(),
        limit: z.number().int().min(1).max(200).optional(),
        account_id: z.string().nullable().optional(),
      }),
      execute: async (input) => executePythonTool("get_trade_history", input),
    }),
    get_performance_insights: tool({
      description:
        "Structured performance scorecard: win rate, P&L by symbol, patterns.",
      inputSchema: z.object({
        period: z
          .enum(["day", "week", "month", "quarter", "year", "all"])
          .optional(),
        account_id: z.string().nullable().optional(),
      }),
      execute: async (input) =>
        executePythonTool("get_performance_insights", input),
    }),
    web_search: tool({
      description:
        "SearXNG web index (news/public pages). Optional time_range=day|week|month|year and sites=[reddit.com,x.com]. NOT live X/Reddit feeds or minute-level sentiment. Do NOT use for option bid/ask — use get_option_quote.",
      inputSchema: z.object({
        query: z.string(),
        limit: z.number().int().min(1).max(10).optional(),
        time_range: z.enum(["day", "week", "month", "year"]).optional(),
        sites: z.array(z.string()).max(4).optional(),
      }),
      execute: async (input) => executePythonTool("web_search", input),
    }),
    get_option_quote: tool({
      description:
        "Broker options bid/ask for a contract (occ_symbol or underlying + expiration + strike + right). Read-only — use for limit-price context, never web_search.",
      inputSchema: z.object({
        occ_symbol: z.string().optional(),
        underlying: z.string().optional(),
        expiration: z.string().optional(),
        strike: z.number().positive().optional(),
        right: z.enum(["C", "P"]).optional(),
      }),
      execute: async (input) => executePythonTool("get_option_quote", input),
    }),
    propose_order: tool({
      description:
        "Propose a paper trade (symbol, side, qty, market|limit). Optionally account_id for STRAT1/STRAT2. Options: long-only buy-to-open, limit-only — limit_price is per-share premium. Shows Approve/Reject card. Call immediately on buy/sell — do not narrate or fake [Calling propose_order...].",
      inputSchema: z.object({
        symbol: z.string(),
        side: z.enum(["buy", "sell"]),
        qty: z.number().positive(),
        order_type: z.enum(["market", "limit"]).optional(),
        limit_price: z.number().positive().optional(),
        account_id: z.string().optional(),
        option_underlying: z.string().optional(),
        option_expiration: z.string().optional(),
        option_strike: z.number().positive().optional(),
        option_right: z.enum(["C", "P"]).optional(),
      }),
      needsApproval: true,
      execute: async (input) => executePythonTool("propose_order", input),
    }),
    propose_rule: tool({
      description:
        "Propose a signal/alert rule (name, condition, action). Shows Approve/Reject card; on approval the rule is armed. Call on alert/signal/watch requests — do not narrate or fake [Calling propose_rule...].",
      inputSchema: z.object({
        spec: z.object({
          name: z.string(),
          condition: z.any(),
          action: z
            .object({
              type: z.string().optional(),
              message: z.string().optional(),
              trigger_on: z.enum(["always", "transition"]).optional(),
              cooldown_seconds: z.number().int().min(0).optional(),
            })
            .optional(),
        }),
      }),
      needsApproval: true,
      execute: async (input) => executePythonTool("propose_rule", input),
    }),
  };

  if (!enabled) return all;

  return Object.fromEntries(
    Object.entries(all).filter(([name]) => enabled.has(name)),
  );
}
