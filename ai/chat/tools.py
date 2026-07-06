"""Trading copilot agent tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from ai.perf_context import format_perf_insights_for_llm, sanitize_trade_history_stats
from ai.research.jobs import submit_research_job
from ai.utils.symbols import extract_symbol
from db.account_scope import resolve_perf_scope
from db.accounts import (
    account_is_execution_capable,
    default_execution_account,
    get_account,
    list_accounts,
)
from db.analytics import ai_performance_context
from db.base import now_iso
from db.earnings_calendar import earnings_dates_for, next_earnings_date
from db.managed_positions import list_open_positions
from db.paper_orders import get_proposal, list_pending_proposals
from db.sqlite import get_all_latest_snapshots
from db.strategies import list_strategies
from db.trades import list_trade_events, trade_summary_stats
from signals.rule_schema import Group, Leaf, RuleSpec

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "get_portfolio_snapshot": {
        "title": "Portfolio snapshot",
        "description": (
            "All accounts (Alpaca paper + read-only Schwab): equity, cash, positions, "
            "open orders — per account plus an aggregate. Includes today's day P&L per "
            "account + aggregate, plus cumulative unrealized P&L on positions."
        ),
    },
    "get_agent_status": {
        "title": "Agent status",
        "description": (
            "What the trading agent is running right now: enabled strategies + their "
            "config, positions the agent is managing (with exit state), and proposals "
            "pending phone approval."
        ),
    },
    "get_earnings_dates": {
        "title": "Earnings dates",
        "description": (
            "Upcoming + recent earnings dates for a symbol (live Finnhub calendar). "
            "Use this for 'when does X report?' instead of web_search."
        ),
    },
    "get_context": {
        "title": "Symbol context",
        "description": (
            "Read everything the app knows about a symbol — quote, RVOL, session-node "
            "levels (POC/VAH/VAL/PDH/PDL), technicals (vs 50/200-DMA, RSI, ATR), "
            "fundamentals, earnings, recent news headlines, strategy playbook signals, "
            "the calibrated decision score, and active alerts. "
            "Use this FIRST for any question about a symbol's data/levels/signals — "
            "do NOT web_search for our own data."
        ),
    },
    "list_capabilities": {
        "title": "Perception capabilities",
        "description": (
            "List what app data the agent can read (scopes + fields). Call when unsure "
            "whether the app has some data."
        ),
    },
    "query": {
        "title": "App data query",
        "description": (
            "List app data across resources: watchlists, strategies, screener, "
            "market regime, news, calibration, price_alerts, proposals, reports. "
            "Args: {resource, filters?}. For a SINGLE symbol's quote/levels/score "
            "use get_context instead."
        ),
    },
    "search_memories": {
        "title": "Search memories",
        "description": "Search stored preferences, lessons, and research notes.",
    },
    "save_memory": {
        "title": "Save memory",
        "description": (
            "Save a durable memory to the long-term store: a directive (standing "
            "rule), preference, fact, lesson, or thesis. When the user says "
            "'remember this/that ...', pass quote = their exact words (that marks "
            "it as the user's own, authored rule). Routed through the memory judge "
            "(dedup/supersede). Confirm the saved id back to the user."
        ),
    },
    "forget_memory": {
        "title": "Forget memory",
        "description": (
            "Archive a memory by id (never a hard delete — the user can restore it "
            "from History). Use on 'forget that / that's no longer true'. Only pass "
            "an id you retrieved this session via search_memories. Tell the user it "
            "was archived and can be undone."
        ),
    },
    "start_research": {
        "title": "Start research",
        "description": (
            "Enqueue Odysseus-style deep research (portfolio, bars, memories, web). "
            "Returns a job id — use the Research page or stream API for progress."
        ),
    },
    "get_trade_history": {
        "title": "Trade history",
        "description": (
            "Imported/synced trade ledger: fills, dividends, fees. "
            "Use for questions about past trades, symbols, or dates."
        ),
    },
    "get_performance_insights": {
        "title": "Performance insights",
        "description": (
            "Structured performance scorecard: reliable net-cash realized P&L on closed "
            "positions, win rate, day-of-week patterns, behavioral facts, entry style. "
            "Prefer over guessing performance."
        ),
    },
    "web_search": {
        "title": "Web search",
        "description": (
            "SearXNG web index: news articles and public pages. Optional site scoping "
            "(reddit.com, x.com) and time_range=day|week|month|year. NOT live X/Reddit "
            "feeds — no minute-level windows, no sentiment scores. Say so if user asks. "
            "Do NOT use for option bid/ask or contract pricing — use get_option_quote."
        ),
    },
    "get_option_quote": {
        "title": "Option quote",
        "description": (
            "Live or delayed broker options bid/ask for a contract. Provide occ_symbol "
            "or underlying + expiration (YYYY-MM-DD) + strike + right (C|P). "
            "Read-only — use for limit-price context, never web_search for quotes."
        ),
    },
    "propose_order": {
        "title": "Propose order",
        "description": (
            "Propose a paper trade for user approval (symbol, side buy|sell, qty, "
            "order_type market|limit). Optionally set account_id to target a specific "
            "paper account (STRAT1/STRAT2); defaults to the primary paper account. "
            "Options: long-only buy-to-open calls/puts, limit-only — limit_price is "
            "per-share premium (max loss = premium × 100 × contracts). Use "
            "option_underlying, option_expiration (YYYY-MM-DD), option_strike, "
            "option_right (C|P), or a Schwab-style option symbol. Shows Approve/Reject "
            "card — does not execute. Call immediately on buy/sell requests; never "
            "narrate a fake tool call."
        ),
    },
    "create_rule": {
        "title": "Create alert rule",
        "description": (
            "Creates AND ENABLES a trade rule immediately — confirm parameters "
            "with the user before calling. Args: name, condition over a metric, "
            "optional action. Call on alert/signal/watch/notify requests; never "
            "narrate a fake call."
        ),
    },
    "create_price_alert": {
        "title": "Create price alert",
        "description": (
            "Create a price alert (a notification, not an order). direction: "
            "above|below|cross (default cross); repeat=true for a persistent alert "
            "that re-arms after each crossing. To set an alert at a named level "
            "(VAL/POC/VAH/PDH/PDL), first call get_context(symbol) to read that "
            "level's price."
        ),
    },
    "update_price_alert": {
        "title": "Update price alert",
        "description": (
            "Edit an existing price alert (id, optional price, direction, note, "
            "repeat). Get the id via query('price_alerts') or get_context."
        ),
    },
    "delete_price_alert": {
        "title": "Delete price alert",
        "description": (
            "Remove a price alert by id. Get the id via query('price_alerts') or "
            "get_context."
        ),
    },
}

_TOOL_PLANNER_PROMPT = """You are a tool planner for a trading copilot.
Return JSON only: {"tools": [{"name": "<tool>", "arguments": {...}}]}

