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
        "Read everything the app knows about a symbol — quote, RVOL, session-node levels (POC/VAH/VAL/PDH/PDL), technicals (vs 50/200-DMA, RSI, ATR), fundamentals, earnings, recent news, strategy playbook signals, the calibrated decision score, and active alerts. Use this FIRST for any question about a symbol's data, levels, or signals — never web_search for our own data.",
      inputSchema: z.object({ symbol: z.string() }),
      execute: async (input) => executePythonTool("get_context", input),
    }),
    list_capabilities: tool({
      description:
        "List what app data the agent can read (context scopes, resources, write actions). Call when unsure whether the app has some data.",
      inputSchema: z.object({}),
      execute: async () => executePythonTool("list_capabilities", {}),
    }),
    query: tool({
      // Deliberately does NOT enumerate resources: this legacy TS path cannot import
      // the Python registry, and its hand-copied list had rotted to 9 of 19. The live
      // (CHAT_BRAIN=python) path derives the names from `RESOURCES`; here, point the
      // model at list_capabilities so it reads the real set instead of a stale copy.
      description:
        "List app data across resources (watchlists, market, news, fundamentals, options_flow, my_trades, schwab_accounts and more — call list_capabilities for the full current set; do not assume this handful is all of them). Args: {resource, filters?}. For a SINGLE symbol's quote/levels/score use get_context instead.",
      inputSchema: z.object({
        resource: z.string(),
        filters: z.record(z.string(), z.any()).optional(),
      }),
      execute: async (input) => executePythonTool("query", input),
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
    save_memory: tool({
      description:
        "Save a durable memory: a directive (standing rule), preference, fact, lesson, or thesis. When the user says 'remember this/that ...', pass quote = their exact words (marks it as their own authored rule). Routed through the memory judge (dedup/supersede). Do NOT save risk/config limits (those live in Settings) or one-off requests. Confirm the saved id back.",
      inputSchema: z.object({
        content: z.string(),
        type: z.enum([
          "directive",
          "preference",
          "fact",
          "lesson",
          "thesis",
        ]),
        quote: z.string().optional(),
        symbols: z.array(z.string()).optional(),
        valid_until: z.string().optional(),
      }),
      execute: async (input) => executePythonTool("save_memory", input),
    }),
    forget_memory: tool({
      description:
        "Archive a memory by id (never a hard delete — the user can restore it from History). Use on 'forget that / that's no longer true'. Only pass an id you retrieved this session via search_memories. Tell the user it was archived and can be undone.",
      inputSchema: z.object({
        id: z.number().int(),
        reason: z.string(),
      }),
      execute: async (input) => executePythonTool("forget_memory", input),
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
    create_rule: tool({
      description:
        "Creates a signal/alert rule. An alert rule is ENABLED immediately; a rule whose action is a TRADE is saved DISABLED and the user must arm it himself — read `enabled` in the result and tell him which happened. Confirm parameters with the user before calling. Call on alert/signal/watch requests — do not narrate or fake [Calling create_rule...].",
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
      execute: async (input) => executePythonTool("create_rule", input),
    }),
    create_price_alert: tool({
      description:
        "Create a price alert (a notification, not an order). direction: above|below|cross (default cross); repeat=true for a persistent alert that re-arms after each crossing. To set an alert at a named level (VAL/POC/VAH/PDH/PDL), first call get_context(symbol) to read that level's price.",
      inputSchema: z.object({
        symbol: z.string(),
        price: z.number().positive(),
        direction: z.enum(["above", "below", "cross"]).optional(),
        note: z.string().optional(),
        repeat: z.boolean().optional(),
      }),
      execute: async (input) => executePythonTool("create_price_alert", input),
    }),
    update_price_alert: tool({
      description:
        "Edit an existing price alert (id, optional price, direction, note, repeat). Get the id via query('price_alerts') or get_context.",
      inputSchema: z.object({
        id: z.number().int(),
        price: z.number().positive().optional(),
        direction: z.enum(["above", "below", "cross"]).optional(),
        note: z.string().optional(),
        repeat: z.boolean().optional(),
      }),
      execute: async (input) => executePythonTool("update_price_alert", input),
    }),
    delete_price_alert: tool({
      description:
        "Remove a price alert by id. Get the id via query('price_alerts') or get_context.",
      inputSchema: z.object({
        id: z.number().int(),
      }),
      execute: async (input) => executePythonTool("delete_price_alert", input),
    }),
    create_watchlist: tool({
      description:
        "Create a new watchlist (name + optional starting symbols); it becomes the active list. Only symbols the user named or just discussed — never pad a list.",
      inputSchema: z.object({
        name: z.string(),
        symbols: z.array(z.string()).optional(),
      }),
      execute: async (input) => executePythonTool("create_watchlist", input),
    }),
    add_watchlist_symbols: tool({
      description:
        "Add symbols to a watchlist. list = the list's name or id (query('watchlists') to see them); omit for the active list. Slot-capped lists (e.g. the flow list) reject adds when full — report the error, never silently drop.",
      inputSchema: z.object({
        symbols: z.array(z.string()).min(1),
        list: z.string().optional(),
      }),
      execute: async (input) =>
        executePythonTool("add_watchlist_symbols", input),
    }),
    remove_watchlist_symbols: tool({
      description:
        "Remove symbols from a watchlist. list = the list's name or id; omit for the active list.",
      inputSchema: z.object({
        symbols: z.array(z.string()).min(1),
        list: z.string().optional(),
      }),
      execute: async (input) =>
        executePythonTool("remove_watchlist_symbols", input),
    }),
    rename_watchlist: tool({
      description:
        "Rename a watchlist. list = the CURRENT name or id (required — never guess).",
      inputSchema: z.object({
        list: z.string(),
        name: z.string(),
      }),
      execute: async (input) => executePythonTool("rename_watchlist", input),
    }),
  };

  if (!enabled) return all;

  return Object.fromEntries(
    Object.entries(all).filter(([name]) => enabled.has(name)),
  );
}
