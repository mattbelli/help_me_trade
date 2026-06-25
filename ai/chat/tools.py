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
            "fundamentals, earnings, the calibrated decision score, and active alerts. "
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
    "search_memories": {
        "title": "Search memories",
        "description": "Search stored preferences, lessons, and research notes.",
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
    "propose_rule": {
        "title": "Propose alert rule",
        "description": (
            "Propose a signal/alert rule (name, condition over a metric, action). "
            "Shows an Approve/Reject card; on approval the rule is armed. "
            "Call on alert/signal/watch/notify requests; never narrate a fake call."
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
- list_capabilities — arguments: {}
- search_memories — arguments: {"query": string, "limit": number}
- start_research — arguments: {"topic": string, "symbol": string|null, "depth": "quick"|"standard"|"deep"}
- get_trade_history — arguments: {"symbol": string|null, "from_date": string|null, "to_date": string|null, "limit": number, "account_id": string|null}
- get_performance_insights — arguments: {"period": "day"|"week"|"month"|"quarter"|"year"|"all", "account_id": string|null}
- web_search — arguments: {"query": string, "limit": number, "time_range": "day"|"week"|"month"|"year"|null, "sites": string[]|null}
- get_option_quote — arguments: {"occ_symbol": string|null, "underlying": string|null, "expiration": string|null, "strike": number|null, "right": "C"|"P"|null}
- propose_rule — arguments: {"spec": {"name": string, "condition": object, "action": object|null}}

Rules:
- Return {"tools": []} when no tool is needed (greetings, thanks, small talk).
- Do NOT call start_research for general chat, risk opinions, or the word "analyze" alone.
- Only call start_research when the user explicitly asks for a deep research report or written report.
- Use get_context FIRST for symbol prices, RVOL, session-node levels, technicals, fundamentals, earnings, decision scores, or active alerts — never web_search for our own app data.
- Use web_search for news, social buzz, trending stocks, X/Reddit chatter, or current events.
- Use get_option_quote for option bid/ask or contract pricing — never web_search for quotes.
- Use get_trade_history for specific trade lookups; get_performance_insights for win rate, patterns, or overall P&L.
- Never call get_portfolio_snapshot or get_performance_insights on greetings (hi, hello, hey).
- When the user describes an alert/signal/rule, call propose_rule with a validated spec.
"""


class PlannedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    tools: list[PlannedToolCall] = Field(default_factory=list)


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


def plan_tools(user_text: str, *, model: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """LLM JSON planner — replaces keyword matching."""
    text = user_text.strip()
    if not text:
        return []

    llm = get_llm(model=model)
    messages = [
        LLMMessage(role="system", content=_TOOL_PLANNER_PROMPT),
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

    if name == "search_memories":
        query = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 5))
        results = get_memory_service().search(query, limit=limit)
        return {"results": results}

    if name == "start_research":
        topic = str(args.get("topic", "Market research")).strip()
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        depth = str(args.get("depth", "standard")).strip().lower()
        if depth not in ("quick", "standard", "deep"):
            depth = "standard"

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
        resolved_account_id = str(account_id) if account_id else None
        events = list_trade_events(
            symbol=sym,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            limit=limit,
            account_id=resolved_account_id,
        )
        stats = sanitize_trade_history_stats(
            trade_summary_stats(
                from_date=str(from_date) if from_date else None,
                to_date=str(to_date) if to_date else None,
                account_id=resolved_account_id,
            )
        )
        return {
            "stats": stats,
            "events": events,
            "count": len(events),
            "symbol_filter": sym,
        }

    if name == "get_performance_insights":
        period = str(args.get("period", "year")).strip().lower()
        if period not in ("day", "week", "month", "quarter", "year", "all"):
            period = "year"
        account_id = args.get("account_id")
        ctx = ai_performance_context(
            account_id=str(account_id) if account_id else None,
            period=period,
            include_trade_context=True,
        )
        return format_perf_insights_for_llm(ctx)

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

    if name == "propose_rule":
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