Available tools:
- get_portfolio_snapshot — arguments: {}
- get_agent_status — arguments: {}
- get_earnings_dates — arguments: {"symbol": string}
- get_context — arguments: {"symbol": string}
- query — arguments: {"resource": string, "filters": object|null}
- list_capabilities — arguments: {}
- search_memories — arguments: {"query": string, "limit": number}
- save_memory — arguments: {"content": string, "type": "directive"|"preference"|"fact"|"lesson"|"thesis", "quote": string|null, "symbols": string[]|null, "valid_until": string|null}
- forget_memory — arguments: {"id": number, "reason": string}
- start_research — arguments: {"topic": string, "symbol": string|null, "depth": "quick"|"standard"|"deep"|"exhaustive" (optional)}
- get_trade_history — arguments: {"symbol": string|null, "from_date": string|null, "to_date": string|null, "limit": number, "account_id": string|null}
- get_performance_insights — arguments: {"period": "day"|"week"|"month"|"quarter"|"year"|"all", "account_id": string|null}
- web_search — arguments: {"query": string, "limit": number, "time_range": "day"|"week"|"month"|"year"|null, "sites": string[]|null}
- get_option_quote — arguments: {"occ_symbol": string|null, "underlying": string|null, "expiration": string|null, "strike": number|null, "right": "C"|"P"|null}
- create_rule — arguments: {"spec": {"name": string, "condition": object, "action": object|null}}
- create_price_alert — arguments: {"symbol": string, "price": number, "direction": "above"|"below"|"cross"|null, "note": string|null, "repeat": boolean|null}
- update_price_alert — arguments: {"id": number, "price": number|null, "direction": "above"|"below"|"cross"|null, "note": string|null, "repeat": boolean|null}
- delete_price_alert — arguments: {"id": number}

