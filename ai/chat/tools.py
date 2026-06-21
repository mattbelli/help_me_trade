"""Trading copilot agent tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ai.insights import snapshot_to_context
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from ai.perf_context import format_perf_insights_for_llm, sanitize_trade_history_stats
from ai.research.jobs import submit_research_job
from ai.utils.symbols import extract_symbol
from db.analytics import ai_performance_context
from db.sqlite import get_latest_snapshot
from db.trades import list_trade_events, trade_summary_stats
from signals.rule_schema import Group, Leaf, RuleSpec

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "get_portfolio_snapshot": {
        "title": "Portfolio snapshot",
        "description": "Latest account, positions, and open orders from the broker poll.",
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
            "order_type market|limit). Options: long-only buy-to-open calls/puts, "
            "limit-only — limit_price is per-share premium (max loss = premium × 100 "
            "× contracts). Use option_underlying, option_expiration (YYYY-MM-DD), "
            "option_strike, option_right (C|P), or a Schwab-style option symbol. "
            "Shows Approve/Reject card — does not execute. "
            "Call immediately on buy/sell requests; never narrate a fake tool call."
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
- search_memories — arguments: {"query": string, "limit": number}
- start_research — arguments: {"topic": string, "symbol": string|null, "depth": "quick"|"standard"|"deep"}
- get_trade_history — arguments: {"symbol": string|null, "from_date": string|null, "to_date": string|null, "limit": number}
- get_performance_insights — arguments: {"period": "day"|"week"|"month"|"quarter"|"year"|"all", "account_id": string|null}
- web_search — arguments: {"query": string, "limit": number, "time_range": "day"|"week"|"month"|"year"|null, "sites": string[]|null}
- get_option_quote — arguments: {"occ_symbol": string|null, "underlying": string|null, "expiration": string|null, "strike": number|null, "right": "C"|"P"|null}
- propose_rule — arguments: {"spec": {"name": string, "condition": object, "action": object|null}}

Rules:
- Return {"tools": []} when no tool is needed (greetings, thanks, small talk).
- Do NOT call start_research for general chat, risk opinions, or the word "analyze" alone.
- Only call start_research when the user explicitly asks for a deep research report or written report.
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
    if name == "get_portfolio_snapshot":
        snapshot = get_latest_snapshot()
        if snapshot is None:
            return {"error": "No snapshot yet — broker poll has not run."}
        return json.loads(snapshot_to_context(snapshot))

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
        events = list_trade_events(
            symbol=sym,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            limit=limit,
        )
        stats = sanitize_trade_history_stats(
            trade_summary_stats(
                from_date=str(from_date) if from_date else None,
                to_date=str(to_date) if to_date else None,
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

        built = build_order_proposal_from_args(args, db_path=DEFAULT_DB_PATH)
        if "error" in built:
            return built

        proposal_payload = built["proposal"]
        intent_fields = built["intent_fields"]
        session_id_str = built["session_id"]

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
