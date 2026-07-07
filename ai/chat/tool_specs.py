"""Provider-agnostic JSON-Schema parameters for the chat tool registry.

``TOOL_DEFINITIONS`` (``ai/chat/tools.py``) carries each tool's title +
description but no parameter schema — the schemas lived only in the TS
``frontend/lib/chat-tools.ts`` Zod defs and in the mobile planner's prose. The
one Python loop calls providers' native tool-calling (``complete_tools``), which
needs a real JSON-Schema per tool, so this module is the single Python source of
those schemas. It mirrors the Zod shapes in ``chat-tools.ts`` field-for-field —
keep the two in sync until the TS tool path is removed (a later round).

``chat_tool_specs`` fuses these schemas with the descriptions from
``TOOL_DEFINITIONS`` (descriptions stay DRY, defined once in ``tools.py``).
"""

from __future__ import annotations

from ai.chat.tools import TOOL_DEFINITIONS
from ai.llm.base import ToolSpec

# Tools that never auto-execute: they surface an Approve/Reject card and only run
# on the human's approval. The loop must NOT execute these itself — the surface
# owns the approval handshake (web: AI-SDK HITL round-trip; mobile: create a
# PENDING phone proposal for the biometric flow). Mirrors ``needsApproval: true``
# in ``frontend/lib/chat-tools.ts``.
APPROVAL_TOOLS = frozenset({"propose_order", "create_rule"})

_EMPTY: dict = {"type": "object", "properties": {}}

# JSON-Schema parameters per tool, mirroring the Zod inputSchema in chat-tools.ts.
_PARAMETERS: dict[str, dict] = {
    "get_portfolio_snapshot": _EMPTY,
    "get_agent_status": _EMPTY,
    "list_capabilities": _EMPTY,
    "get_earnings_dates": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
    "get_context": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    },
    "query": {
        "type": "object",
        "properties": {
            "resource": {"type": "string"},
            "filters": {"type": "object"},
        },
        "required": ["resource"],
    },
    "search_memories": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
    "save_memory": {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "type": {
                "type": "string",
                "enum": ["directive", "preference", "fact", "lesson", "thesis"],
            },
            "quote": {"type": "string"},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "valid_until": {"type": "string"},
        },
        "required": ["content", "type"],
    },
    "forget_memory": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["id"],
    },
    "start_research": {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "symbol": {"type": "string"},
            "depth": {
                "type": "string",
                "enum": ["quick", "standard", "deep"],
            },
        },
        "required": ["topic"],
    },
    "get_trade_history": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "from_date": {"type": "string"},
            "to_date": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "account_id": {"type": "string"},
        },
    },
    "get_performance_insights": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["day", "week", "month", "quarter", "year", "all"],
            },
            "account_id": {"type": "string"},
        },
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
            },
            "sites": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query"],
    },
    "get_option_quote": {
        "type": "object",
        "properties": {
            "occ_symbol": {"type": "string"},
            "underlying": {"type": "string"},
            "expiration": {"type": "string"},
            "strike": {"type": "number"},
            "right": {"type": "string", "enum": ["C", "P"]},
        },
    },
    "propose_order": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "qty": {"type": "number", "exclusiveMinimum": 0},
            "order_type": {"type": "string", "enum": ["market", "limit"]},
            "limit_price": {"type": "number", "exclusiveMinimum": 0},
            "account_id": {"type": "string"},
            "option_underlying": {"type": "string"},
            "option_expiration": {"type": "string"},
            "option_strike": {"type": "number", "exclusiveMinimum": 0},
            "option_right": {"type": "string", "enum": ["C", "P"]},
        },
        "required": ["symbol", "side", "qty"],
    },
    "create_rule": {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "condition": {"type": "object"},
                    "action": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "message": {"type": "string"},
                            "trigger_on": {
                                "type": "string",
                                "enum": ["always", "transition"],
                            },
                            "cooldown_seconds": {"type": "integer", "minimum": 0},
                        },
                    },
                },
                "required": ["name", "condition"],
            }
        },
        "required": ["spec"],
    },
    "create_price_alert": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "price": {"type": "number", "exclusiveMinimum": 0},
            "direction": {"type": "string", "enum": ["above", "below", "cross"]},
            "note": {"type": "string"},
            "repeat": {"type": "boolean"},
        },
        "required": ["symbol", "price"],
    },
    "update_price_alert": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "price": {"type": "number", "exclusiveMinimum": 0},
            "direction": {"type": "string", "enum": ["above", "below", "cross"]},
            "note": {"type": "string"},
            "repeat": {"type": "boolean"},
        },
        "required": ["id"],
    },
    "delete_price_alert": {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    },
}


def chat_tool_specs(enabled: set[str] | None = None) -> list[ToolSpec]:
    """Build the provider tool specs for the loop.

    Every tool in ``TOOL_DEFINITIONS`` gets a spec (description from there, schema
    from ``_PARAMETERS``). ``enabled`` restricts to a subset — the parity of the
    web toolbar toggle (``buildChatTools(enabled)`` in chat-tools.ts). A name in
    ``enabled`` that isn't a known tool is ignored (deny-by-default).
    """
    specs: list[ToolSpec] = []
    for name, meta in TOOL_DEFINITIONS.items():
        if enabled is not None and name not in enabled:
            continue
        specs.append(
            ToolSpec(
                name=name,
                description=meta.get("description", ""),
                parameters=_PARAMETERS.get(name, _EMPTY),
            )
        )
    return specs
