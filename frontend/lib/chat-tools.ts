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
        "Latest account, positions, and open orders from the broker poll.",
      inputSchema: z.object({}),
      execute: async () =>
        executePythonTool("get_portfolio_snapshot", {}),
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
        "SearXNG web index (news/public pages). Optional time_range=day|week|month|year and sites=[reddit.com,x.com]. NOT live X/Reddit feeds or minute-level sentiment.",
      inputSchema: z.object({
        query: z.string(),
        limit: z.number().int().min(1).max(10).optional(),
        time_range: z.enum(["day", "week", "month", "year"]).optional(),
        sites: z.array(z.string()).max(4).optional(),
      }),
      execute: async (input) => executePythonTool("web_search", input),
    }),
    propose_order: tool({
      description:
        "Propose a paper trade (symbol, side, qty, market|limit). Shows Approve/Reject card. Call immediately on buy/sell — do not narrate or fake [Calling propose_order...].",
      inputSchema: z.object({
        symbol: z.string(),
        side: z.enum(["buy", "sell"]),
        qty: z.number().positive(),
        order_type: z.enum(["market", "limit"]).optional(),
        limit_price: z.number().positive().optional(),
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