Rules:
- Return {"tools": []} when no tool is needed (greetings, thanks, small talk).
- Do NOT call start_research for general chat, risk opinions, or the word "analyze" alone.
- Only call start_research when the user explicitly asks for a deep research report or written report.
- Use get_context FIRST for symbol prices, RVOL, session-node levels, technicals, fundamentals, earnings, decision scores, or active alerts — never web_search for our own app data.
- Use query for watchlists, strategies, screener, market regime, news, calibration, price alerts, pending proposals, or stored reports (not single-symbol snapshots).
- Use web_search for news, social buzz, trending stocks, X/Reddit chatter, or current events.
- Use get_option_quote for option bid/ask or contract pricing — never web_search for quotes.
- Use get_trade_history for specific trade lookups; get_performance_insights for win rate, patterns, or overall P&L.
- Never call get_portfolio_snapshot or get_performance_insights on greetings (hi, hello, hey).
- When the user describes an alert/signal/rule, call create_rule with a validated spec (confirm parameters first — it arms immediately).
- When the user asks to set/create an alert, call create_price_alert directly (it's safe — a notification). If they reference a level (VAL/POC/VAH/PDH/PDL/200-DMA), call get_context first to get the price.
- Use delete_price_alert / update_price_alert to remove or change an existing alert (get its id via query('price_alerts') or get_context).
- When the user says "remember this/that ..." call save_memory with quote = their exact words. Do NOT save risk/config limits (those live in Settings) or one-off requests.
- When the user says "forget that" / "that's no longer true", call forget_memory with the id (find it first via search_memories).
"""


# Appended to the planner prompt ONLY when allow_propose_order=True (the mobile copilot).
# The mobile path intercepts propose_order into a PENDING phone proposal — it never executes.
_PROPOSE_ORDER_PLANNER_ADDENDUM = """
- propose_order — arguments: {"symbol": string, "side": "buy"|"sell", "qty": number, "order_type": "market"|"limit", "limit_price": number|null, "account_id": string|null}
Rules for propose_order:
- When the user asks to BUY or SELL a stock, call propose_order. It creates a PENDING proposal the user approves on their phone (Face ID) — it does NOT execute directly.
- Long-only: a sell only reduces a held long (no shorting). Default order_type "market".
- Only on an explicit buy/sell instruction — never for opinions, analysis, or "should I".
"""


class PlannedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    tools: list[PlannedToolCall] = Field(default_factory=list)


# Includes `propose_order`. The base planner prompt omits it (so a plan_tools→execute_tool
# caller can never auto-EXECUTE a trade); it's offered only when plan_tools is called with
# allow_propose_order=True (the mobile path), which intercepts it into a PENDING phone
# proposal via create_phone_proposal — never execute_tool.
_ALLOWED_TOOLS = frozenset(TOOL_DEFINITIONS.keys())

_MULTI_ACCOUNT_NOTE = (
    "Per-position `unrealized_pl` is CUMULATIVE since entry — NOT today. "
    "`day_pl` / `day_pl_pct` (per account) and `total_day_pl` (aggregate) are "
    "TODAY's change. Alpaca = paper (tradeable); Schwab = real money, read-only "
    "(no day P&L shown)."
)


def _multi_account_context(
    snaps: list[dict[str, Any]],
    meta: dict[str, dict[str, Any]],
    day_pl_by_account: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    day_pl_map = day_pl_by_account or {}
    accounts_out: list[dict[str, Any]] = []
    total_equity = 0.0
    total_cash = 0.0
    tradeable_equity = 0.0
    total_day_pl = 0.0

    for snap in snaps:
        account_id = snap.get("account_id") or snap.get("account", {}).get("id")
        acct_meta = meta.get(str(account_id), {}) if account_id else {}
        execution_capable = bool(acct_meta.get("execution_capable"))

        account = snap.get("account", {})
        positions = snap.get("positions", [])
        equity = float(account.get("portfolio_value", 0))
        cash = float(account.get("cash", 0))
        buying_power = float(account.get("buying_power", 0))

        total_equity += equity
        total_cash += cash
        if execution_capable:
            tradeable_equity += equity

        day_pl_entry = day_pl_map.get(str(account_id)) if account_id else None
        day_pl = day_pl_entry.get("day_pl") if day_pl_entry else None
        day_pl_pct = day_pl_entry.get("day_pl_pct") if day_pl_entry else None
        if day_pl is not None:
            total_day_pl += float(day_pl)

        open_orders = snap.get("open_orders") or []
        compact_orders = [
            {
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "qty": o.get("qty"),
                "type": o.get("order_type"),
                "limit_price": o.get("limit_price"),
                "status": o.get("status"),
            }
            for o in open_orders[:20]
        ]

        accounts_out.append(
            {
                "account_id": account_id,
                "label": acct_meta.get("label") or account_id,
                "broker": acct_meta.get("broker") or snap.get("broker"),
                "tradeable": execution_capable,
                "read_only": not execution_capable,
                "equity": equity,
                "cash": cash,
                "buying_power": buying_power,
                "day_pl": day_pl,
                "day_pl_pct": day_pl_pct,
                "position_count": len(positions),
                "positions": positions[:20],
                "open_orders": compact_orders,
                "captured_at": snap.get("captured_at"),
            }
        )

    return {
        "accounts": accounts_out,
        "aggregate": {
            "total_equity": total_equity,
            "total_cash": total_cash,
            "tradeable_equity": tradeable_equity,
            "total_day_pl": total_day_pl,
            "account_count": len(accounts_out),
        },
        "note": _MULTI_ACCOUNT_NOTE,
    }


def _account_label_map(*, db_path: Path | None = None) -> dict[str, str]:
    from db.base import DEFAULT_DB_PATH

    resolved = db_path or DEFAULT_DB_PATH
    return {
        a["id"]: str(a.get("label") or a["id"])
        for a in list_accounts(db_path=resolved)
    }


def plan_tools(
    user_text: str,
    *,
    model: str | None = None,
    allow_propose_order: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    """LLM JSON planner — replaces keyword matching.

    `allow_propose_order` adds `propose_order` to the planner's menu. Default False so
    the planner can NEVER plan a trade for any caller that routes its output through
    `execute_tool` (which would EXECUTE). Only the mobile path sets it True — and that
    path intercepts `propose_order` into a PENDING phone proposal, never execute_tool.
    """
    text = user_text.strip()
    if not text:
        return []

    system_prompt = _TOOL_PLANNER_PROMPT
    if allow_propose_order:
        system_prompt += _PROPOSE_ORDER_PLANNER_ADDENDUM

    llm = get_llm(model=model)
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=text),
    ]
    try:
        raw = complete_for_extraction(llm, messages, max_tokens=400)
        parsed = parse_json_object(raw)
        if not parsed:
            logger.warning("Tool plan JSON parse failed for: %r", text[:120])
            return []
        plan = ToolPlan.model_validate(parsed)
    except Exception:
        logger.exception("Tool planning failed")
        return []

    planned: list[tuple[str, dict[str, Any]]] = []
    for item in plan.tools:
        name = item.name.strip()
        if name not in _ALLOWED_TOOLS:
            logger.warning("Ignoring unknown planned tool: %s", name)
            continue
        args = dict(item.arguments or {})
        if name == "start_research" and not args.get("symbol"):
            sym = extract_symbol(text)
            if sym:
                args["symbol"] = sym
        planned.append((name, args))
    return planned


async def execute_tool(name: str, args: dict[str, Any]) -> Any:
    from db.base import DEFAULT_DB_PATH

    if name == "get_portfolio_snapshot":
        from brokers.factory import get_broker_for_account

        snaps = get_all_latest_snapshots(db_path=DEFAULT_DB_PATH)
        if not snaps:
            return {"error": "No snapshot yet — broker poll has not run."}
        meta = {a["id"]: a for a in list_accounts(db_path=DEFAULT_DB_PATH)}

        day_pl_by_account: dict[str, dict[str, Any]] = {}
        for a in list_accounts(db_path=DEFAULT_DB_PATH):
            if str(a.get("broker", "")).lower() != "alpaca" or not a.get(
                "execution_capable"
            ):
                continue
            try:
                broker = get_broker_for_account(a["id"])
                points = await broker.aget_portfolio_history("1D")
                if points:
                    last = points[-1]
                    day_pl_by_account[a["id"]] = {
                        "day_pl": last.profit_loss,
                        "day_pl_pct": last.profit_loss_pct,
                    }
            except Exception:
                logger.exception("day P&L fetch failed for account %s", a.get("id"))

        return _multi_account_context(snaps, meta, day_pl_by_account)

    if name == "get_agent_status":
        from signals.strategy_stats import get_strategy_stats

        labels = _account_label_map(db_path=DEFAULT_DB_PATH)
        strategies = [
            {
                "id": s["id"],
                "name": s["name"],
                "enabled": s["enabled"],
                "account_label": labels.get(s["account_id"])
                if s.get("account_id")
                else None,
                "notional_usd": s["notional_usd"],
                "exit_mode": s["exit_mode"],
                "universe": s["universe"],
                "max_concurrent_positions": s["max_concurrent_positions"],
                "max_deployed_usd": s["max_deployed_usd"],
                # Validated backtest edge (None until the PC ships strategy_stats.json).
                "backtest": get_strategy_stats(s["id"]),
            }
            for s in list_strategies(db_path=DEFAULT_DB_PATH)
        ]
        managed_positions = [
            {
                "symbol": p["symbol"],
                "strategy_id": p["strategy_id"],
                "account_label": labels.get(p["account_id"], p["account_id"]),
                "exit_mode": p["exit_mode"],
                "first_target_hit": p["first_target_hit"],
                "high_water_mark": p.get("high_water_mark"),
                "entry_date": p["entry_date"],
            }
            for p in list_open_positions(db_path=DEFAULT_DB_PATH)
        ]
        pending_approvals = []
        for proposal in list_pending_proposals(db_path=DEFAULT_DB_PATH):
            full = get_proposal(proposal["id"], db_path=DEFAULT_DB_PATH) or {}
            payload = full.get("proposal") or {}
            account_id = payload.get("account_id")
            pending_approvals.append(
                {
                    "id": proposal["id"],
                    "summary": proposal.get("summary"),
                    "symbol": proposal["symbol"],
                    "side": proposal["side"],
                    "qty": proposal["qty"],
                    "account_label": labels.get(account_id, account_id)
                    if account_id
                    else None,
                    "expires_at": proposal.get("expires_at"),
                }
            )
        return {
            "strategies": strategies,
            "managed_positions": managed_positions,
            "pending_approvals": pending_approvals,
        }

    if name == "get_earnings_dates":
        symbol_raw = args.get("symbol")
        if not symbol_raw:
            return {"error": "symbol is required"}
        symbol = str(symbol_raw).upper()
        today = now_iso()[:10]
        recent = earnings_dates_for(symbol, db_path=DEFAULT_DB_PATH)[-6:]
        return {
            "symbol": symbol,
            "next_earnings": next_earnings_date(symbol, today, db_path=DEFAULT_DB_PATH),
            "recent": recent,
            "note": (
                "Source: Finnhub (free tier). Forward dates are typically confirmed "
                "only ~2-4 weeks out; absence ≠ no earnings."
            ),
        }

    if name == "get_context":
        symbol_raw = args.get("symbol")
        if not symbol_raw:
            return {"error": "symbol is required"}
        from ai.perception.context import build_symbol_context

        symbol = str(symbol_raw).upper()
        return await build_symbol_context(symbol, db_path=DEFAULT_DB_PATH)

    if name == "list_capabilities":
        from ai.perception.context import list_capabilities

        return list_capabilities()

    if name == "query":
        from ai.perception.resources import query as perception_query

        resource = args.get("resource")
        if not resource:
            return {"error": "resource is required"}
        filters = args.get("filters")
        resolved_filters = dict(filters) if isinstance(filters, dict) else None
        return await perception_query(
            str(resource),
            resolved_filters,
            db_path=DEFAULT_DB_PATH,
        )

    if name == "search_memories":
        query = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 5))
        results = get_memory_service().search(query, limit=limit)
        return {"results": results}

    if name == "save_memory":
        from ai.memories.judge import Candidate, judge_and_store
        from db.ai_tables import MEMORY_TYPES

        content = str(args.get("content", "")).strip()
        if not content:
            return {"error": "content is required"}
        memory_type = str(args.get("type", "")).strip()
        if memory_type not in MEMORY_TYPES:
            return {
                "error": f"type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
            }
        quote_raw = args.get("quote")
        quote = str(quote_raw).strip() if quote_raw else None
        symbols_raw = args.get("symbols")
        symbols = (
            [str(s).strip().upper() for s in symbols_raw if str(s).strip()]
            if isinstance(symbols_raw, list)
            else []
        )
        valid_until_raw = args.get("valid_until")
        valid_until = str(valid_until_raw).strip() if valid_until_raw else None
        # A quote keeps user_explicit semantics (judge never NOOPs it), but
        # authority is NEVER minted from a model-controlled tool argument — a
        # hallucinated quote must not create a proposal-shaping directive.
        # The founder promotes via the widget's Confirm tap (→ user_confirmed).
        if quote:
            source, authority = "user_explicit", "agent_inferred"
        else:
            source, authority = "agent_save", "agent_inferred"
        candidate = Candidate(
            memory_type=memory_type,
            content=content,
            quote=quote,
            symbols=symbols,
            source=source,
            authority=authority,
            valid_until=valid_until,
            metadata={"source": source},
        )
        stored = judge_and_store([candidate])
        if not stored:
            return {"status": "noop", "message": "Already known — nothing saved."}
        record = stored[0]
        return {
            "status": "saved",
            "id": record["id"],
            "action": record.get("action", "ADD"),
            "type": record["type"],
            "content": record["content"],
            "message": f"Saved [mem:{record['id']}].",
        }

    if name == "forget_memory":
        from db.ai_tables import get_memory, update_memory

        mem_id_raw = args.get("id")
        if mem_id_raw is None:
            return {"error": "id is required"}
        try:
            mem_id = int(mem_id_raw)
        except (TypeError, ValueError):
            return {"error": "id must be an integer"}
        existing = get_memory(mem_id, db_path=DEFAULT_DB_PATH)
        if existing is None:
            return {"error": "not found"}
        if existing.get("pinned") or existing.get("authority") == "user_authored":
            return {
                "error": (
                    f"[mem:{mem_id}] is a pinned/user-authored rule — the copilot "
                    "can't archive it. The founder can from the memory widget."
                )
            }
        reason = str(args.get("reason", "")).strip() or "forgotten via chat"
        meta = dict(existing.get("metadata") or {})
        meta["forget_reason"] = reason
        update_memory(
            mem_id,
            metadata=meta,
            status="archived",
            db_path=DEFAULT_DB_PATH,
        )
        return {
            "status": "archived",
            "id": mem_id,
            "undo": (
                f"Archived [mem:{mem_id}] — say 'undo that' or restore it from the "
                "memory widget's History view."
            ),
            "message": f"Archived [mem:{mem_id}]. This can be undone.",
        }

    if name == "start_research":
        topic = str(args.get("topic", "Market research")).strip()
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        from config.research_engine import resolve_effort

        depth_raw = args.get("depth")
        if depth_raw is None or not str(depth_raw).strip():
            depth = resolve_effort(None).name
        else:
            depth = resolve_effort(str(depth_raw).strip().lower()).name

        job_id = submit_research_job(topic=topic, symbol=sym, depth=depth)

        return {
            "job_id": job_id,
            "status": "queued",
            "topic": topic,
            "symbol": sym,
            "depth": depth,
            "message": (
                "Research job queued. Open the Research page or poll "
                f"GET /api/research/jobs/{job_id} for status."
            ),
        }

    if name == "get_trade_history":
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        from_date = args.get("from_date")
        to_date = args.get("to_date")
        limit = min(int(args.get("limit", 50)), 200)
        account_id = args.get("account_id")
        scope = resolve_perf_scope(str(account_id) if account_id else None)
        events = list_trade_events(
            symbol=sym,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            limit=limit,
            account_id=scope.ledger_account_id,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
        )
        stats = sanitize_trade_history_stats(
            trade_summary_stats(
                from_date=str(from_date) if from_date else None,
                to_date=str(to_date) if to_date else None,
                account_id=scope.ledger_account_id,
                real_only=scope.real_only,
                paper_only=scope.paper_only,
            )
        )
        result: dict[str, Any] = {
            "stats": stats,
            "events": events,
            "count": len(events),
            "symbol_filter": sym,
        }
        if scope.fallback:
            result["scope_note"] = (
                "requested account could not be resolved; "
                "showing aggregate real accounts"
            )
        return result

    if name == "get_performance_insights":
        period = str(args.get("period", "year")).strip().lower()
        if period not in ("day", "week", "month", "quarter", "year", "all"):
            period = "year"
        account_id = args.get("account_id")
        scope = resolve_perf_scope(str(account_id) if account_id else None)
        ctx = ai_performance_context(
            account_id=scope.ledger_account_id,
            real_only=scope.real_only,
            paper_only=scope.paper_only,
            period=period,
            include_trade_context=True,
        )
        formatted = format_perf_insights_for_llm(ctx)
        if scope.fallback:
            formatted["scope_note"] = (
                "requested account could not be resolved; "
                "showing aggregate real accounts"
            )
        return formatted

    if name == "web_search":
        from ai.research.sources import (
            WEB_SEARCH_LIMITATIONS,
            _build_web_query,
            web_search,
            web_search_capabilities,
            web_search_status,
        )

        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}
        limit = min(max(int(args.get("limit", 5)), 1), 10)
        time_range = args.get("time_range")
        tr = str(time_range).strip().lower() if time_range else None
        if tr not in ("day", "week", "month", "year"):
            tr = None
        sites_raw = args.get("sites")
        sites: list[str] | None = None
        if isinstance(sites_raw, list):
            sites = [str(s).strip() for s in sites_raw if str(s).strip()]

        status = web_search_status()
        if status.get("uses_mock_web"):
            return {
                "error": "searxng_not_configured",
                "message": (
                    "SearXNG is not configured. Set SEARXNG_URL in .env or "
                    "Settings → Research."
                ),
                "query": query,
                "limitations": WEB_SEARCH_LIMITATIONS,
                "results": [],
            }

        hits = web_search(
            query,
            limit=limit,
            time_range=tr,
            sites=sites,
            allow_mock=False,
        )
        resolved_query = _build_web_query(query, sites)
        real = [h for h in hits if not h.get("search_failed")]
        return {
            "capabilities": web_search_capabilities(),
            "limitations": WEB_SEARCH_LIMITATIONS,
            "query": query,
            "resolved_query": resolved_query,
            "time_range": tr,
            "sites": sites,
            "results": [
                {
                    "title": hit.get("title"),
                    "url": hit.get("url"),
                    "snippet": hit.get("snippet"),
                    "published": hit.get("published"),
                    "search_failed": bool(hit.get("search_failed")),
                }
                for hit in hits
            ],
            "result_count": len(real),
        }

    if name == "get_option_quote":
        from datetime import date as date_type
        from decimal import Decimal

        from brokers.factory import get_broker
        from imports.option_symbol import OptionSymbol, parse_occ_symbol, to_occ_symbol

        occ_raw = args.get("occ_symbol")
        occ_symbol = str(occ_raw).strip().upper() if occ_raw else None

        if not occ_symbol:
            underlying = args.get("underlying")
            expiration = args.get("expiration")
            strike_raw = args.get("strike")
            right_raw = args.get("right")
            if not underlying or not expiration or strike_raw is None or not right_raw:
                return {
                    "error": (
                        "Provide occ_symbol or underlying, expiration (YYYY-MM-DD), "
                        "strike, and right (C|P)"
                    ),
                }
            try:
                exp = date_type.fromisoformat(str(expiration).strip())
            except ValueError:
                return {"error": "expiration must be YYYY-MM-DD"}
            try:
                strike = Decimal(str(strike_raw))
            except Exception:
                return {"error": "strike must be a number"}
            right = str(right_raw).strip().upper()
            if right not in ("C", "P"):
                return {"error": "right must be C or P"}
            opt = OptionSymbol(
                underlying=str(underlying).strip().upper(),
                expiration=exp,
                strike=strike,
                right=right,
            )
            occ_symbol = to_occ_symbol(opt)
        elif parse_occ_symbol(occ_symbol) is None:
            return {"error": "occ_symbol is not a valid OCC option symbol"}

        broker = get_broker()
        fetch = getattr(broker, "aget_option_quote", None)
        if not callable(fetch):
            return {
                "status": "no_quote",
                "message": (
                    "No live options data for this contract (set your limit from "
                    "your broker's option chain)."
                ),
            }

        quote = await fetch(occ_symbol)
        if quote is None:
            return {
                "status": "no_quote",
                "occ_symbol": occ_symbol,
                "message": (
                    "No live options data for this contract (set your limit from "
                    "your broker's option chain)."
                ),
            }
        return {"occ_symbol": occ_symbol, **quote}

    if name == "propose_order":
        from db.base import DEFAULT_DB_PATH
        from db.paper_orders import record_paper_order_proposal
        from trading.execution_service import ExecutionService
        from trading.models import RiskRejectedError
        from trading.order_proposal import (
            build_order_proposal_from_args,
            order_intent_from_fields,
        )

        explicit_account_id = args.get("account_id")
        if explicit_account_id and not account_is_execution_capable(
            str(explicit_account_id), db_path=DEFAULT_DB_PATH
        ):
            return {
                "error": (
                    f"Account {explicit_account_id} is read-only (e.g. a real Schwab "
                    "account) — pick a paper account (STRAT1/STRAT2) to place orders."
                ),
            }

        built = build_order_proposal_from_args(args, db_path=DEFAULT_DB_PATH)
        if "error" in built:
            return built

        proposal_payload = built["proposal"]
        intent_fields = built["intent_fields"]
        session_id_str = built["session_id"]
        resolved_account_id = intent_fields.get("account_id")
        account_row = (
            get_account(str(resolved_account_id), db_path=DEFAULT_DB_PATH)
            if resolved_account_id
            else default_execution_account(db_path=DEFAULT_DB_PATH)
        )
        account_label = (
            str(account_row.get("label") or account_row["id"]) if account_row else None
        )

        recorded = record_paper_order_proposal(
            proposal=proposal_payload,
            session_id=session_id_str,
            db_path=DEFAULT_DB_PATH,
        )
        proposal_id = recorded["id"]

        intent = order_intent_from_fields(
            intent_fields,
            proposal_id=proposal_id,
            session_id=session_id_str,
            db_path=DEFAULT_DB_PATH,
        )

        try:
            result = await ExecutionService.submit(intent, db_path=DEFAULT_DB_PATH)
        except RiskRejectedError as exc:
            return {
                "status": "rejected",
                "error": exc.reason,
                "proposal_id": proposal_id,
                "proposal": proposal_payload,
            }

        message = result.dry_run_log or f"{result.status}: {result.execution_id}"
        score = None
        try:
            from signals.decision_scorer import score_symbol

            score = score_symbol(
                intent_fields["symbol"],
                side=intent_fields["side"],
                db_path=DEFAULT_DB_PATH,
            )
        except Exception:
            pass

        return {
            "status": result.status,
            "execution_id": result.execution_id,
            "execution_mode": result.execution_mode,
            "proposal_id": proposal_id,
            "proposal": proposal_payload,
            "message": message,
            "score": score,
            "account_label": account_label,
        }

    if name == "create_rule":
        import uuid

        import db.ai_tables as ai_tables
        from db.ai_tables import create_rule

        try:
            spec = RuleSpec.model_validate(args.get("spec") or args)
        except Exception as exc:
            return {"error": f"Invalid rule spec: {exc}"}
        engine = spec.to_engine_rule()
        rule_id = f"rule-{uuid.uuid4().hex[:8]}"
        try:
            rule = create_rule(
                rule_id=rule_id,
                name=engine["name"],
                conditions=engine["conditions"],
                action=engine["action"],
                enabled=bool(engine.get("enabled", True)),
                db_path=ai_tables.DEFAULT_DB_PATH,
            )
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "status": "created",
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "summary": _summarize_rule_spec(spec),
            "message": f"Armed alert: {rule['name']}",
        }

    if name == "create_price_alert":
        from db.chart_features import create_price_alert as db_create_price_alert

        symbol_raw = args.get("symbol")
        if not symbol_raw:
            return {"error": "symbol is required"}
        price_raw = args.get("price")
        if price_raw is None:
            return {"error": "price is required"}
        try:
            price_f = float(price_raw)
        except (TypeError, ValueError):
            return {"error": "price must be a number"}
        if price_f <= 0:
            return {"error": "price must be a positive number"}
        direction = str(args.get("direction", "cross")).strip().lower()
        if direction not in ("above", "below", "cross"):
            return {"error": "direction must be one of: above, below, cross"}
        note = str(args.get("note", ""))
        repeat = bool(args.get("repeat", False))
        return db_create_price_alert(
            symbol=str(symbol_raw),
            price=price_f,
            direction=direction,
            note=note,
            repeat=repeat,
        )

    if name == "update_price_alert":
        from db.chart_features import update_price_alert as db_update_price_alert

        alert_id_raw = args.get("id")
        if alert_id_raw is None:
            return {"error": "id is required"}
        try:
            alert_id = int(alert_id_raw)
        except (TypeError, ValueError):
            return {"error": "id must be an integer"}
        price_f: float | None = None
        if args.get("price") is not None:
            try:
                price_f = float(args["price"])
            except (TypeError, ValueError):
                return {"error": "price must be a number"}
            if price_f <= 0:
                return {"error": "price must be a positive number"}
        direction: str | None = None
        if args.get("direction") is not None:
            direction = str(args["direction"]).strip().lower()
            if direction not in ("above", "below", "cross"):
                return {"error": "direction must be one of: above, below, cross"}
        note = None if args.get("note") is None else str(args["note"])
        repeat = None if args.get("repeat") is None else bool(args["repeat"])
        updated = db_update_price_alert(
            alert_id,
            price=price_f,
            direction=direction,
            note=note,
            repeat=repeat,
        )
        if updated is None:
            return {"error": "not found"}
        return updated

    if name == "delete_price_alert":
        from db.chart_features import delete_price_alert as db_delete_price_alert

        alert_id_raw = args.get("id")
        if alert_id_raw is None:
            return {"error": "id is required"}
        try:
            alert_id = int(alert_id_raw)
        except (TypeError, ValueError):
            return {"error": "id must be an integer"}
        if not db_delete_price_alert(alert_id):
            return {"error": "not found"}
        return {"ok": True}

    return {"error": f"Unknown tool {name}"}


_OPERATOR_SYMBOLS = {
    "lessThan": "<",
    "lessThanInclusive": "≤",
    "greaterThan": ">",
    "greaterThanInclusive": "≥",
    "equal": "==",
    "notEqual": "!=",
    "in": "in",
    "contains": "contains",
}


def _summarize_leaf(leaf: Leaf) -> str:
    from signals.rule_schema import METRIC_REGISTRY

    entry = METRIC_REGISTRY.get(leaf.metric, {})
    prefix = f"{leaf.symbol} " if leaf.symbol else ""
    if entry.get("value_type") == "bool":
        return f"{prefix}{leaf.metric}"
    op = _OPERATOR_SYMBOLS.get(leaf.operator.value, leaf.operator.value)
    return f"{prefix}{leaf.metric} {op} {leaf.value}"


def _summarize_rule_spec(spec: RuleSpec) -> str:
    def walk(condition: Leaf | Group) -> str:
        if isinstance(condition, Leaf):
            return _summarize_leaf(condition)
        if condition.all is not None:
            return " AND ".join(walk(child) for child in condition.all)
        if condition.any is not None:
            return " OR ".join(walk(child) for child in condition.any)
        if condition.not_ is not None:
            return f"NOT ({walk(condition.not_)})"
        return "unknown condition"

    cond_text = walk(spec.condition)
    action_type = spec.action.type or "alert"
    return f"{cond_text} → {action_type}"
